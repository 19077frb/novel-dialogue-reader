"""单元测试：source_map 与候选引语的定位一致性。

source_map 是“码点 ↔ 章节/节点/源文档”的唯一依据；候选引语必须能用它定位回原文，
而不是重新按字符串搜索。
"""

from __future__ import annotations

from fixtures.epub_factory import Document, EpubSpec, build_epub, minimal_spec
from ndr.ingest.epub import parse_epub
from ndr.ingest.txt import parse_txt
from ndr.quotes.scanner import scan_quotes

VERSION = "book-version-1"

TXT_SAMPLE = (
    "序章 雨夜\n"
    "\n"
    "「雨停了。」少女合上伞。\n"
    "少年没有回答，只是把外套递了过去。\n"
    "\n"
    "「……谢谢。」她低声说。\n"
    "第一章 转折\n"
    "远处传来钟声。\n"
    "「明天也来这里吧。」少年忽然说。\n"
)


def _tile(parsed) -> None:  # noqa: ANN001
    cursor = 0
    for mapping in parsed.mappings:
        assert mapping.canonical_start_cp == cursor, "映射段必须无缝覆盖 canonical 文本"
        cursor = mapping.canonical_end_cp
    assert cursor == parsed.canonical_length_cp


def test_txt_mappings_tile_canonical_text() -> None:
    _tile(parse_txt(TXT_SAMPLE.encode("utf-8")))
    _tile(parse_txt(TXT_SAMPLE.replace("\n", "\r\n").encode("utf-8")))


def test_txt_source_offsets_point_back_to_source_text() -> None:
    raw = TXT_SAMPLE.replace("\n", "\r\n").encode("utf-8")
    parsed = parse_txt(raw)
    source = raw.decode("utf-8")

    for mapping in parsed.mappings:
        canonical_slice = parsed.canonical_text[
            mapping.canonical_start_cp : mapping.canonical_end_cp
        ]
        source_slice = source[mapping.source_text_start_cp : mapping.source_text_end_cp]
        assert canonical_slice.rstrip("\n") == source_slice
        assert mapping.synthetic is True  # CRLF 被规范化，标记为 synthetic


def test_epub_mappings_carry_source_href_and_synthetic_breaks() -> None:
    parsed = parse_epub(build_epub(minimal_spec()))
    _tile(parsed)

    hrefs = {mapping.source_href for mapping in parsed.mappings}
    assert hrefs == {"OEBPS/text/b-first.xhtml", "OEBPS/text/a-second.xhtml"}
    # 块之间的换行是导入时合成的
    assert any(mapping.synthetic for mapping in parsed.mappings)


def test_node_ranges_slice_to_their_own_text() -> None:
    for parsed in (parse_txt(TXT_SAMPLE.encode("utf-8")), parse_epub(build_epub(minimal_spec()))):
        for node in parsed.nodes:
            if node.node_type.value == "image":
                continue
            assert parsed.canonical_text[node.start_cp : node.end_cp]


def test_candidate_quotes_align_with_mapping_segments() -> None:
    parsed = parse_txt(TXT_SAMPLE.encode("utf-8"))
    result = scan_quotes(parsed.canonical_text, book_version_id=VERSION)

    assert [parsed.canonical_text[q.start_cp : q.end_cp] for q in result.quotes] == [
        "「雨停了。」",
        "「……谢谢。」",
        "「明天也来这里吧。」",
    ]

    for quote in result.quotes:
        # 引语落在映射段内：可以用 mapping 反查章节与节点，不需要字符串搜索
        covering = [
            mapping
            for mapping in parsed.mappings
            if mapping.canonical_start_cp <= quote.start_cp
            and quote.end_cp <= mapping.canonical_end_cp
        ]
        assert covering, "候选引语必须落在某个映射段内"
        mapping = covering[0]
        # 注意：mapping 的范围含行尾换行，节点范围不含；按 node_id 关联（服务层也是这么做的）。
        node = next(
            (
                item
                for item in parsed.nodes
                if item.node_id == mapping.node_id
                and item.chapter_ordinal == mapping.chapter_ordinal
            ),
            None,
        )
        assert node is not None
        assert parsed.canonical_text[node.start_cp : node.end_cp].count(
            parsed.canonical_text[quote.start_cp : quote.end_cp]
        ) >= 1


def test_quote_positions_are_relative_to_node_start() -> None:
    parsed = parse_txt(TXT_SAMPLE.encode("utf-8"))
    result = scan_quotes(parsed.canonical_text, book_version_id=VERSION)

    first = result.quotes[0]
    node = next(
        node for node in parsed.nodes if node.start_cp <= first.start_cp < node.end_cp
    )
    node_text = parsed.canonical_text[node.start_cp : node.end_cp]
    offset = first.start_cp - node.start_cp
    assert node_text[offset : offset + len("「雨停了。」")] == "「雨停了。」"


def test_epub_cross_block_quote_keeps_mapping_chain() -> None:
    spec = EpubSpec(
        documents=[
            Document(
                doc_id="ch1",
                href="text/chapter.xhtml",
                body="<p>「跨块的同一句发言，</p>\n<p>被叙述分开了。」</p>",
            )
        ]
    )
    parsed = parse_epub(build_epub(spec))
    result = scan_quotes(parsed.canonical_text, book_version_id=VERSION)

    assert len(result.quotes) == 1
    quote = result.quotes[0]
    spans = [
        mapping
        for mapping in parsed.mappings
        if mapping.canonical_start_cp < quote.end_cp
        and mapping.canonical_end_cp > quote.start_cp
    ]
    assert len(spans) >= 2  # 跨了两个块 → 两条映射
    covered = "".join(
        parsed.canonical_text[mapping.canonical_start_cp : mapping.canonical_end_cp]
        for mapping in spans
    )
    assert parsed.canonical_text[quote.start_cp : quote.end_cp] in covered
