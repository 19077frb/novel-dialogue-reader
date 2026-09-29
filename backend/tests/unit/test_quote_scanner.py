"""单元测试：引号扫描与 Gap。

门槛：扫描器**只提出候选**——不分配说话人、不按轮流推断、不做人名匹配；
异常引号既不会吞章，也不会被静默忽略（都有警告）。
"""

from __future__ import annotations

import dataclasses

import pytest

from ndr.domain.enums import GapDecision, QuoteKind
from ndr.quotes.gaps import build_gaps
from ndr.quotes.ids import gap_id_for, quote_id_for
from ndr.quotes.normalization import detect_auto_close_suggestions
from ndr.quotes.scanner import (
    AUTO_CLOSE_REPLACEMENT,
    SCANNER_VERSION,
    AutoClosePoint,
    ScanLimits,
    scan_quotes,
)

VERSION = "book-version-1"


def _scan(text: str, **kwargs) -> object:  # noqa: ANN003
    return scan_quotes(text, book_version_id=VERSION, **kwargs)


def _codes(result) -> list[str]:  # noqa: ANN001
    return [warning.code for warning in result.warnings]


def test_simple_quotes_are_candidates_with_positions() -> None:
    text = "少年说：「雨停了。」然后沉默。"
    result = _scan(text)

    assert len(result.quotes) == 1
    quote = result.quotes[0]
    assert text[quote.start_cp : quote.end_cp] == "「雨停了。」"
    assert quote.inner_start_cp == quote.start_cp + 1
    assert quote.inner_end_cp == quote.end_cp - 1
    assert text[quote.inner_start_cp : quote.inner_end_cp] == "雨停了。"
    assert quote.delimiter == "corner_bracket"
    assert quote.nesting_depth == 0
    assert quote.parent_quote_id is None
    assert quote.kind_hint is None  # 是否对白/心声由后续判定，扫描器不猜
    assert result.warnings == ()


def test_nested_quotes_record_depth_and_parent() -> None:
    text = "「他当时说的是『明天见』，我记得很清楚。」"
    result = _scan(text)

    assert len(result.quotes) == 2
    outer, inner = result.quotes
    assert outer.nesting_depth == 0 and outer.parent_quote_id is None
    assert inner.nesting_depth == 1
    assert inner.parent_quote_id == outer.quote_id
    assert inner.delimiter == "corner_bracket_double"
    assert inner.kind_hint is QuoteKind.QUOTATION  # 嵌套里的『』按引用处理
    assert text[inner.start_cp : inner.end_cp] == "『明天见』"


def test_curly_and_angle_delimiters_are_recognised() -> None:
    text = "他念道：“春眠不觉晓。”又提到《雨夜》这本书。"
    result = _scan(text)

    by_delimiter = {quote.delimiter: quote for quote in result.quotes}
    assert set(by_delimiter) == {"curly_double", "angle_double"}
    assert by_delimiter["angle_double"].kind_hint is QuoteKind.QUOTATION
    assert by_delimiter["curly_double"].kind_hint is None
    assert text[by_delimiter["angle_double"].start_cp : by_delimiter["angle_double"].end_cp] == "《雨夜》"


def test_parentheses_get_other_hint() -> None:
    text = "他停下来（大约三秒）才继续。"
    result = _scan(text)
    assert len(result.quotes) == 1
    assert result.quotes[0].kind_hint is QuoteKind.OTHER
    assert result.quotes[0].delimiter == "paren_fullwidth"


def test_scanned_quote_exposes_no_speaker_fields() -> None:
    """扫描器不得携带任何说话人/归属字段，避免“假装完成了识别”。"""

    fields = {field.name for field in dataclasses.fields(__import__("ndr.quotes.scanner", fromlist=["ScannedQuote"]).ScannedQuote)}
    forbidden = {"speaker", "speaker_id", "speaker_ref", "assignment", "group_id", "label"}
    assert fields & forbidden == set()


def test_unclosed_quote_is_warned_and_not_emitted() -> None:
    text = "少女说「雨停了。\n少年没有回答。"
    result = _scan(text)

    assert result.quotes == ()
    assert "unclosed_quote" in _codes(result)
    warning = next(item for item in result.warnings if item.code == "unclosed_quote")
    assert text[warning.position_cp] == "「"


def test_auto_close_point_repairs_paragraph_before_next_open_quote() -> None:
    text = "“确实可以买到。\n但是，我苦笑着继续说道。\n“我的话保留意见。”\n"
    newline_cp = text.index("\n")
    result = _scan(
        text,
        auto_close_points=[AutoClosePoint(opening_cp=0, close_cp=newline_cp)],
    )

    first, second = result.quotes
    assert text[first.start_cp : first.end_cp] == "“确实可以买到。"
    assert first.closing == AUTO_CLOSE_REPLACEMENT
    assert first.normalized is True
    assert first.nesting_depth == 0
    assert second.nesting_depth == 0
    assert second.normalized is False
    assert result.stats["normalized_closes"] == 1
    assert "unclosed_quote" not in _codes(result)


def test_auto_close_detector_suggests_paragraph_end_before_next_open() -> None:
    text = "“第一段没有闭合。\n“第二段。”\n叙述。\n"
    suggestions = detect_auto_close_suggestions(text)

    assert len(suggestions) == 1
    suggestion = suggestions[0]
    assert suggestion.opening_cp == 0
    assert suggestion.close_cp == text.index("\n")
    assert suggestion.normalized_text == "“第一段没有闭合。”"
    assert suggestion.normalized_text.endswith(AUTO_CLOSE_REPLACEMENT)


def test_stray_closing_quote_is_warned() -> None:
    text = "少年没有回答。」"
    result = _scan(text)

    assert result.quotes == ()
    assert "stray_close" in _codes(result)


def test_quote_within_paragraph_span_limit_is_allowed() -> None:
    text = "「雨停了，\n少年把外套递了过去。」"
    result = _scan(text)

    assert len(result.quotes) == 1
    assert result.quotes[0].nesting_depth == 0


def test_quote_spanning_too_many_paragraphs_is_dropped() -> None:
    text = "「雨停了，\n甲\n乙\n丙\n少年把外套递了过去。」"
    result = _scan(text, limits=ScanLimits(max_span_paragraphs=2))

    assert result.quotes == ()
    assert "quote_spans_too_many_paragraphs" in _codes(result)


def test_runaway_quote_is_dropped_by_length_limit() -> None:
    # 在硬上限内闭合，但超过长度上限 → 丢弃并给出 quote_too_long
    body = "文" * 150
    text = f"「{body}」"
    result = _scan(text, limits=ScanLimits(max_length_cp=100))

    assert result.quotes == ()
    assert "quote_too_long" in _codes(result)


def test_never_closing_quote_is_abandoned_by_hard_ceiling() -> None:
    # 远远超过硬上限仍未闭合 → 放弃开引号（unclosed_quote），避免吞掉整章
    body = "文" * 1000
    text = f"「{body}"
    result = _scan(text, limits=ScanLimits(max_length_cp=100))

    assert result.quotes == ()
    assert "unclosed_quote" in _codes(result)


def test_open_quote_is_abandoned_after_length_limit_and_does_not_swallow_text() -> None:
    # 一个忘记闭合的引号，后面还有正常对白：前者被放弃，后者仍要提取出来（不吞章）。
    filler = "文" * 400
    text = f"「{filler}\n「雨停了。」少年说。"
    result = _scan(text, limits=ScanLimits(max_length_cp=120))

    assert [text[q.start_cp : q.end_cp] for q in result.quotes] == ["「雨停了。」"]
    assert "unclosed_quote" in _codes(result)


def test_nesting_depth_limit_is_warned_and_ignored() -> None:
    text = "「一『二“三『四』三”二』一」"
    result = _scan(text, limits=ScanLimits(max_nesting_depth=2))

    assert "nesting_too_deep" in _codes(result)
    assert all(quote.nesting_depth <= 1 for quote in result.quotes)


def test_offsets_are_code_points_not_utf16_units() -> None:
    text = "𠮷野家的猫🐈说：「雨停了。」"
    result = _scan(text)
    quote = result.quotes[0]
    assert text[quote.start_cp : quote.end_cp] == "「雨停了。」"
    # 扩展汉字与 emoji 各占 1 个码点（而不是 UTF-16 的 2 个单元）
    assert text.index("🐈") == 5
    assert len(text) == len(text.encode("utf-16-le")) // 2 - 2


def test_ids_are_stable_for_same_version_and_scanner() -> None:
    text = "他说：「雨停了。」"
    first = _scan(text)
    second = _scan(text)
    assert [q.quote_id for q in first.quotes] == [q.quote_id for q in second.quotes]

    other = _scan(text, scanner_version="quote-scan-2")
    assert other.quotes[0].quote_id != first.quotes[0].quote_id
    assert first.quotes[0].quote_id == quote_id_for(
        VERSION, first.quotes[0].start_cp, first.quotes[0].end_cp, SCANNER_VERSION
    )


def test_stats_report_warning_counts() -> None:
    text = "少年没有回答。」「雨停了。」"
    result = _scan(text)
    assert result.stats["quotes"] == 1
    assert result.stats["stray_closes"] == 1


def test_gap_only_between_top_level_quotes() -> None:
    text = "「外面『里面』结束。」少年说。\n\n「第二句。」"
    result = _scan(text)
    gaps = build_gaps(text, result.quotes, book_version_id=VERSION, scanner_version=SCANNER_VERSION)

    assert len(gaps) == 1
    gap = gaps[0]
    assert text[gap.start_cp : gap.end_cp] == "少年说。\n\n"
    assert gap.decision is GapDecision.UNCERTAIN  # 扫描器不判断 Gap 语义
    assert gap.left_quote_id in {q.quote_id for q in result.quotes}
    assert gap.gap_id == gap_id_for(VERSION, gap.start_cp, gap.end_cp, SCANNER_VERSION)
    # 嵌套的『里面』不参与 Gap 构造
    nested = next(q for q in result.quotes if q.nesting_depth == 1)
    assert nested.quote_id not in {gap.left_quote_id, gap.right_quote_id}


def test_gap_is_skipped_when_quotes_touch() -> None:
    text = "「一」「二」"
    result = _scan(text)
    gaps = build_gaps(text, result.quotes, book_version_id=VERSION, scanner_version=SCANNER_VERSION)
    assert gaps == ()


def test_gap_can_cross_chapter_boundary() -> None:
    """解析部分：Gap 允许跨章节，不能只按章节关闭。"""

    text = "第一章 一\n「甲」\n\n第二章 二\n少年沉默了很久。\n「乙」"
    result = _scan(text)
    gaps = build_gaps(text, result.quotes, book_version_id=VERSION, scanner_version=SCANNER_VERSION)

    assert len(gaps) == 1
    narration = text[gaps[0].start_cp : gaps[0].end_cp]
    assert "第二章 二" in narration  # 跨过了章节标题
    assert gaps[0].decision is GapDecision.UNCERTAIN


def test_gap_can_span_paragraph_breaks() -> None:
    text = "「一」\n少年沉默。\n「二」"
    result = _scan(text)
    gaps = build_gaps(text, result.quotes, book_version_id=VERSION, scanner_version=SCANNER_VERSION)
    assert len(gaps) == 1
    assert "少年沉默" in text[gaps[0].start_cp : gaps[0].end_cp]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("「甲」", ["「甲」"]),
        ("“甲”", ["“甲”"]),
        ("『甲』", ["『甲』"]),  # 独立出现的『』仍可能是对白，只提取不判类型
        ("《甲》", ["《甲》"]),
    ],
)
def test_various_delimiters_are_extracted(text: str, expected: list[str]) -> None:
    result = _scan(text)
    assert [text[q.start_cp : q.end_cp] for q in result.quotes] == expected
