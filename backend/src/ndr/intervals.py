"""Immutable interval lookup preserving original order, overlaps and boundary semantics."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterable
from typing import Generic, TypeVar

T = TypeVar("T")


class SpanIndex(Generic[T]):
    def __init__(self, items: Iterable[T], *, start: Callable[[T], int], end: Callable[[T], int]):
        self.rows = sorted(
            (start(item), ordinal, end(item), item) for ordinal, item in enumerate(items)
        )
        self.starts = [row[0] for row in self.rows]
        self.max_ends: list[int] = []
        maximum = -(2**63)
        for _, _, finish, _ in self.rows:
            maximum = max(maximum, finish)
            self.max_ends.append(maximum)

    def overlapping(self, start: int, end: int) -> list[T]:
        left = bisect_right(self.max_ends, start)
        right = bisect_left(self.starts, end)
        # Original order preserves the existing tie-breaking for nested annotations.
        rows = [row for row in self.rows[left:right] if row[2] > start]
        return [row[3] for row in sorted(rows, key=lambda row: row[1])]
