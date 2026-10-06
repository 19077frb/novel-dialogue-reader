from dataclasses import replace

import pytest

from ndr.context.budget import (
    CHAPTER_POLICY,
    DEFAULT_POLICY,
    policy_for_version,
    policy_version_for,
)
from ndr.context.full_source import FullContextError
from ndr.context.source_selection import QuoteView
from ndr.context.window_builder import WindowInputs, plan_windows
from ndr.domain.enums import ReadingMode

TEXT = "第一章\n前文。林舟说：「你好。」\n陆欣答：「再见。」\n人物身份在章末揭示。\n第二章\n下一章秘密。"


def inputs(**changes):
    quotes = []
    for index, text in enumerate(("「你好。」", "「再见。」"), 1):
        start = TEXT.index(text)
        quotes.append(QuoteView(f"q{index}", start, start + len(text)))
    return WindowInputs(
        **{
            "book_version_id": "v",
            "canonical_text": TEXT,
            "quotes": quotes,
            "source_ranges": ((0, TEXT.index("第二章")),),
            "policy": CHAPTER_POLICY,
            **changes,
        }
    )


def plan(data=None, targets=("q1", "q2")):
    return plan_windows(inputs() if data is None else data, target_quote_ids=targets)


def body(window):
    return "".join(f.text for f in window.fragments)


def test_whole_chapter_includes_prefix_suffix_and_all_intervening_original():
    result = plan()
    assert len(result.windows) == 1
    window = result.windows[0]
    assert body(window) == TEXT[: TEXT.index("第二章")]
    assert window.visible_horizon_cp == TEXT.index("第二章")
    assert not window.omitted and window.policy_version == "context-chapter-1"
    assert window.budget["context_tokens"] <= CHAPTER_POLICY.context_tokens
    assert window.budget["source_range"] == [0, TEXT.index("第二章")]
    assert [f.start_cp for f in window.fragments][1:] == [f.end_cp for f in window.fragments][:-1]


def test_legacy_policy_keys_are_unchanged_and_new_policy_is_explicit():
    assert "full_source" not in DEFAULT_POLICY.as_key()
    assert CHAPTER_POLICY.as_key()["full_source"] is True
    assert policy_for_version("context-chapter-1") == CHAPTER_POLICY
    assert policy_version_for(DEFAULT_POLICY) == "context-1"
    assert policy_for_version("unknown") == DEFAULT_POLICY


def test_different_chapters_are_never_concatenated_into_one_window():
    result = plan(inputs(source_ranges=((0, TEXT.index("陆欣")), (TEXT.index("陆欣"), len(TEXT)))))
    assert len(result.windows) == 2 and [w.target_quote_ids for w in result.windows] == [
        ("q1",),
        ("q2",),
    ]
    assert body(result.windows[0]) + body(result.windows[1]) == TEXT


def test_explicit_initial_horizon_clips_original_not_just_names():
    cutoff = TEXT.index("陆欣")
    result = plan(inputs(visible_horizon_cp=cutoff), targets=("q1",))
    assert body(result.windows[0]) == TEXT[:cutoff]
    assert result.windows[0].visible_horizon_cp == cutoff
    with pytest.raises(FullContextError, match="可见位置"):
        plan(inputs(visible_horizon_cp=cutoff))


def test_reread_can_ignore_initial_horizon_but_not_source_scope():
    result = plan(inputs(reading_mode=ReadingMode.REREAD, visible_horizon_cp=0))
    assert body(result.windows[0]) == TEXT[: TEXT.index("第二章")]
    assert result.windows[0].visible_horizon_cp is None


def test_changed_body_changes_dependency_even_with_unchanged_positions():
    data = inputs()
    altered = replace(data, canonical_text=TEXT.replace("前文", "后文"))
    assert plan(data).dependency_hash != plan(altered).dependency_hash
    assert plan(data).windows[0].window_id == plan(altered).windows[0].window_id


def test_source_range_changes_window_id_and_dependency():
    first = plan().windows[0]
    second = plan(inputs(source_ranges=((TEXT.index("前文"), TEXT.index("第二章")),))).windows[0]
    assert first.window_id != second.window_id and first.dependency_hash != second.dependency_hash


@pytest.mark.parametrize(
    "ranges",
    [
        (),
        ((-1, 10),),
        ((0, 9999),),
        ((0, 20), (10, 30)),
        ((20, 30), (0, 10)),
        ((0, 0),),
        ((False, 30),),
    ],
)
def test_invalid_or_missing_source_ranges_reject_before_sending(ranges):
    with pytest.raises(FullContextError):
        plan(inputs(source_ranges=ranges))


@pytest.mark.parametrize("targets", [("q1", "q1"), ("q1", "missing")])
def test_duplicate_or_missing_targets_are_not_silently_ignored(targets):
    with pytest.raises(FullContextError):
        plan(targets=targets)


def test_full_source_cannot_enable_gap_compression():
    with pytest.raises(FullContextError, match="压缩"):
        plan(inputs(policy=replace(CHAPTER_POLICY, gap_compression=True)))


def test_oversized_complete_unit_is_rejected_not_truncated_or_sent_anyway():
    with pytest.raises(FullContextError, match="未截断"):
        plan(inputs(policy=replace(CHAPTER_POLICY, context_tokens=5)))


def test_explicit_original_separator_is_used_only_when_whole_unit_does_not_fit():
    text = "林舟说：「你好。」\n***\n陆欣答：「再见。」\n"
    quotes = []
    for index, quoted in enumerate(("「你好。」", "「再见。」"), 1):
        start = text.index(quoted)
        quotes.append(QuoteView(f"q{index}", start, start + len(quoted)))
    data = inputs(canonical_text=text, quotes=quotes, source_ranges=((0, len(text)),))
    assert len(plan(data).windows) == 1
    small = replace(data, policy=replace(CHAPTER_POLICY, context_tokens=16))
    split = plan(small)
    assert len(split.windows) == 2
    assert "".join(body(w) for w in split.windows) == text


def test_separator_inside_a_quote_cannot_cut_the_quote():
    text = "林舟说：「长句。\n***\n仍然是同一发言。」\n"
    start = text.index("「")
    data = inputs(
        canonical_text=text,
        quotes=[QuoteView("q1", start, text.index("」") + 1)],
        source_ranges=((0, len(text)),),
        policy=replace(CHAPTER_POLICY, context_tokens=8),
    )
    with pytest.raises(FullContextError, match="未截断"):
        plan(data, targets=("q1",))
