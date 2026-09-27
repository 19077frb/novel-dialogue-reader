"""TXT → 统一文档树（DEVELOPMENT.md 4.1）。

规范：

- 解码用 :mod:`ndr.ingest.encoding` 的严格候选逻辑，绝不静默丢字。
- 行尾统一成 ``\\n``；不增删内容、不改写标点，因此 canonical 全文与原始解码文本只差行尾。
- 每个 canonical 行对应一条 source_map 记录，覆盖整个 canonical 文本
  （空白行也有映射，只是没有节点）。
- 章节标题只认“可信”的行（短行 + 常见章节词），其余按正文段落处理。
- 码点一律指 canonical 全文中的 ``[start_cp, end_cp)``。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from ..domain.enums import ContentNodeType
from .document import ParsedBook, ParsedChapter, ParsedMapping, ParsedNode, ParsedTxt
from .encoding import detect_encoding

PARSER_VERSION = "txt-1"
NORMALIZATION_VERSION = "canonical-lf-1"

_LINE_RE = re.compile(r"([^\r\n]*)(\r\n|\r|\n|$)")
_MAX_HEADING_CHARS = 30
_HEADING_RE = re.compile(
    r"^(?:"
    r"第[0-9０-９一二三四五六七八九十百千万零〇两]+[章节節回卷篇部](?:[^\n]{0,20})"
    r"|序章|序言|序|楔子|引子|前言|后记|後記|尾声|尾聲|终章|終章|终幕|終幕|外传|外傳|番外"
    r")(?:[^\n]{0,20})$"
)

__all__ = [
    "NORMALIZATION_VERSION",
    "PARSER_VERSION",
    "ParsedBook",
    "ParsedChapter",
    "ParsedMapping",
    "ParsedNode",
    "ParsedTxt",
    "parse_txt",
]


@dataclass
class _Line:
    text: str
    separator: str
    canonical_start_cp: int
    canonical_end_cp: int  # 含规范化后的行尾
    canonical_text_end_cp: int  # 不含行尾
    source_start_cp: int
    source_text_end_cp: int
    synthetic: bool

    @property
    def blank(self) -> bool:
        return not self.text.strip()

    @property
    def heading(self) -> bool:
        stripped = self.text.strip()
        if not stripped or len(stripped) > _MAX_HEADING_CHARS:
            return False
        return bool(_HEADING_RE.match(stripped))


def _split_lines(text: str) -> list[_Line]:
    """按行切分，同时计算 canonical 与源文本的码点偏移。"""

    lines: list[_Line] = []
    canonical_cursor = 0
    for match in _LINE_RE.finditer(text):
        body = match.group(1)
        separator = match.group(2)
        if body == "" and separator == "":
            break  # 末尾的空匹配
        normalized_separator = "\n" if separator else ""
        canonical_start = canonical_cursor
        canonical_text_end = canonical_start + len(body)
        canonical_end = canonical_text_end + len(normalized_separator)
        lines.append(
            _Line(
                text=body,
                separator=separator,
                canonical_start_cp=canonical_start,
                canonical_end_cp=canonical_end,
                canonical_text_end_cp=canonical_text_end,
                source_start_cp=match.start(),
                source_text_end_cp=match.start() + len(body),
                synthetic=separator not in ("", "\n"),
            )
        )
        canonical_cursor = canonical_end
    return lines


def _split_chapters(
    lines: list[_Line], canonical_length_cp: int, title: str | None
) -> tuple[list[ParsedChapter], list[int | None], list[str]]:
    """按可信标题行切分章节；返回章节、每行所属章节、警告。"""

    warnings: list[str] = []
    chapter_of_line: list[int | None] = [None] * len(lines)
    chapters: list[ParsedChapter] = []

    heading_indexes = [index for index, line in enumerate(lines) if line.heading]
    if not lines:
        return chapters, chapter_of_line, warnings
    if not heading_indexes:
        warnings.append("未识别到章节标题，按单章导入。")
        chapters.append(
            ParsedChapter(ordinal=0, title=title, start_cp=0, end_cp=canonical_length_cp)
        )
        return chapters, [0] * len(lines), warnings

    first_heading = heading_indexes[0]
    if any(not lines[index].blank for index in range(first_heading)):
        chapters.append(
            ParsedChapter(
                ordinal=0,
                title=None,
                start_cp=lines[0].canonical_start_cp,
                end_cp=lines[first_heading - 1].canonical_end_cp,
            )
        )
        for index in range(first_heading):
            chapter_of_line[index] = 0

    for position, start_index in enumerate(heading_indexes):
        end_index = (
            heading_indexes[position + 1] - 1
            if position + 1 < len(heading_indexes)
            else len(lines) - 1
        )
        ordinal = len(chapters)
        chapters.append(
            ParsedChapter(
                ordinal=ordinal,
                title=lines[start_index].text.strip(),
                start_cp=lines[start_index].canonical_start_cp,
                end_cp=lines[end_index].canonical_end_cp,
            )
        )
        for index in range(start_index, end_index + 1):
            chapter_of_line[index] = ordinal

    return chapters, chapter_of_line, warnings


def parse_txt(raw: bytes, *, encoding: str | None = None, title: str | None = None) -> ParsedBook:
    """解析 TXT 字节流；返回 canonical 全文、章节、节点与 source_map。"""

    detection = detect_encoding(raw, encoding)
    lines = _split_lines(detection.text)
    canonical_text = "".join(line.text + ("\n" if line.separator else "") for line in lines)

    warnings: list[str] = list(detection.warnings)
    chapters, chapter_of_line, chapter_warnings = _split_chapters(
        lines, len(canonical_text), title
    )
    warnings.extend(chapter_warnings)

    # 节点：每个非空行一个节点；标题行标记为 heading。
    nodes: list[ParsedNode] = []
    node_id_of_line: list[str | None] = [None] * len(lines)
    per_chapter_counter: dict[int, int] = {}
    for index, line in enumerate(lines):
        if line.blank or chapter_of_line[index] is None:
            continue
        chapter_ordinal = chapter_of_line[index]
        counter = per_chapter_counter.get(chapter_ordinal, 0)
        per_chapter_counter[chapter_ordinal] = counter + 1
        node_id = f"n{counter:05d}"
        node_id_of_line[index] = node_id
        node_type = ContentNodeType.HEADING if line.heading else ContentNodeType.PARAGRAPH
        payload: dict[str, object] = {"text": line.text}
        if node_type is ContentNodeType.HEADING:
            payload["level"] = 1
        nodes.append(
            ParsedNode(
                chapter_ordinal=chapter_ordinal,
                node_id=node_id,
                node_type=node_type,
                ordinal=counter,
                start_cp=line.canonical_start_cp,
                end_cp=line.canonical_text_end_cp,
                tree_json=json.dumps(payload, ensure_ascii=False),
            )
        )

    mappings = tuple(
        ParsedMapping(
            chapter_ordinal=chapter_of_line[index],
            node_id=node_id_of_line[index],
            ordinal=index,
            canonical_start_cp=line.canonical_start_cp,
            canonical_end_cp=line.canonical_end_cp,
            source_text_start_cp=line.source_start_cp,
            source_text_end_cp=line.source_text_end_cp,
            synthetic=line.synthetic,
            source_href=None,
        )
        for index, line in enumerate(lines)
    )

    if any(line.synthetic for line in lines):
        warnings.append("原文行尾不是 LF，导入时已规范化为 LF（source_map 标记为 synthetic）。")

    canonical_bytes = canonical_text.encode("utf-8")
    return ParsedBook(
        canonical_text=canonical_text,
        canonical_sha256=hashlib.sha256(canonical_bytes).hexdigest(),
        canonical_length_cp=len(canonical_text),
        source_sha256=hashlib.sha256(raw).hexdigest(),
        format="TXT",
        parser_version=PARSER_VERSION,
        normalization_version=NORMALIZATION_VERSION,
        warnings=tuple(warnings),
        chapters=tuple(chapters),
        nodes=tuple(nodes),
        mappings=mappings,
        encoding=detection.encoding,
        encoding_confidence=detection.confidence,
        metadata={"title": title or ""},
    )
