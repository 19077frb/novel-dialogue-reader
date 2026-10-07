"""导出渲染：把**冻结投影**与不可变原文渲染成统一结构。

规则：

- 只读已存数据与本地资源，**不导入任何 LLM 适配器**（导出零模型调用）。
- 未处理 / `UNKNOWN` / `stale` / horizon 遮断（`withheld`）的对白保持原样：没有颜色、没有编号。
- 编号是真实文本（`〔S1〕`），灰度打印或颜色被覆盖时仍然可辨认。
- 样式预设统一在这里决定，HTML 与 EPUB 共用同一份 CSS（"样张与实际文件共享渲染逻辑"）。
"""

from __future__ import annotations

import html as html_module
import json
from dataclasses import dataclass, field
from itertools import groupby
from typing import Any

from sqlalchemy import select

from ..characters.colors import BASE_COLORS, color_css
from ..domain.enums import ContentNodeType, ExportStylePreset
from ..ingest.query import load_canonical_text
from ..intervals import SpanIndex
from ..storage.models import Book, BookVersion, Chapter, ContentNode

EXPORTER_VERSION = "exporter-5"

EXPORT_PALETTE = BASE_COLORS

EXPORT_CSS = """/* 轻小说对话辅助阅读器导出样式：不依赖外部资源，可在离线环境打开 */
body { font-family: "Noto Serif CJK SC", "Songti SC", "Microsoft YaHei", serif; line-height: 1.9;
  margin: 0 auto; max-width: 46em; padding: 1.5em; color: #1f2328; background: #ffffff; }
h1.book-title { font-size: 1.6em; margin: 0 0 0.4em; }
p.meta { color: #5c6570; font-size: 0.9em; margin: 0 0 1.2em; }
h2.chapter-title { font-size: 1.25em; margin: 1.8em 0 0.8em;
  border-bottom: 1px solid #d8dce1; padding-bottom: 0.3em; }
h3.node-heading { font-size: 1.1em; margin: 1.3em 0 0.6em; }
p.node-paragraph { margin: 0 0 0.9em; text-indent: 2em; }
hr.separator { border: none; border-top: 1px solid #d8dce1; margin: 1.2em 0; }
figure { margin: 1em auto; text-align: center; }
figure img { max-width: 100%; }
figcaption { color: #5c6570; font-size: 0.85em; }
ul.legend { list-style: none; padding: 0; display: flex; flex-wrap: wrap;
  gap: 0.6em; font-size: 0.9em; }
ul.legend li { border: 1px solid #d8dce1; border-radius: 999px; padding: 0.1em 0.6em; }
span.label { font-size: 0.75em; vertical-align: super; font-weight: 600; }
.speaker-0 { color: #2f6feb; } .speaker-1 { color: #d97706; } .speaker-2 { color: #16a34a; }
.speaker-3 { color: #dc2626; } .speaker-4 { color: #7c3aed; } .speaker-5 { color: #0891b2; }
.speaker-6 { color: #ca8a04; } .speaker-7 { color: #db2777; }
"""


@dataclass(frozen=True)
class RenderedRun:
    text: str
    color_class: str | None = None
    label: str | None = None
    quote_id: str | None = None


@dataclass(frozen=True)
class RenderedBlock:
    node_type: ContentNodeType
    runs: list[RenderedRun] = field(default_factory=list)
    level: int | None = None
    resource_id: str | None = None
    media_type: str | None = None
    alt: str = ""
    start_cp: int | None = None
    end_cp: int | None = None

    @property
    def plain_text(self) -> str:
        return "".join(run.text for run in self.runs)


@dataclass(frozen=True)
class RenderedChapter:
    chapter_id: str
    ordinal: int
    title: str
    blocks: list[RenderedBlock] = field(default_factory=list)


@dataclass(frozen=True)
class RenderedLegendEntry:
    label: str
    color_class: str | None
    quote_count: int


@dataclass(frozen=True)
class RenderedBook:
    book_id: str
    title: str
    language: str
    style: ExportStylePreset
    chapters: list[RenderedChapter] = field(default_factory=list)
    legend: list[RenderedLegendEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    visible_horizon_cp: int | None = None

    @property
    def plain_text(self) -> str:
        parts: list[str] = []
        for chapter in self.chapters:
            parts.append(chapter.title)
            parts.extend(block.plain_text for block in chapter.blocks)
        return "\n".join(parts)

    def referenced_resource_ids(self) -> list[str]:
        ids: list[str] = []
        for chapter in self.chapters:
            for block in chapter.blocks:
                if block.resource_id and block.resource_id not in ids:
                    ids.append(block.resource_id)
        return ids


def export_css(rendered: RenderedBook) -> str:
    classes = {entry.color_class for entry in rendered.legend}
    classes.update(run.color_class for chapter in rendered.chapters
                   for block in chapter.blocks for run in block.runs)
    indices = sorted({int(value.removeprefix("speaker-")) for value in classes
                      if value and value.startswith("speaker-")
                      and value.removeprefix("speaker-").isdigit()})
    light = "\n".join(f".speaker-{index} {{ color: {color_css(index)}; }}" for index in indices)
    dark = "\n".join(f".speaker-{index} {{ color: {color_css(index, dark=True)}; }}"
                     for index in indices)
    return (EXPORT_CSS + "\n" + light + "\n@media (prefers-color-scheme: dark) {\n"
            "body { color: #e6e8eb; background: #17191c; }\n"
            "p.meta { color: #a3abb5; }\n" + dark + "\n}")


def style_uses(style: ExportStylePreset) -> tuple[bool, bool]:
    """返回 ``(use_color, use_label)``：样式只影响显示，不重新识别任何对白。"""

    if style is ExportStylePreset.COLOR_ONLY:
        return True, False
    if style is ExportStylePreset.LABEL_ONLY:
        return False, True
    return True, True


def _load_payload(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def usable_items(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """从冻结投影里筛出**可以着色**的条目（未知/遮断/过期的保持原样）。"""

    usable: dict[str, dict[str, Any]] = {}
    for item in payload.get("items", []) or []:
        if not isinstance(item, dict):
            continue
        if item.get("withheld"):
            continue
        if item.get("stale"):
            continue
        if not item.get("color_index") and not item.get("label"):
            continue
        usable[str(item.get("quote_id"))] = item
    return usable


def _slices(
    items: list[dict[str, Any]], start_cp: int, end_cp: int
) -> list[tuple[int, int, dict[str, Any] | None]]:
    """按标注边界切分 `[start_cp, end_cp)`；嵌套时取最内层。"""

    relevant = [
        item for item in items if int(item["start_cp"]) < end_cp and int(item["end_cp"]) > start_cp
    ]
    if not relevant:
        return [(start_cp, end_cp, None)]
    points = {start_cp, end_cp}
    for item in relevant:
        points.add(max(start_cp, int(item["start_cp"])))
        points.add(min(end_cp, int(item["end_cp"])))
    ordered = sorted(points)
    slices: list[tuple[int, int, dict[str, Any] | None]] = []
    for left, right in zip(ordered, ordered[1:], strict=False):
        if right <= left:
            continue
        chosen: dict[str, Any] | None = None
        for item in relevant:
            if int(item["start_cp"]) > left or int(item["end_cp"]) < right:
                continue
            if chosen is None or (
                int(item["end_cp"]) - int(item["start_cp"])
                < int(chosen["end_cp"]) - int(chosen["start_cp"])
            ):
                chosen = item
        slices.append((left, right, chosen))
    return slices


def _runs_for_node(
    text: str,
    *,
    node_start: int,
    node_end: int,
    items: list[dict[str, Any]],
    use_color: bool,
    use_label: bool,
) -> list[RenderedRun]:
    runs: list[RenderedRun] = []
    for left, right, item in _slices(items, node_start, node_end):
        segment = text[left - node_start : right - node_start]
        if not segment:
            continue
        if item is None:
            runs.append(RenderedRun(text=segment))
            continue
        color_class = None
        if use_color:
            index = int(item.get("color_index") or 0)
            color_class = f"speaker-{index}"
        label = f"〔{item['label']}〕" if use_label and item.get("label") else None
        runs.append(
            RenderedRun(
                text=segment,
                color_class=color_class,
                label=label,
                quote_id=str(item.get("quote_id")),
            )
        )
    return runs


def render_book(
    session,  # noqa: ANN001 - Session
    settings,  # noqa: ANN001 - Settings
    *,
    book: Book,
    version: BookVersion,
    projection_payload: dict[str, Any],
    style: ExportStylePreset,
    chapter_ids: list[str] | None = None,
) -> RenderedBook:
    """渲染选中的章节；标注只来自 `projection_payload`（冻结投影），不读实时标注表。"""

    use_color, use_label = style_uses(style)
    canonical = load_canonical_text(settings, version)
    selected = set(chapter_ids or [])
    chapter_stmt = select(Chapter).where(Chapter.book_version_id == version.id)
    if selected:
        chapter_stmt = chapter_stmt.where(Chapter.id.in_(selected))
    chapters = list(session.scalars(chapter_stmt.order_by(Chapter.ordinal)))
    usable = usable_items(projection_payload)
    items = list(usable.values())
    item_index = SpanIndex(
        items, start=lambda item: int(item["start_cp"]), end=lambda item: int(item["end_cp"])
    )
    node_stmt = (
        select(ContentNode)
        .join(Chapter)
        .where(
            Chapter.book_version_id == version.id,
        )
        .order_by(Chapter.ordinal, ContentNode.start_cp, ContentNode.ordinal)
    )
    if selected:
        node_stmt = node_stmt.where(Chapter.id.in_(selected))
    node_stream = session.scalars(node_stmt.execution_options(yield_per=500))
    node_groups = iter(groupby(node_stream, key=lambda node: node.chapter_id))
    next_nodes = next(node_groups, None)

    rendered: list[RenderedChapter] = []
    for chapter in chapters:
        blocks: list[RenderedBlock] = []
        nodes = next_nodes[1] if next_nodes and next_nodes[0] == chapter.id else ()
        for node in nodes:
            payload = _load_payload(node.tree_json)
            if node.node_type is ContentNodeType.IMAGE:
                blocks.append(
                    RenderedBlock(
                        node_type=ContentNodeType.IMAGE,
                        resource_id=payload.get("resource_id"),
                        media_type=payload.get("media_type"),
                        alt=str(payload.get("alt") or ""),
                    )
                )
                continue
            if node.node_type is ContentNodeType.SEPARATOR:
                blocks.append(RenderedBlock(node_type=ContentNodeType.SEPARATOR))
                continue
            start = int(node.start_cp or 0)
            end = int(node.end_cp or start)
            text = canonical[start:end] if end > start else ""
            if not text:
                continue
            if node.node_type is ContentNodeType.HEADING:
                blocks.append(
                    RenderedBlock(
                        node_type=ContentNodeType.HEADING,
                        level=int(payload.get("level") or 1),
                        runs=[RenderedRun(text=text)],
                        start_cp=start,
                        end_cp=end,
                    )
                )
                continue
            blocks.append(
                RenderedBlock(
                    node_type=ContentNodeType.PARAGRAPH,
                    start_cp=start,
                    end_cp=end,
                    runs=_runs_for_node(
                        text,
                        node_start=start,
                        node_end=end,
                        items=item_index.overlapping(start, end),
                        use_color=use_color,
                        use_label=use_label,
                    ),
                )
            )
        if next_nodes and next_nodes[0] == chapter.id:
            next_nodes = next(node_groups, None)
        rendered.append(
            RenderedChapter(
                chapter_id=chapter.id,
                ordinal=chapter.ordinal,
                title=chapter.title or f"第 {chapter.ordinal + 1} 章",
                blocks=blocks,
            )
        )

    legend = [
        RenderedLegendEntry(
            label=str(row.get("label")),
            color_class=(
                f"speaker-{int(row.get('color_index') or 0)}"
                if use_color
                else None
            ),
            quote_count=int(row.get("quote_count") or 0),
        )
        for row in projection_payload.get("legend", []) or []
        if row.get("label")
    ]
    return RenderedBook(
        book_id=book.id,
        title=book.title,
        language="zh",
        style=style,
        chapters=rendered,
        legend=legend,
        warnings=list(projection_payload.get("warnings") or []),
        visible_horizon_cp=projection_payload.get("visible_horizon_cp"),
    )


def escape(text: str) -> str:
    """统一转义入口（HTML 与 EPUB 共用）。"""

    return html_module.escape(text, quote=False)


def export_filename(rendered: RenderedBook, *, suffix: str, selected: bool) -> str:
    """安全的文件名：中文可用，去掉路径分隔与文件系统保留字符。"""

    raw = f"{rendered.title}{'（节选）' if selected else '（标注版）'}"
    safe = (
        "".join(char for char in raw if char not in '<>:"/\\|?*' and ord(char) >= 32).strip()
        or "export"
    )
    return f"{safe}{suffix}"
