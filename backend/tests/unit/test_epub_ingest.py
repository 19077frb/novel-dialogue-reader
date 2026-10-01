"""单元测试：EPUB 结构、节点树与安全边界。"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from fixtures.epub_factory import (
    Document,
    EpubSpec,
    build_epub,
    minimal_spec,
    pretty_printed_spec,
    ruby_and_image_spec,
)
from ndr.domain.enums import ContentNodeType
from ndr.ingest.epub import EPUB_PARSER_VERSION, EpubError, EpubLimits, _parse_xhtml, parse_epub

IMAGE_BYTES = b"\x89PNG\r\n\x1a\noriginal-fixture-bytes"


def _parse(spec: EpubSpec, **kwargs):  # noqa: ANN001, ANN202
    return parse_epub(build_epub(spec), **kwargs)


def test_spine_order_defines_reading_order() -> None:
    parsed = _parse(minimal_spec())

    # 文件名（a-second 在 b-first 之前）与 manifest 顺序都不能决定阅读顺序。
    assert [chapter.ordinal for chapter in parsed.chapters] == [0, 1]
    assert [chapter.title for chapter in parsed.chapters] == ["第一章 雨夜", "第二章 名字"]
    assert [chapter.source_href for chapter in parsed.chapters] == [
        "OEBPS/text/b-first.xhtml",
        "OEBPS/text/a-second.xhtml",
    ]
    assert parsed.canonical_text.startswith("第一章 雨夜")
    assert parsed.canonical_text.index("第二章 名字") > parsed.canonical_text.index("第一章 雨夜")
    assert parsed.format == "EPUB"
    assert parsed.parser_version == EPUB_PARSER_VERSION
    assert parsed.encoding is None  # EPUB 没有单一文件级编码


def test_metadata_and_toc_are_separate_from_spine() -> None:
    parsed = _parse(minimal_spec())
    assert parsed.metadata["title"] == "原创 EPUB 样例"
    assert [entry["title"] for entry in parsed.toc] == ["第一章 雨夜", "第二章 名字"]


def test_ncx_titles_are_used_when_nav_is_absent() -> None:
    spec = EpubSpec(
        documents=[
            Document(doc_id="ch1", href="text/one.xhtml", body="<p>正文一。</p>"),
            Document(doc_id="ch2", href="text/two.xhtml", body="<p>正文二。</p>"),
        ],
        ncx=[("text/one.xhtml", "NCX 第一章"), ("text/two.xhtml", "NCX 第二章")],
    )
    parsed = _parse(spec)
    assert [chapter.title for chapter in parsed.chapters] == ["NCX 第一章", "NCX 第二章"]


def test_chapter_title_falls_back_to_first_heading() -> None:
    spec = EpubSpec(
        documents=[
            Document(doc_id="ch1", href="text/one.xhtml", body="<h1>回目 一</h1><p>正文。</p>")
        ]
    )
    parsed = _parse(spec)
    assert [chapter.title for chapter in parsed.chapters] == ["回目 一"]


def test_ruby_keeps_base_text_and_excludes_reading() -> None:
    parsed = _parse(ruby_and_image_spec(IMAGE_BYTES))

    assert "漢" in parsed.canonical_text
    assert "かん" not in parsed.canonical_text  # rt 注音不得进入正文
    assert "(" not in parsed.canonical_text  # rp 回退括号也不进入正文

    ruby_node = next(node for node in parsed.nodes if "ruby" in node.tree_json)
    payload = json.loads(ruby_node.tree_json)
    annotation = payload["ruby"][0]
    assert annotation["base"] == "漢"
    assert annotation["rt"] == "かん"
    assert parsed.canonical_text[annotation["start_cp"] : annotation["end_cp"]] == "漢"


def test_image_becomes_zero_length_node_and_registers_resource() -> None:
    parsed = _parse(ruby_and_image_spec(IMAGE_BYTES))

    image_node = next(node for node in parsed.nodes if node.node_type is ContentNodeType.IMAGE)
    assert image_node.start_cp == image_node.end_cp  # 图片不占用正文
    payload = json.loads(image_node.tree_json)
    assert payload["media_type"] == "image/png"

    resource = next(item for item in parsed.resources if item.resource_id == payload["resource_id"])
    assert resource.media_type == "image/png"
    assert resource.relative_path == "OEBPS/images/cover.png"
    assert resource.sha256 == hashlib.sha256(IMAGE_BYTES).hexdigest()
    assert resource.byte_size == len(IMAGE_BYTES)


def test_cross_block_dialogue_keeps_distinct_ranges() -> None:
    parsed = _parse(ruby_and_image_spec(IMAGE_BYTES))
    first = parsed.canonical_text.index("「跨块的同一句发言，」")
    second = parsed.canonical_text.index("「被叙述分开了。」")
    assert first < second
    first_node = next(node for node in parsed.nodes if node.start_cp <= first < node.end_cp)
    second_node = next(node for node in parsed.nodes if node.start_cp <= second < node.end_cp)
    assert first_node is not second_node
    assert parsed.canonical_text[first_node.start_cp : first_node.end_cp] == "「跨块的同一句发言，」"


def test_script_and_style_never_enter_text() -> None:
    parsed = _parse(ruby_and_image_spec(IMAGE_BYTES))
    assert "window.__evil" not in parsed.canonical_text
    assert "color: red" not in parsed.canonical_text


def test_external_image_is_not_downloaded() -> None:
    parsed = _parse(ruby_and_image_spec(IMAGE_BYTES))
    assert any("外链图片" in warning for warning in parsed.warnings)
    assert all("remote.png" not in resource.relative_path for resource in parsed.resources)


def test_external_spine_document_is_rejected() -> None:
    """spine 里的包外文档无法离线阅读，直接拒绝（不下载、不静默跳过）。"""

    spec = EpubSpec(
        documents=[Document(doc_id="ch1", href="text/one.xhtml", body="<p>正文。</p>")],
        spine=["ch1", "remote"],
        external_items=[("remote", "https://evil.example/chapter.xhtml")],
    )
    with pytest.raises(EpubError) as excinfo:
        _parse(spec)
    assert excinfo.value.code == "EPUB_EXTERNAL_REFERENCE"


def test_external_manifest_resource_is_ignored_not_fetched() -> None:
    spec = EpubSpec(
        documents=[Document(doc_id="ch1", href="text/one.xhtml", body="<p>正文。</p>")],
        external_items=[("remoteimg", "https://evil.example/cover.png")],
    )
    parsed = _parse(spec)
    assert parsed.resources == ()
    assert any("包外" in warning for warning in parsed.warnings)


def test_traversal_entry_is_rejected() -> None:
    spec = EpubSpec(
        documents=[Document(doc_id="ch1", href="text/one.xhtml", body="<p>正文。</p>")],
        extras={"../escape.txt": b"nope"},
    )
    with pytest.raises(EpubError) as excinfo:
        _parse(spec)
    assert excinfo.value.code == "EPUB_UNSAFE_PATH"


def test_symlink_entry_is_rejected() -> None:
    spec = EpubSpec(
        documents=[Document(doc_id="ch1", href="text/one.xhtml", body="<p>正文。</p>")],
        symlink_entries=("OEBPS/text/link.xhtml",),
    )
    with pytest.raises(EpubError) as excinfo:
        _parse(spec)
    assert excinfo.value.code == "EPUB_SYMLINK_ENTRY"


def test_entry_count_limit_is_enforced() -> None:
    spec = EpubSpec(
        documents=[Document(doc_id="ch1", href="text/one.xhtml", body="<p>正文。</p>")],
        extras={f"extra/{index}.txt": b"x" for index in range(5)},
    )
    with pytest.raises(EpubError) as excinfo:
        _parse(spec, limits=EpubLimits(max_entries=3))
    assert excinfo.value.code == "EPUB_ENTRY_LIMIT"


def test_total_uncompressed_limit_is_enforced() -> None:
    spec = EpubSpec(
        documents=[Document(doc_id="ch1", href="text/one.xhtml", body="<p>正文。</p>")],
        resources={"images/big.png": b"x" * 4096},
    )
    with pytest.raises(EpubError) as excinfo:
        _parse(spec, limits=EpubLimits(max_total_uncompressed_bytes=1024))
    assert excinfo.value.code == "EPUB_SIZE_LIMIT"


def test_single_entry_limit_is_enforced() -> None:
    spec = EpubSpec(
        documents=[Document(doc_id="ch1", href="text/one.xhtml", body="<p>正文。</p>")],
        resources={"images/big.png": b"x" * 4096},
    )
    with pytest.raises(EpubError) as excinfo:
        _parse(spec, limits=EpubLimits(max_entry_uncompressed_bytes=1024))
    assert excinfo.value.code == "EPUB_ENTRY_TOO_LARGE"


def test_missing_container_and_opf_are_reported() -> None:
    spec = EpubSpec(
        documents=[Document(doc_id="ch1", href="text/one.xhtml", body="<p>正文。</p>")],
        include_container=False,
    )
    with pytest.raises(EpubError) as excinfo:
        _parse(spec)
    assert excinfo.value.code == "EPUB_INVALID_CONTAINER"


def test_not_a_zip_is_rejected() -> None:
    with pytest.raises(EpubError) as excinfo:
        parse_epub(b"not a zip at all")
    assert excinfo.value.code == "EPUB_NOT_A_ZIP"


def test_empty_spine_is_rejected() -> None:
    spec = EpubSpec(
        documents=[Document(doc_id="ch1", href="text/one.xhtml", body="<p>正文。</p>")],
        spine=[],
    )
    with pytest.raises(EpubError) as excinfo:
        _parse(spec)
    assert excinfo.value.code == "EPUB_EMPTY_SPINE"


def test_javascript_manifest_item_is_not_registered() -> None:
    spec = EpubSpec(
        documents=[Document(doc_id="ch1", href="text/one.xhtml", body="<p>正文。</p>")],
        resources={"scripts/app.js": b"alert(1)"},
    )
    parsed = _parse(spec)
    assert all(not resource.relative_path.endswith(".js") for resource in parsed.resources)
    assert any("脚本" in warning for warning in parsed.warnings)


def test_source_map_tiles_canonical_and_marks_synthetic() -> None:
    parsed = _parse(minimal_spec())
    cursor = 0
    for mapping in parsed.mappings:
        assert mapping.canonical_start_cp == cursor
        cursor = mapping.canonical_end_cp
        assert mapping.source_href is not None
    assert cursor == parsed.canonical_length_cp
    assert any(mapping.synthetic for mapping in parsed.mappings)


def test_pretty_printed_whitespace_is_collapsed() -> None:
    parsed = _parse(pretty_printed_spec())
    assert parsed.canonical_text == "第一段 跨行书写。\n第二段 带制表符。"
    assert "\t" not in parsed.canonical_text
    assert "  " not in parsed.canonical_text


def test_standard_html_entities_are_normalized_without_changing_literal_sections() -> None:
    body = (
        '<p title="&copy;">甲&nbsp;乙 &copy; &NotEqualTilde; '
        '&amp;nbsp; <![CDATA[&nbsp;]]></p><!-- &copy; -->'
        '<script>evil(&nbsp;)</script><style>evil</style>'
    )
    parsed = _parse(EpubSpec(documents=[Document("ch1", "text/one.xhtml", body)]))
    assert parsed.canonical_text == "甲 乙 © ≂̸ &nbsp; &nbsp;"
    assert sum("标准 HTML 字符实体" in warning for warning in parsed.warnings) == 1
    assert "evil" not in parsed.canonical_text


@pytest.mark.parametrize("body", [
    "<p>甲&nbsp;乙</div>", "<p>&notARealEntity;</p>", "<p>甲 & 乙</p>",
    "<p>甲&nbsp 乙</p>",
])
def test_entity_compatibility_does_not_recover_invalid_xml(body: str) -> None:
    with pytest.raises(EpubError) as excinfo:
        _parse(EpubSpec(documents=[Document("ch1", "text/one.xhtml", body)]))
    assert excinfo.value.code == "EPUB_INVALID_XHTML"
    assert excinfo.value.details["href"] == "OEBPS/text/one.xhtml"
    assert excinfo.value.details["reason"]


def test_standard_xml_needs_no_entity_compatibility_warning() -> None:
    parsed = _parse(minimal_spec())
    assert not any("标准 HTML 字符实体" in warning for warning in parsed.warnings)


def test_entity_repair_preserves_declared_encoding_and_does_not_fetch_dtd() -> None:
    raw = (
        '<?xml version="1.0" encoding="iso-8859-1"?>'
        '<!DOCTYPE html SYSTEM "https://invalid.example/never-fetch.dtd">'
        '<html><body><p>café&nbsp;&copy;</p></body></html>'
    ).encode("iso-8859-1")
    doc = _parse_xhtml(raw, "chapter.xhtml", lambda _: None)
    assert doc.blocks[0].text == "café ©"
    assert doc.repaired_entities


def test_custom_dtd_entities_are_not_rewritten_by_compatibility() -> None:
    raw = (
        '<!DOCTYPE html [<!ENTITY nbsp "custom">]>'
        '<html><p>&nbsp; &copy;</p></html>'
    ).encode()
    with pytest.raises(EpubError) as excinfo:
        _parse_xhtml(raw, "chapter.xhtml", lambda _: None)
    assert excinfo.value.code == "EPUB_INVALID_XHTML"


def test_long_collection_default_and_explicit_spine_limit() -> None:
    documents = [Document(f"ch{i}", f"text/{i}.xhtml", "<p>正文。</p>") for i in range(544)]
    spec = EpubSpec(documents=documents)
    assert len(_parse(spec).chapters) == 544
    with pytest.raises(EpubError) as excinfo:
        _parse(spec, limits=EpubLimits(max_spine_items=500))
    assert excinfo.value.code == "EPUB_SPINE_LIMIT"


def _illustration_spec() -> EpubSpec:
    def image(alt: str) -> str:
        return f'<p><img src="../images/a.png" alt="{alt}"/></p>'

    return EpubSpec(
        documents=[
            Document("cover", "text/cover.xhtml", image("封面")),
            Document("one", "text/one.xhtml", "<p>第一章正文。</p>"),
            Document("inside", "text/inside.xhtml", image("章内插画")),
            Document("plate", "text/plate.xhtml", image("第二章扉页")),
            Document("plate2", "text/plate2.xhtml", image("连续扉页")),
            Document("two", "text/two.xhtml", "<p>第二章正文。</p>"),
            Document("end", "text/end.xhtml", image("末尾插画")),
        ],
        nav=[("text/one.xhtml", "第一章"), ("text/plate.xhtml", "第二章")],
        resources={"images/a.png": IMAGE_BYTES},
    )


def test_illustration_pages_join_chapters_without_losing_order_or_source_maps() -> None:
    parsed = _parse(_illustration_spec())
    assert [c.title for c in parsed.chapters] == ["第一章", "第二章"]
    assert [c.source_href for c in parsed.chapters] == [
        "OEBPS/text/one.xhtml", "OEBPS/text/plate.xhtml",
    ]
    assert parsed.canonical_text == "第一章正文。\n第二章正文。"
    images = [n for n in parsed.nodes if n.node_type is ContentNodeType.IMAGE]
    assert [json.loads(n.tree_json)["alt"] for n in images] == [
        "封面", "章内插画", "第二章扉页", "连续扉页", "末尾插画",
    ]
    assert [n.chapter_ordinal for n in images] == [0, 0, 1, 1, 1]
    assert all(n.start_cp == n.end_cp for n in images)
    assert [m.source_href for m in parsed.mappings] == [
        "OEBPS/text/one.xhtml", "OEBPS/text/two.xhtml",
    ]
    assert parsed.mappings[0].canonical_start_cp == 0
    assert parsed.mappings[0].canonical_end_cp == parsed.mappings[1].canonical_start_cp
    assert parsed.mappings[-1].canonical_end_cp == parsed.canonical_length_cp
    for chapter in parsed.chapters:
        ns = [n for n in parsed.nodes if n.chapter_ordinal == chapter.ordinal]
        assert len({n.node_id for n in ns}) == len(ns)
        assert all(chapter.start_cp <= n.start_cp <= n.end_cp <= chapter.end_cp for n in ns)
    assert any("原目录及锚点" in w for w in parsed.warnings)


def test_image_only_book_keeps_all_images_as_one_readable_chapter() -> None:
    spec = _illustration_spec()
    spec.documents = [d for d in spec.documents if d.doc_id not in {"one", "two"}]
    spec.nav = None
    parsed = _parse(spec)
    assert len(parsed.chapters) == 1
    assert parsed.canonical_text == ""
    assert len(parsed.nodes) == 5


def test_unlisted_image_caption_remains_in_its_toc_chapter() -> None:
    spec = _illustration_spec()
    spec.documents[2].body += "<p>原书的说明文字。</p>"
    parsed = _parse(spec)
    assert len(parsed.chapters) == 2
    assert "原书的说明文字。" in parsed.canonical_text


def test_toc_groups_unlisted_files_and_splits_shared_document_anchors() -> None:
    spec = EpubSpec(documents=[
        Document("meta", "text/meta.xhtml", "<p>卷首文字。</p>"),
        Document("one", "text/one.xhtml", '<h1 id="start">第一章！</h1><p>甲。</p>'),
        Document("extra", "text/logo.xhtml", "<p>附属文字。</p>"),
        Document("multi", "text/multi.xhtml", '<div id="two"><h1>第二章，开始！</h1>'
                 '<p>乙。</p></div><h1 id="three">第三章！</h1><p>丙。</p>'),
    ], nav=[("text/one.xhtml#start", "第一章！"), ("text/multi.xhtml#two", "第二章，开始！"),
            ("text/multi.xhtml#three", "第三章！")])
    parsed = _parse(spec)
    assert [c.title for c in parsed.chapters] == ["卷首", "第一章！", "第二章，开始！", "第三章！"]
    texts = [parsed.canonical_text[c.start_cp:c.end_cp] for c in parsed.chapters]
    assert "附属文字。" in texts[1] and "乙。" not in texts[1]
    assert "乙。" in texts[2] and "丙。" not in texts[2]
    assert "丙。" in texts[3] and "乙。" not in texts[3]
    assert any(m.source_href == "OEBPS/text/logo.xhtml" for m in parsed.mappings)


def test_inline_anchor_split_preserves_text_and_local_source_positions() -> None:
    spec = EpubSpec(documents=[Document("one", "text/one.xhtml", '<p id="a">甲甲'
                    '<span id="b">乙乙</span>丙丙</p>')],
                    nav=[("text/one.xhtml#a", "甲章"), ("text/one.xhtml#b", "乙章")])
    parsed = _parse(spec)
    assert parsed.canonical_text == "甲甲乙乙丙丙"
    assert [(c.start_cp, c.end_cp) for c in parsed.chapters] == [(0, 2), (2, 6)]
    assert [(m.source_text_start_cp, m.source_text_end_cp) for m in parsed.mappings] == [(0, 2), (2, 6)]
    assert not any(m.synthetic for m in parsed.mappings)


def test_missing_toc_anchor_falls_back_without_dropping_text() -> None:
    spec = minimal_spec()
    spec.nav = [("text/b-first.xhtml#missing", "不存在")]
    parsed = _parse(spec)
    assert len(parsed.chapters) == 2
    assert any("目录锚点不存在" in w for w in parsed.warnings)
    assert "第一章 雨夜" in parsed.canonical_text and "第二章 名字" in parsed.canonical_text


@pytest.mark.parametrize("ncx", [False, True])
def test_nested_toc_volume_and_leaf_sharing_href_are_deduplicated(ncx: bool) -> None:
    spec = EpubSpec(documents=[Document("one", "text/one.xhtml", "<p>甲。</p>"),
                              Document("two", "text/two.xhtml", "<p>乙。</p>")])
    if ncx:
        toc = ('<ncx><navMap><navPoint><navLabel><text>第一卷</text></navLabel>'
               '<content src="text/one.xhtml"/><navPoint><navLabel><text>第一章！</text></navLabel>'
               '<content src="text/one.xhtml"/></navPoint><navPoint><navLabel><text>第二章！</text>'
               '</navLabel><content src="text/two.xhtml"/></navPoint></navPoint></navMap></ncx>')
        spec.ncx = [("text/one.xhtml", "placeholder")]
        toc_path = "OEBPS/toc.ncx"
    else:
        toc = ('<html xmlns:epub="http://www.idpf.org/2007/ops"><nav epub:type="toc"><ol><li>'
               '<a href="text/one.xhtml">第一卷</a><ol><li><a href="text/one.xhtml">第一章！</a></li>'
               '<li><a href="text/two.xhtml">第二章！</a></li></ol></li></ol></nav></html>')
        spec.nav = [("text/one.xhtml", "placeholder")]
        toc_path = "OEBPS/nav.xhtml"
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(build_epub(spec))) as source, zipfile.ZipFile(buffer, "w") as out:
        for info in source.infolist():
            out.writestr(info, toc.encode() if info.filename == toc_path else source.read(info.filename))
    parsed = parse_epub(buffer.getvalue())
    assert [c.title for c in parsed.chapters] == ["第一卷 · 第一章！", "第一卷 · 第二章！"]


def test_annotation_roundtrip_preserves_original_image_chapter_boundaries() -> None:
    spec = _illustration_spec()
    spec.extras["OEBPS/annotations.json"] = b"{}"
    parsed = _parse(spec)
    assert len(parsed.chapters) == len(spec.documents)
    assert not any("独立插画页" in w for w in parsed.warnings)


def test_unlisted_cover_in_new_volume_precedes_next_volume_text() -> None:
    spec = EpubSpec(
        documents=[
            Document("one", "vol1/text.xhtml", "<p>卷一。</p>"),
            Document("cover", "vol2/cover.xhtml", '<img src="../images/a.png"/>'),
            Document("two", "vol2/text.xhtml", "<p>卷二。</p>"),
        ], resources={"images/a.png": IMAGE_BYTES},
    )
    parsed = _parse(spec)
    assert len(parsed.chapters) == 2
    assert next(n for n in parsed.nodes if n.node_type is ContentNodeType.IMAGE).chapter_ordinal == 1
