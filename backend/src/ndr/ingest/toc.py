"""Bind publication navigation to stored chapters without changing text or identities."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..storage.models import Chapter
from .document import ParsedBook


def bind_toc(parsed: ParsedBook, chapters: Sequence[Chapter]) -> list[dict[str, Any]]:
    """Bind exact parsed targets to current ranges, including manually merged chapters."""
    by_ordinal = {chapter.ordinal: chapter for chapter in chapters}
    result = []
    for entry in parsed.toc:
        target = entry.get("target_cp")
        chapter = None
        if target is not None:
            candidate = by_ordinal.get(entry.get("chapter_ordinal"))
            if candidate is not None and candidate.start_cp <= target <= candidate.end_cp:
                chapter = candidate
            else:
                chapter = next((c for c in chapters if c.start_cp <= target < c.end_cp), None)
                if chapter is None:
                    chapter = next((c for c in chapters
                                    if c.start_cp == c.end_cp == target), None)
        result.append({"id": entry["id"], "parent_id": entry.get("parent_id"),
                       "title": entry["title"], "chapter_id": chapter.id if chapter else None,
                       "target_cp": target if chapter else None})
    return result
