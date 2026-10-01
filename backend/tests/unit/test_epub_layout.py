"""CSS-aware decorative layout normalization, without browser execution."""

from xml.etree import ElementTree as ET

import pytest

from fixtures.epub_factory import Document, EpubSpec, build_epub
from ndr.ingest.epub import parse_epub
from ndr.ingest.layout import LayoutNormalizer


def _letters(text: str) -> str:
    return "".join(f"<p>{letter}</p>" for letter in text)


def _parse(body: str, css: str = "", *, roundtrip: bool = False):
    spec = EpubSpec(documents=[Document("title", "text/title.xhtml", body)])
    # The fixture wraps a head; an embedded style is enough to test read-only hints.
    spec.documents[0].body = f"<style>{css}</style>{body}"
    if roundtrip:
        spec.extras["OEBPS/annotations.json"] = b"{}"
    return parse_epub(build_epub(spec))


def test_float_columns_and_multicolumn_subtitle_are_readable() -> None:
    body = (
        '<div class="design"><div class="col">' + _letters("作者甲") + "</div>"
        '<div class="col">' + _letters("illustration") + "</div>"
        '<div class="book col"><p>原创的</p><p>世界故事</p><p>卷一</p></div>'
        '<div class="subtitle"><div class="col">' + _letters("晴朗的") + "</div>"
        '<div class="col">' + _letters("新生活") + "</div></div></div>"
    )
    parsed = _parse(body, ".design > .col, .subtitle > .col {float: right}")
    assert parsed.canonical_text == "作者甲\nillustration\n原创的世界故事卷一\n晴朗的新生活"
    assert any("单字分段" in w for w in parsed.warnings)


@pytest.mark.parametrize("style", [
    "writing-mode:vertical-rl", "-epub-writing-mode:vertical-lr", "float:right",
])
def test_inline_css_recognizes_single_character_columns(style: str) -> None:
    parsed = _parse(f'<div style="{style}">{_letters("原创字")}</div>')
    assert parsed.canonical_text == "原创字"


def test_writing_mode_inheritance_and_selector_specificity() -> None:
    body = '<section id="layout"><div class="letters">' + _letters("原创字") + "</div></section>"
    parsed = _parse(body, "section {writing-mode: vertical-rl} .letters {float:none}")
    assert parsed.canonical_text == "原创字"
    parsed = _parse(body, "section {writing-mode:vertical-rl} #layout > .letters {writing-mode:horizontal-tb}")
    assert parsed.canonical_text == "原\n创\n字"


def test_inline_and_important_cascade_can_cancel_float() -> None:
    body = '<div class="letters" style="float:none">' + _letters("原创字") + "</div>"
    assert _parse(body, ".letters{float:right}").canonical_text == "原\n创\n字"
    assert _parse(body, ".letters{float:right!important}").canonical_text == "原创字"
    assert _parse(body, ".letters{float:right!important} .letters{float:none!important}").canonical_text == "原\n创\n字"


def test_plain_short_paragraphs_and_long_vertical_prose_are_not_merged() -> None:
    assert _parse('<div>' + _letters("原创字") + '</div>').canonical_text == "原\n创\n字"
    text = "这是正常正文中的完整段落，应当保留原来的分段。"
    body = '<div style="writing-mode:vertical-rl">' + f"<p>{text}</p>" * 3 + "</div>"
    assert _parse(body).canonical_text == "\n".join([text] * 3)
    body = '<div style="writing-mode:vertical-rl"><p>「好。」</p><p>「谢谢。」</p><p>「不客气。」</p></div>'
    assert _parse(body).canonical_text == "「好。」\n「谢谢。」\n「不客气。」"


def test_images_and_ruby_are_not_destroyed_or_interleaved() -> None:
    body = '<div style="float:right"><p>甲</p><img src="missing.png"/><p>乙</p><p>丙</p></div>'
    assert _parse(body).canonical_text == "甲\n乙\n丙"
    body = '<div style="writing-mode:vertical-rl"><p><ruby>甲<rt>こう</rt></ruby></p><p>乙</p><p>丙</p></div>'
    parsed = _parse(body)
    assert parsed.canonical_text == "甲乙丙"
    assert '"rt": "こう"' in parsed.nodes[0].tree_json


def test_exported_annotations_bypass_layout_normalization() -> None:
    body = '<div style="float:right">' + _letters("原创字") + '</div>'
    assert _parse(body, roundtrip=True).canonical_text == "原\n创\n字"


def test_external_stylesheet_is_ignored_without_network_access() -> None:
    root = ET.fromstring('<html><head><link rel="stylesheet" href="https://invalid.example/a.css"/></head>'
                         '<body><div>' + _letters("原创字") + '</div></body></html>')
    calls = []
    normalizer = LayoutNormalizer()
    def offline_loader(href: str) -> None:
        calls.append(href)
    assert normalizer.normalize(root, offline_loader) == 0
    assert calls == ["https://invalid.example/a.css"]


def test_registered_stylesheet_links_are_used_and_imports_are_not_followed() -> None:
    body = '<link rel="stylesheet" href="../styles/a.css"/><div class="letters">' + _letters("原创字") + '</div>'
    spec = EpubSpec(documents=[Document("title", "text/title.xhtml", body)], resources={
        "styles/a.css": b'@import url("https://invalid.example/never-fetch.css"); .letters{float:right}',
    })
    assert parse_epub(build_epub(spec)).canonical_text == "原创字"


def test_bad_css_and_oversized_stylesheets_do_not_break_text_import() -> None:
    body = '<div class="letters">' + _letters("原创字") + '</div>'
    assert _parse(body, ".letters:unsupported() {float:right} invalid;").canonical_text == "原\n创\n字"
    assert _parse(body, "/*" + "x" * (128 * 1024) + "*/ .letters {float:right}").canonical_text == "原\n创\n字"
