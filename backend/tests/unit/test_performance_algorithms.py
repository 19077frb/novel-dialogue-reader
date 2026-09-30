"""Differential checks against the original linear interval/window algorithms."""

from __future__ import annotations

import random

from ndr.context.budget import BudgetPolicy
from ndr.context.source_selection import GapView, QuoteView
from ndr.context.window_builder import (
    OVERSIZED_WARNING,
    WindowInputs,
    _required_tokens,
    _split_targets,
)
from ndr.intervals import SpanIndex


def test_span_index_matches_linear_filter_with_nested_and_equal_spans() -> None:
    rng = random.Random(8765)
    spans = [(rng.randrange(100), rng.randrange(100)) for _ in range(500)]
    spans += [(0, 100), (50, 50), (20, 40), (20, 40)]
    index = SpanIndex(spans, start=lambda row: row[0], end=lambda row: row[1])
    for _ in range(500):
        start, end = rng.randrange(110), rng.randrange(110)
        assert index.overlapping(start, end) == [
            row for row in spans if row[0] < end and row[1] > start
        ]
    assert SpanIndex([], start=lambda row: 0, end=lambda row: 0).overlapping(0, 1) == []


def test_split_targets_matches_original_required_token_algorithm() -> None:
    rng = random.Random(42)
    text = "中文 English 123。\n" * 100
    for _ in range(150):
        quotes = [
            QuoteView(quote_id=f"q{i}", start_cp=i * 20, end_cp=i * 20 + rng.randrange(1, 45))
            for i in range(40)
        ]
        gaps = [
            GapView(
                gap_id=f"g{i}",
                start_cp=rng.randrange(800),
                end_cp=rng.randrange(800),
                left_quote_id="q0",
                right_quote_id="q1",
            )
            for i in range(30)
        ]
        inputs = WindowInputs(
            book_version_id="v",
            canonical_text=text,
            quotes=quotes,
            gaps=gaps,
            policy=BudgetPolicy(context_tokens=rng.randrange(1, 100)),
        )
        groups, current, warnings = [], [], []
        for quote in quotes:
            if (
                inputs.estimator.estimate(text[quote.start_cp : quote.end_cp])
                > inputs.policy.context_tokens
            ):
                warnings.append(f"{OVERSIZED_WARNING}:{quote.quote_id}")
                if current:
                    groups.append(current)
                    current = []
                groups.append([quote])
                continue
            candidate = [*current, quote]
            cost = _required_tokens(
                text=text,
                targets=candidate,
                gaps=gaps,
                estimator=inputs.estimator,
            )
            if current and cost > inputs.policy.context_tokens:
                groups.append(current)
                current = [quote]
            else:
                current = candidate
        if current:
            groups.append(current)
        assert _split_targets(inputs=inputs, ordered_targets=quotes) == (groups, warnings)
