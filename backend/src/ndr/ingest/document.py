"""统一文档树的共享数据结构（TXT 与 EPUB 输出同一契约）。

- 坐标一律是 canonical 全文中的 Unicode 码点区间 ``[start_cp, end_cp)``。
- ``ParsedBook`` 是导入层的输出，也是持久化层的输入；TXT 会填 ``encoding``，
  EPUB 则为 ``None``（EPUB 的文本编码由 XML 解析器处理）。
- 节点文本不重复存整本正文：正文写入 canonical 文件，节点只保留范围。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..domain.enums import ContentNodeType

# XHTML 里的空白折叠：连续空白（含换行/缩进）折叠成一个空格。
WHITESPACE_RE = re.compile(r"[ \t\r\n\f\v\u00a0\u3000]+")


def collapse_whitespace(text: str) -> str:
    """把 XHTML 文本节点里的排版空白折叠成单个空格并去掉首尾空白。"""

    return WHITESPACE_RE.sub(" ", text).strip()


def has_chapter_body_text(text: str, title: str | None, *, has_heading: bool) -> bool:
    """A stored chapter heading alone is not narrative content to infer from."""
    visible = collapse_whitespace(text)
    return bool(visible) and not (
        has_heading and bool(title) and visible == collapse_whitespace(title)
    )


@dataclass(frozen=True)
class ParsedChapter:
    ordinal: int
    title: str | None
    start_cp: int
    end_cp: int
    source_href: str | None = None


@dataclass(frozen=True)
class ParsedNode:
    chapter_ordinal: int
    node_id: str
    node_type: ContentNodeType
    ordinal: int
    start_cp: int
    end_cp: int
    tree_json: str


@dataclass(frozen=True)
class ParsedMapping:
    chapter_ordinal: int | None
    node_id: str | None
    ordinal: int
    canonical_start_cp: int
    canonical_end_cp: int
    source_text_start_cp: int
    source_text_end_cp: int
    synthetic: bool
    source_href: str | None = None


@dataclass(frozen=True)
class ParsedResource:
    """登记资源：图片、CSS、字体等非正文条目。"""

    resource_id: str
    media_type: str
    relative_path: str
    sha256: str
    byte_size: int


@dataclass(frozen=True)
class ParsedBook:
    canonical_text: str
    canonical_sha256: str
    canonical_length_cp: int
    source_sha256: str
    format: str  # "TXT" / "EPUB"
    parser_version: str
    normalization_version: str
    warnings: tuple[str, ...] = ()
    chapters: tuple[ParsedChapter, ...] = ()
    nodes: tuple[ParsedNode, ...] = ()
    mappings: tuple[ParsedMapping, ...] = ()
    resources: tuple[ParsedResource, ...] = ()
    encoding: str | None = None
    encoding_confidence: str | None = None
    # 目录（TOC）与正文顺序分离：``toc`` 保留原始目录项，spine 顺序见 chapters。
    toc: tuple[dict[str, str], ...] = field(default_factory=tuple)
    metadata: dict[str, str] = field(default_factory=dict)

    def node_for(self, start_cp: int, end_cp: int) -> ParsedNode | None:
        for node in self.nodes:
            if node.start_cp == start_cp and node.end_cp == end_cp:
                return node
        return None


# 兼容旧版命名。
ParsedTxt = ParsedBook
