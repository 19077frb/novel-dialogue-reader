"""Paragraph-local repair suggestions for unmatched curly quotes.

The scanner treats a repair as a virtual closing delimiter: canonical positions
and source mappings stay unchanged. Suggestions are persisted separately so the
user can inspect, disable, or move a closing point before rescanning.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select

from ..domain.enums import QuoteNormalizationSource, QuoteNormalizationStatus
from ..storage.models import QuoteNormalization
from .scanner import AUTO_CLOSE_REPLACEMENT, AutoClosePoint

OPENING = "“"
CLOSING = "”"


@dataclass(frozen=True)
class AutoCloseSuggestion:
    opening_cp: int
    close_cp: int
    original_text: str
    normalized_text: str
    reason: str

    def point(self) -> AutoClosePoint:
        return AutoClosePoint(
            opening_cp=self.opening_cp,
            close_cp=self.close_cp,
            replacement=AUTO_CLOSE_REPLACEMENT,
        )


def _paragraph_end(canonical_text: str, opening_cp: int) -> int:
    newline_cp = canonical_text.find("\n", opening_cp)
    return len(canonical_text) if newline_cp < 0 else newline_cp


def detect_auto_close_suggestions(canonical_text: str) -> tuple[AutoCloseSuggestion, ...]:
    """Find unmatched “ whose paragraph can be closed before a next opening quote."""

    stack: list[int] = []
    delimiter_positions: list[int] = []
    for index, char in enumerate(canonical_text):
        if char in (OPENING, CLOSING):
            delimiter_positions.append(index)
        if char == OPENING:
            stack.append(index)
        elif char == CLOSING and stack:
            stack.pop()

    suggestions: list[AutoCloseSuggestion] = []
    for opening_cp in sorted(stack):
        close_cp = _paragraph_end(canonical_text, opening_cp)
        if close_cp <= opening_cp + 1:
            continue
        # 一次建立索引，避免每个缺失闭引号都重新扫描到书末。
        next_index = bisect_left(delimiter_positions, close_cp)
        if (
            next_index == len(delimiter_positions)
            or canonical_text[delimiter_positions[next_index]] != OPENING
        ):
            continue
        original_text = canonical_text[opening_cp:close_cp]
        suggestions.append(
            AutoCloseSuggestion(
                opening_cp=opening_cp,
                close_cp=close_cp,
                original_text=original_text,
                normalized_text=original_text + AUTO_CLOSE_REPLACEMENT,
                reason="开引号未在本段或后续段落闭合，且下一处引号是开引号。",
            )
        )
    return tuple(sorted(suggestions, key=lambda item: item.opening_cp))


def upsert_suggestions(
    session,  # noqa: ANN001 - SQLAlchemy Session
    version_id: str,
    suggestions: Sequence[AutoCloseSuggestion],
) -> int:
    """Persist newly detected suggestions while preserving user-edited rows."""

    existing = {
        row.opening_cp: row
        for row in session.execute(
            select(QuoteNormalization).where(QuoteNormalization.book_version_id == version_id)
        ).scalars()
    }
    created = 0
    for suggestion in suggestions:
        row = existing.get(suggestion.opening_cp)
        if row is not None:
            continue
        session.add(
            QuoteNormalization(
                book_version_id=version_id,
                opening_cp=suggestion.opening_cp,
                close_cp=suggestion.close_cp,
                original_text=suggestion.original_text,
                normalized_text=suggestion.normalized_text,
                reason=suggestion.reason,
                source=QuoteNormalizationSource.AUTO,
                status=QuoteNormalizationStatus.ACTIVE,
            )
        )
        created += 1
    session.flush()
    return created


def active_close_points(rows: Sequence[QuoteNormalization]) -> tuple[AutoClosePoint, ...]:
    return tuple(
        AutoClosePoint(
            opening_cp=row.opening_cp,
            close_cp=row.close_cp,
            replacement=row.replacement,
        )
        for row in rows
        if row.status is QuoteNormalizationStatus.ACTIVE
    )
