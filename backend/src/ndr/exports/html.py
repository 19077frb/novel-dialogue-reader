"""单文件 HTML 导出。

- CSS 内联、图片以 data URL 内联：断网也能打开，不引用 localhost 或远程资源。
- 不输出脚本；原文与标注文本都经过转义。
"""

from __future__ import annotations

import base64
from collections.abc import Mapping

from ..domain.enums import ContentNodeType, ExportStylePreset
from .render import RenderedBlock, RenderedBook, escape, export_css


def _image_data_url(media_type: str | None, data: bytes) -> str:
    kind = media_type or "application/octet-stream"
    return f"data:{kind};base64,{base64.b64encode(data).decode('ascii')}"


def render_block(block: RenderedBlock, *, images: Mapping[str, bytes]) -> str:
    if block.node_type is ContentNodeType.IMAGE:
        resource_id = block.resource_id
        if not resource_id or resource_id not in images:
            return '<p class="meta" data-ndr-auxiliary="true">（插图缺失）</p>'
        data_url = _image_data_url(block.media_type, images[resource_id])
        alt = escape(block.alt)
        return f'<figure><img src="{data_url}" alt="{alt}"/><figcaption>{alt}</figcaption></figure>'
    if block.node_type is ContentNodeType.SEPARATOR:
        return '<hr class="separator"/>'
    if block.node_type is ContentNodeType.HEADING:
        level = 3 if (block.level or 1) >= 1 else 3
        return f'<h{level} class="node-heading">{escape(block.plain_text)}</h{level}>'
    parts: list[str] = []
    for run in block.runs:
        text = escape(run.text)
        if run.quote_id and (run.color_class or run.label):
            classes = " ".join(name for name in (run.color_class,) if name)
            label = (
                '<span class="label" data-ndr-auxiliary="true">'
                f"{escape(run.label)}</span>"
                if run.label
                else ""
            )
            span_class = f' class="{classes}"' if classes else ""
            parts.append(f"<span{span_class}>{label}{text}</span>")
        else:
            parts.append(text)
    return f'<p class="node-paragraph">{"".join(parts)}</p>'


def render_html(
    rendered: RenderedBook,
    *,
    images: Mapping[str, bytes],
    generated_at: str,
) -> str:
    """生成单文件 HTML（UTF-8、无外部依赖）。"""

    legend = "".join(
        f'<li><span class="{entry.color_class}">{escape(entry.label)}</span>'
        f" · {entry.quote_count} 句</li>"
        if entry.color_class
        else f"<li>{escape(entry.label)} · {entry.quote_count} 句</li>"
        for entry in rendered.legend
    )
    body: list[str] = [
        '<!DOCTYPE html>',
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8"/>',
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>',
        f"<title>{escape(rendered.title)}</title>",
        f"<style>{export_css(rendered)}</style>",
        "</head>",
        "<body>",
        f'<h1 class="book-title">{escape(rendered.title)}</h1>',
        (
            '<p class="meta">导出时间 '
            f"{escape(generated_at)} · 样式 {rendered.style.value}"
            + (
                f" · 可见范围截止位置 {rendered.visible_horizon_cp}"
                if rendered.visible_horizon_cp is not None
                else ""
            )
            + "（离线单文件，不依赖任何外部资源）</p>"
        ),
    ]
    if legend:
        body.append(f'<ul class="legend">{legend}</ul>')
    for warning in rendered.warnings:
        body.append(f'<p class="meta">{escape(warning)}</p>')
    for chapter in rendered.chapters:
        body.append(f'<h2 class="chapter-title">{escape(chapter.title)}</h2>')
        body.extend(render_block(block, images=images) for block in chapter.blocks)
    body.extend(["</body>", "</html>", ""])
    return "\n".join(body)


def style_label(style: ExportStylePreset) -> str:
    return {
        ExportStylePreset.COLOR_AND_LABEL: "颜色 + 编号",
        ExportStylePreset.COLOR_ONLY: "仅颜色",
        ExportStylePreset.LABEL_ONLY: "仅编号",
    }[style]
