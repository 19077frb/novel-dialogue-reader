"""单元测试：EPUB 结构、节点树与安全边界。"""

from __future__ import annotations

import hashlib
import json

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
