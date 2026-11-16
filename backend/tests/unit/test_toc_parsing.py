from types import SimpleNamespace

import pytest

from fixtures.toc_epub import nested_epub
from ndr.ingest.epub import _parse_toc_nav, _parse_xhtml, parse_epub
from ndr.ingest.toc import bind_toc


@pytest.mark.parametrize("ncx", [False, True])
def test_full_ancestors_parent_body_and_inline_targets(ncx):
    parsed = parse_epub(nested_epub(ncx=ncx))
    assert parsed.canonical_text == "卷首\n父章正文。\n第一节。第二节。"
    assert [c.title for c in parsed.chapters] == [
        "作品 · 第一卷", "作品 · 第一卷 · 第一章", "作品 · 第一卷 · 第一章 · 第二节",
    ]
    assert [n["title"] for n in parsed.toc[:4]] == ["作品", "第一卷", "第一章", "第二节"]
    assert [n["parent_id"] for n in parsed.toc[:4]] == [None, "toc-0", "toc-1", "toc-2"]
    assert parsed.toc[0].get("target_cp") is None
    for node, text in zip(parsed.toc[1:4], ["卷首", "第一节。", "第二节。"], strict=True):
        assert node["target_cp"] == parsed.canonical_text.index(text)
    stored = [SimpleNamespace(id=f"c{c.ordinal}", ordinal=c.ordinal,
                              start_cp=c.start_cp, end_cp=c.end_cp) for c in parsed.chapters]
    bound = bind_toc(parsed, stored)
    assert [n["chapter_id"] for n in bound[:4]] == [None, "c0", "c1", "c2"]
    assert all(n["target_cp"] is None and n["chapter_id"] is None for n in bound[4:])


def test_aliases_retain_separate_nodes_at_the_same_boundary():
    nodes = _parse_toc_nav(b'<nav><ol><li><a href="a">A</a><ol>'
                           b'<li><a href="a">B</a></li></ol></li></ol></nav>')
    assert len(nodes) == 2
    assert nodes[1]["parent_id"] == nodes[0]["id"]


def test_ndr_paragraph_anchor_offsets_keep_whitespace_normalization():
    document = _parse_xhtml(
        b'<html><p class="node-paragraph">  hello <span> '
        b'<span id="middle"/> world</span><span data-ndr-auxiliary="true">ignored</span>'
        b'<span id="end"/></p></html>', "body.xhtml", lambda _: None,
        strip_ndr_auxiliary=True,
    )
    assert document.blocks[0].text == "hello world"
    assert document.anchors == {"middle": (0, 6), "end": (0, 11)}


def test_terminal_anchor_remains_a_valid_navigation_target():
    from fixtures.epub_factory import Document, EpubSpec, build_epub

    parsed = parse_epub(build_epub(EpubSpec(
        documents=[Document("body", "body.xhtml", '<p>body<span id="end"/></p>')],
        nav=[("body.xhtml#end", "End")],
    )))
    assert parsed.toc[0]["target_cp"] == len(parsed.canonical_text)
