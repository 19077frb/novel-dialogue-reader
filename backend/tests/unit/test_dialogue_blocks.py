from dataclasses import replace

import pytest

from ndr.context.budget import (
    BOUNDED_BLOCK_POLICY,
    CHAPTER_POLICY,
    DEFAULT_POLICY,
    DIALOGUE_BLOCK_POLICY,
    BudgetItemKind,
    TokenEstimator,
    policy_for_version,
)
from ndr.context.full_source import FullContextError
from ndr.context.source_selection import ParagraphView, QuoteView
from ndr.context.window_builder import WindowInputs, plan_windows
from ndr.domain.enums import ReadingMode

TEXT = "开头。\n「甲。」\n「乙。」\n旁白。\n「丙。」\n「丁。」\n结尾。\n"


def inputs(text=TEXT, *, budget=22, mode=ReadingMode.REREAD, nodes=True, **changes):
    quotes, position = [], 0
    while (start := text.find("「", position)) != -1:
        end = text.index("」", start) + 1
        quotes.append(QuoteView(f"q{len(quotes) + 1}", start, end))
        position = end
    paragraphs, position = [], 0
    for line in text.splitlines(keepends=True):
        if line.strip():
            paragraphs.append(ParagraphView(str(position), position, position + len(line.rstrip())))
        position += len(line)
    return WindowInputs(
        **{
            "book_version_id": "v",
            "canonical_text": text,
            "quotes": quotes,
            "paragraphs": paragraphs if nodes else (),
            "reading_mode": mode,
            "source_ranges": ((0, len(text)),),
            "policy": replace(DIALOGUE_BLOCK_POLICY, context_tokens=budget),
            **changes,
        }
    )


def plan(data):
    return plan_windows(data, target_quote_ids=[q.quote_id for q in data.quotes])


def main(window):
    return [f for f in window.fragments if f.kind is not BudgetItemKind.OVERLAP]


@pytest.mark.parametrize("nodes", [True, False])
@pytest.mark.parametrize("mode", [ReadingMode.INITIAL, ReadingMode.REREAD])
def test_long_chapter_keeps_turn_blocks_and_accounts_for_boundary_source(nodes, mode):
    data = inputs(nodes=nodes, mode=mode)
    result = plan(data)
    assert len(result.windows) > 1
    assert "".join(f.text for w in result.windows for f in main(w)) == TEXT
    assert [q for w in result.windows for q in w.target_quote_ids] == ["q1", "q2", "q3", "q4"]
    assert any({"q1", "q2"} <= set(w.target_quote_ids) for w in result.windows)
    assert any({"q3", "q4"} <= set(w.target_quote_ids) for w in result.windows)
    for w in result.windows:
        assert w.budget["context_tokens"] == sum(
            data.estimator.estimate(f.text) for f in w.fragments
        )
        assert w.budget["context_tokens"] <= data.policy.context_tokens
        assert all(f.text == TEXT[f.start_cp : f.end_cp] for f in w.fragments)
        start, end = w.budget["source_range"]
        overlap = [f for f in w.fragments if f.kind is BudgetItemKind.OVERLAP]
        assert all(f.end_cp <= start or f.start_cp >= end for f in overlap)
        if mode is ReadingMode.INITIAL:
            assert all(f.end_cp <= end for f in w.fragments)
            assert w.visible_horizon_cp == end
        else:
            assert w.visible_horizon_cp is None
    assert result.windows[1].carry_from_window_id == result.windows[0].window_id


def test_fitting_chapter_stays_whole_and_v1_cache_keys_do_not_change():
    data = inputs(budget=100)
    result = plan(data)
    assert len(result.windows) == 1
    assert "".join(f.text for f in result.windows[0].fragments) == TEXT
    assert result.windows[0].policy_version == "context-chapter-2"
    assert not result.windows[0].budget["boundary_ranges"]
    assert "dialogue_blocks" not in DEFAULT_POLICY.as_key()
    assert "dialogue_blocks" not in CHAPTER_POLICY.as_key()
    assert policy_for_version("context-chapter-1") is CHAPTER_POLICY
    assert policy_for_version("context-chapter-2") is DIALOGUE_BLOCK_POLICY
    old = plan(replace(data, policy=replace(CHAPTER_POLICY, context_tokens=100)))
    assert old.windows[0].window_id != result.windows[0].window_id
    assert old.dependency_hash != result.dependency_hash


@pytest.mark.parametrize("mode", [ReadingMode.INITIAL, ReadingMode.REREAD])
def test_new_soft_target_limit_splits_only_at_complete_block_boundaries(mode):
    text = "".join("旁白。\n「甲。」\n「乙。」\n" for _ in range(8))
    data = inputs(text, budget=1000, mode=mode)
    old = plan(data)
    bounded = plan(replace(data, policy=replace(BOUNDED_BLOCK_POLICY, context_tokens=1000, dialogue_target_limit=5)))
    assert len(old.windows) == 1
    assert len(bounded.windows) == 4
    assert all(len(w.target_quote_ids) == 4 for w in bounded.windows)
    assert all(w.policy_version == "context-chapter-3" for w in bounded.windows)
    assert "".join(f.text for w in bounded.windows for f in main(w)) == text
    assert [q for w in bounded.windows for q in w.target_quote_ids] == [q.quote_id for q in data.quotes]
    assert "dialogue_target_limit" not in data.policy.as_key()
    assert policy_for_version("context-chapter-2") is DIALOGUE_BLOCK_POLICY
    assert policy_for_version("context-chapter-3") is BOUNDED_BLOCK_POLICY


def test_soft_target_limit_does_not_cut_an_indivisible_dialogue_block():
    data = inputs("「甲。」\n「乙。」\n「丙。」\n", budget=1000)
    bounded = plan(replace(data, policy=replace(BOUNDED_BLOCK_POLICY, dialogue_target_limit=2)))
    assert len(bounded.windows) == 1 and len(bounded.windows[0].target_quote_ids) == 3


@pytest.mark.parametrize("budget", [0, -1, True, 1.5, 5])
def test_invalid_or_oversized_complete_turn_is_not_split_to_fit(budget):
    with pytest.raises(FullContextError):
        plan(inputs(budget=budget))


def test_multiline_quote_crossing_nodes_is_coalesced_before_partition():
    text = "开头。\n「甲。\n\n乙。」\n旁白。\n「丙。」\n结尾。\n"
    result = plan(inputs(text, budget=20))
    assert [q for w in result.windows for q in w.target_quote_ids] == ["q1", "q2"]
    for w in result.windows:
        for f in main(w):
            assert not (text.index("「") < f.start_cp < text.index("」") + 1)


def test_initial_scope_does_not_include_future_paragraph_or_adjacent_chapter():
    cutoff = TEXT.index("结尾")
    data = inputs(budget=22, mode=ReadingMode.INITIAL, visible_horizon_cp=cutoff)
    result = plan(data)
    assert all(f.end_cp <= cutoff for w in result.windows for f in w.fragments)
    assert "结尾" not in "".join(f.text for w in result.windows for f in w.fragments)


def test_scope_cannot_cut_even_a_readonly_quote():
    data = inputs(visible_horizon_cp=TEXT.index("乙"), mode=ReadingMode.INITIAL)
    with pytest.raises(FullContextError):
        plan_windows(data, target_quote_ids=["q1"])


def test_source_nodes_whitespace_does_not_split_consecutive_quote_paragraphs():
    text = "开头。\n\n「甲。」\n\n「乙。」\n\n旁白。\n\n「丙。」\n\n结尾。\n"
    result = plan(inputs(text, budget=22))
    assert any({"q1", "q2"} <= set(w.target_quote_ids) for w in result.windows)


def test_no_quote_chapter_requires_no_model_window():
    assert not plan(inputs("纯叙述。" * 1000, budget=22)).windows


@pytest.mark.parametrize("text", ["a\n「a」\nb\n「b」\nc\n", TEXT.replace("旁白", "hello🙂")])
def test_prefix_meter_matches_per_fragment_rounding_for_mixed_text(text):
    data = inputs(text, budget=22)
    result = plan(data)
    for w in result.windows:
        assert w.budget["context_tokens"] == sum(
            data.estimator.estimate(f.text) for f in w.fragments
        )
        assert w.budget["context_tokens"] <= 22


def test_custom_estimator_does_not_use_the_default_heuristic_prefix_meter():
    class CharacterEstimator(TokenEstimator):
        def estimate(self, text):
            return len(text)

    data = replace(inputs(budget=22), estimator=CharacterEstimator())
    for w in plan(data).windows:
        assert w.budget["context_tokens"] == sum(len(f.text) for f in w.fragments)
        assert w.budget["context_tokens"] <= 22
