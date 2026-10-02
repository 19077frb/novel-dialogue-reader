"""单元测试：导出渲染、文件名与内部校验。"""

from __future__ import annotations

import io
import zipfile

from ndr.domain.enums import ContentNodeType, ExportStylePreset
from ndr.exports.render import (
    EXPORT_CSS,
    RenderedBlock,
    RenderedBook,
    RenderedChapter,
    RenderedLegendEntry,
    RenderedRun,
    escape,
    export_css,
    export_filename,
    style_uses,
    usable_items,
)
from ndr.exports.validation import check_epub, check_html, normalize_text, visible_text


def _book(**overrides) -> RenderedBook:
    chapter = RenderedChapter(
        chapter_id="c1",
        ordinal=0,
        title="第一章 <测试>",
        blocks=[
            RenderedBlock(
                node_type=ContentNodeType.PARAGRAPH,
                runs=[
                    RenderedRun(text="「雨停了。」", color_class="speaker-0", label="〔S1〕"),
                    RenderedRun(text="少女说。"),
                ],
            )
        ],
    )
    defaults = {
        "book_id": "b1",
        "title": "雨夜 / 测试",
        "language": "zh",
        "style": ExportStylePreset.COLOR_AND_LABEL,
        "chapters": [chapter],
        "legend": [RenderedLegendEntry(label="S1", color_class="speaker-0", quote_count=1)],
        "warnings": [],
        "visible_horizon_cp": None,
    }
    defaults.update(overrides)
    return RenderedBook(**defaults)


def test_style_uses_covers_three_presets() -> None:
    assert style_uses(ExportStylePreset.COLOR_AND_LABEL) == (True, True)
    assert style_uses(ExportStylePreset.COLOR_ONLY) == (True, False)


def test_export_nav_preserves_volume_groups_and_leaf_order() -> None:
    from xml.etree import ElementTree as ET

    from ndr.exports.epub import build_epub
    from ndr.ingest.epub import _parse_toc_nav, parse_epub

    titles = ["第一卷 · 彩页", "第一卷 · 目录", "第二卷 · 序章", "后记"]
    chapters = [RenderedChapter(chapter_id=str(i), ordinal=i, title=title,
                blocks=[RenderedBlock(node_type=ContentNodeType.PARAGRAPH,
                                      runs=[RenderedRun(text=f"正文{i}。")])])
                for i, title in enumerate(titles)]
    raw = build_epub(_book(chapters=chapters), images={}, generated_at="test", identifier="test")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        nav = archive.read("OEBPS/nav.xhtml")
    root = ET.fromstring(nav)
    ns = {"h": "http://www.w3.org/1999/xhtml"}
    groups = root.findall(".//h:nav/h:ol/h:li", ns)
    assert [group.find("h:span", ns).text for group in groups[:2]] == ["第一卷", "第二卷"]
    assert [item["title"] for item in _parse_toc_nav(nav)] == ["彩页", "目录", "序章", "后记"]
    assert [item["volume"] for item in _parse_toc_nav(nav)] == ["第一卷", "第一卷", "第二卷", ""]
    assert [chapter.title for chapter in parse_epub(raw).chapters] == titles


def test_extended_colors_are_emitted_in_html_and_epub_without_modulo():
    from ndr.characters.colors import color_css
    from ndr.exports.epub import build_epub
    from ndr.exports.html import render_html
    from ndr.exports.render import _runs_for_node

    runs = _runs_for_node("「雨停了。」", node_start=0, node_end=7,
                          items=[{"start_cp": 0, "end_cp": 7, "quote_id": "q1",
                                  "color_index": 24, "label": "惠惠"}],
                          use_color=True, use_label=True)
    assert runs[0].color_class == "speaker-24"
    book = _book(chapters=[RenderedChapter(chapter_id="c1", ordinal=0, title="测试",
                 blocks=[RenderedBlock(node_type=ContentNodeType.PARAGRAPH, runs=runs)])])
    expected = f".speaker-24 {{ color: {color_css(24)}; }}"
    assert expected in export_css(book)
    assert expected in render_html(book, images={}, generated_at="test")
    with zipfile.ZipFile(io.BytesIO(build_epub(
        book, images={}, generated_at="test", identifier="test",
    ))) as archive:
        assert expected in archive.read("OEBPS/style.css").decode()
    assert style_uses(ExportStylePreset.LABEL_ONLY) == (False, True)


def test_usable_items_skips_unknown_withheld_and_stale() -> None:
    payload = {
        "items": [
            {"quote_id": "q1", "color_index": 0, "label": "S1"},
            {"quote_id": "q2", "color_index": 1, "label": "S2", "withheld": True, "label_unused": 1},
            {"quote_id": "q3", "color_index": 2, "label": "S3", "stale": True},
            {"quote_id": "q4", "color_index": None, "label": None},
        ]
    }
    usable = usable_items(payload)
    assert set(usable) == {"q1"}


def test_export_filename_is_safe_and_keeps_chinese() -> None:
    name = export_filename(_book(), suffix=".epub", selected=False)
    assert name.endswith(".epub")
    assert "雨夜" in name
    assert "（标注版）" in name
    assert not any(char in name for char in '<>:"/\\|?*')
    assert export_filename(_book(), suffix=".html", selected=True).endswith("（节选）.html")


def test_escape_handles_markup_characters() -> None:
    assert escape("<script>alert(1)</script> & 『引用』") == (
        "&lt;script&gt;alert(1)&lt;/script&gt; &amp; 『引用』"
    )


def test_visible_text_drops_labels_and_markup() -> None:
    document = (
        '<p class="node-paragraph">'
        '<span class="speaker-0"><span class="label">〔S1〕</span>「雨停了。」</span>少女说。</p>'
    )
    assert normalize_text(visible_text(document)) == normalize_text("「雨停了。」少女说。")
    assert "〔S1〕" in visible_text(document, drop_labels=False)


def test_check_html_reports_internal_problems() -> None:
    good = (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><title>t</title>'
        f"<style>{EXPORT_CSS}</style></head><body><p>正文 A</p></body></html>"
    )
    report = check_html(good, expected_text="正文 A")
    assert report["ok"] is True

    bad = (
        '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>'
        '<p>正文 B</p><script src="http://example.com/x.js"></script></body></html>'
    )
    report = check_html(bad, expected_text="正文 B")
    assert report["ok"] is False
    assert report["checks"]["no_scripts"] is False
    assert report["checks"]["no_external_references"] is False

    missing = check_html(good, expected_text="完全不同的文本")
    assert missing["checks"]["text_consistency"] is False


def _epub_bytes(
    *,
    mimetype_first: bool = True,
    stored: bool = True,
    body: str = "正文 C",
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        def write(name: str, data: bytes) -> None:
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
            archive.writestr(info, data)

        if not mimetype_first:
            write("META-INF/container.xml", b"<container/>")
        write("mimetype", b"application/epub+zip")
        if mimetype_first:
            write("META-INF/container.xml", b"<container/>")
        write(
            "OEBPS/text/chapter-001.xhtml",
            (f'<html><body><p class="node-paragraph">{body}</p></body></html>').encode(),
        )
        write("OEBPS/style.css", EXPORT_CSS.encode())
        write("OEBPS/content.opf", b"<package/>")
        write("OEBPS/nav.xhtml", b"<html><body/></html>")
    return buffer.getvalue()


def test_check_epub_requires_mimetype_first_and_stored() -> None:
    ok = check_epub(_epub_bytes(), expected_text="正文 C")
    assert ok["ok"] is True, ok

    compressed = check_epub(_epub_bytes(stored=False), expected_text="正文 C")
    assert compressed["checks"]["mimetype_stored"] is False

    late = check_epub(_epub_bytes(mimetype_first=False), expected_text="正文 C")
    assert late["checks"]["mimetype_first"] is False


def test_check_epub_reports_broken_zip() -> None:
    report = check_epub(b"not a zip", expected_text="")
    assert report["ok"] is False
    assert report["detail"] == "不是有效的 zip"


def test_check_epub_does_not_parse_visible_source_text_twice() -> None:
    source = "<义妹生活> 葛格&#9834;"
    escaped = "&lt;义妹生活&gt; 葛格&amp;#9834;"

    report = check_epub(_epub_bytes(body=escaped), expected_text=source)

    assert report["ok"] is True, report
    assert report["checks"]["text_consistency"] is True
