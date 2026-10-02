"""Add evidence-backed names without overwriting a confirmed identity."""

from __future__ import annotations

import json
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..storage.models import BookCharacter
from .names import GENERIC_NAMES, matches_name, valid_display_name


def supplement_aliases(
    session: Session, character: BookCharacter, names: Iterable[str],
    *, characters: list[BookCharacter] | None = None,
) -> None:
    """Only extend aliases; ambiguous names never become automatic merge keys."""
    others = characters if characters is not None else list(session.scalars(
        select(BookCharacter).where(BookCharacter.book_version_id == character.book_version_id)
    ))
    aliases = json.loads(character.aliases_json or "[]")
    original = list(aliases)
    for value in names:
        value = value.strip()
        if (not valid_display_name(value) or value in GENERIC_NAMES
                or value == character.canonical_name or value in aliases):
            continue
        if any(row.id != character.id and matches_name(
            value, [row.canonical_name or "", *json.loads(row.aliases_json or "[]")],
        ) for row in others):
            continue
        if len(aliases) < 64:
            aliases.append(value)
    if aliases != original:
        character.aliases_json = json.dumps(aliases, ensure_ascii=False)
        character.version = (character.version or 0) + 1
