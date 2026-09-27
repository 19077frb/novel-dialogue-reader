"""T02 单元测试：TXT 编码、规范化与 source_map（F01 / F02 / F11）。"""

from __future__ import annotations

import pytest

from ndr.domain.enums import ContentNodeType
from ndr.ingest.encoding import DecodeFailure, detect_encoding, plausibility
from ndr.ingest.txt import parse_txt

SAMPLE = (
    "第一章 雨夜\n"
    "「雨停了。」少女合上伞。\n"
    "少年没有回答。\n"
    "\n"
    "「……谢谢。」她低声说。\n"
    "第二章 转折\n"
    "𠮷野家的猫🐈跳上窗台。\n"
    "「雨停了。」少女合上伞。\n"
)


def _canonical_lines(text: str) -> list[str]:
    return [line for line in text.split("\n") if line.strip()]


def test_parse_utf8_builds_chapters_nodes_and_map() -> None:
    parsed = parse_txt(SAMPLE.encode("utf-8"))

    assert parsed.encoding == "utf-8"
    assert parsed.canonical_text == SAMPLE
    assert parsed.canonical_length_cp == len(SAMPLE)

    assert [chapter.ordinal for chapter in parsed.chapters] == [0, 1]
    assert [chapter.title for chapter in parsed.chapters] == ["第一章 雨夜", "第二章 转折"]
    assert parsed.chapters[0].start_cp == 0
    assert parsed.chapters[-1].end_cp == len(SAMPLE)

    headings = [node for node in parsed.nodes if node.node_type is ContentNodeType.HEADING]
    assert [node.chapter_ordinal for node in headings] == [0, 1]

    paragraphs = [node for node in parsed.nodes if node.node_type is ContentNodeType.PARAGRAPH]
    assert [parsed.canonical_text[node.start_cp : node.end_cp] for node in paragraphs] == [
        "「雨停了。」少女合上伞。",
        "少年没有回答。",
        "「……谢谢。」她低声说。",
        "𠮷野家的猫🐈跳上窗台。",
        "「雨停了。」少女合上伞。",
    ]


def test_mapping_tiles_the_whole_canonical_text() -> None:
    for raw in (SAMPLE.encode("utf-8"), SAMPLE.replace("\n", "\r\n").encode("utf-8")):
        parsed = parse_txt(raw)
        cursor = 0
        for mapping in parsed.mappings:
            assert mapping.canonical_start_cp == cursor, "映射段之间不能有空洞或重叠"
            assert mapping.canonical_end_cp >= mapping.canonical_start_cp
            cursor = mapping.canonical_end_cp
        assert cursor == parsed.canonical_length_cp


def test_crlf_is_normalized_and_marked_synthetic() -> None:
    parsed = parse_txt(SAMPLE.replace("\n", "\r\n").encode("utf-8"))

    assert parsed.canonical_text == SAMPLE  # 行尾统一成 LF，内容不变
    assert all(mapping.synthetic for mapping in parsed.mappings)
    assert any("synthetic" in warning for warning in parsed.warnings)


def test_source_ranges_point_back_to_original_text() -> None:
    raw = SAMPLE.replace("\n", "\r\n").encode("utf-8")
    parsed = parse_txt(raw)
    source = raw.decode("utf-8")
    node = parsed.nodes[1]
    mapping = next(item for item in parsed.mappings if item.node_id == node.node_id)

    canonical_slice = parsed.canonical_text[mapping.canonical_start_cp : mapping.canonical_end_cp]
    source_slice = source[mapping.source_text_start_cp : mapping.source_text_end_cp]
    assert canonical_slice.rstrip("\n") == source_slice == "「雨停了。」少女合上伞。"


def test_auto_detects_gb18030() -> None:
    raw = SAMPLE.encode("gb18030")
    parsed = parse_txt(raw)
    assert parsed.encoding == "gb18030"
    assert parsed.canonical_text == SAMPLE


def test_wrong_encoding_reports_candidates_and_lossy_preview() -> None:
    raw = SAMPLE.encode("gb18030")

    with pytest.raises(DecodeFailure) as excinfo:
        parse_txt(raw, encoding="utf-8")

    failure = excinfo.value
    assert "utf-8" in str(failure)
    assert any(candidate.encoding == "gb18030" and candidate.ok for candidate in failure.candidates)
    # 预演可以有损（含替换符），但必须被明确标注，且绝不作为正文使用：正文只来自严格解码。
    assert failure.details["preview_is_lossy"] is True
    assert failure.details["preview"]
    assert failure.requested_encoding == "utf-8"
    assert all("candidates" in failure.details for _ in (0,))


def test_explicit_encoding_wins_but_warns_about_alternatives() -> None:
    raw = "雨停了。".encode("gb18030")
    result = detect_encoding(raw, "big5")

    assert result.encoding == "big5"
    assert result.text == "迾礿賸﹝"  # 同一份字节按 big5 解出的字符不同
    assert result.warnings and "gb18030" in result.warnings[0]


def test_no_replacement_character_is_ever_introduced() -> None:
    for raw in (SAMPLE.encode("utf-8"), SAMPLE.encode("gb18030")):
        parsed = parse_txt(raw)
        assert "\ufffd" not in parsed.canonical_text


def test_emoji_and_extended_cjk_use_code_point_offsets() -> None:
    parsed = parse_txt(SAMPLE.encode("utf-8"))
    ext = "𠮷"
    emoji = "🐈"
    assert ord(ext) > 0xFFFF and len(ext) == 1
    index = parsed.canonical_text.index(ext)
    assert parsed.canonical_text[index : index + 1] == ext
    node = next(node for node in parsed.nodes if node.start_cp <= index < node.end_cp)
    assert emoji in parsed.canonical_text[node.start_cp : node.end_cp]
    # 码点长度而不是 UTF-16 长度：emoji 只占 1 个码点。
    assert len(emoji) == 1


def test_repeated_dialogue_keeps_distinct_positions() -> None:
    parsed = parse_txt(SAMPLE.encode("utf-8"))
    quote = "「雨停了。」"
    first = parsed.canonical_text.index(quote)
    second = parsed.canonical_text.index(quote, first + 1)
    assert first != second
    assert parsed.canonical_text[first : first + len(quote)] == quote
    assert parsed.canonical_text[second : second + len(quote)] == quote
    first_node = next(node for node in parsed.nodes if node.start_cp <= first < node.end_cp)
    second_node = next(node for node in parsed.nodes if node.start_cp <= second < node.end_cp)
    assert first_node is not second_node


@pytest.mark.parametrize(
    ("line", "is_heading"),
    [
        ("第一章 雨夜", True),
        ("第十二章", True),
        ("第3节 集合", True),
        ("序章", True),
        ("番外 其一", True),
        ("尾声", True),
        ("她说：「第一章」很好看。", False),
        ("这是一段很长的叙述，明显超出了标题长度限制，所以不应被当作章节标题。", False),
        ("少年没有回答。", False),
    ],
)
def test_heading_detection(line: str, is_heading: bool) -> None:
    parsed = parse_txt(f"{line}\n正文。\n".encode("utf-8"))
    headings = [node for node in parsed.nodes if node.node_type is ContentNodeType.HEADING]
    assert bool(headings) is is_heading


def test_text_without_heading_becomes_single_chapter() -> None:
    parsed = parse_txt("第一段。\n第二段。\n".encode("utf-8"), title="无标题样例")
    assert len(parsed.chapters) == 1
    assert parsed.chapters[0].title == "无标题样例"
    assert any("未识别到章节标题" in warning for warning in parsed.warnings)


def test_preamble_before_first_heading_gets_own_chapter() -> None:
    text = "书名：样例\n作者：无名\n第一章 起点\n正文。\n"
    parsed = parse_txt(text.encode("utf-8"))
    assert [chapter.title for chapter in parsed.chapters] == [None, "第一章 起点"]
    assert parsed.chapters[0].start_cp == 0
    assert parsed.canonical_text[parsed.chapters[0].start_cp : parsed.chapters[0].end_cp].startswith("书名")


def test_utf8_bom_is_stripped() -> None:
    parsed = parse_txt(b"\xef\xbb\xbf" + SAMPLE.encode("utf-8"))
    assert parsed.encoding == "utf-8-sig"
    assert parsed.canonical_text.startswith("第一章")
    assert not parsed.canonical_text.startswith("\ufeff")


def test_empty_file_is_rejected() -> None:
    with pytest.raises(DecodeFailure):
        parse_txt(b"")


def test_plausibility_flags_control_characters() -> None:
    assert plausibility("正常正文。") == 1.0
    assert plausibility("a\x00b") < 1.0
