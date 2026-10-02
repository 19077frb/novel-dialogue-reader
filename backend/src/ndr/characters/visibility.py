"""Append-only reader presentation, independent of deleted canonical identities."""

import json

from sqlalchemy import select

from ..api.errors import ApiError
from ..storage.models import BookCharacter, Chapter, Quote


def position(version, cp):
    if cp is not None and cp > version.canonical_length_cp:
        raise ApiError.validation("人物信息的可见位置超出正文范围")
    return version.canonical_length_cp if cp is None else cp


def history(row):
    return json.loads(row.presentation_history_json or "[]")


def identity(row):
    return (f"character:{row.id}" if isinstance(row, BookCharacter) else
            f"character:{row.character_id}" if row.character_id else f"group:{row.id}")


def capture(row, cp, *, force=False):
    """Append current metadata; capture a baseline before changing an untracked row."""
    rows = history(row)
    value = {"cp": cp, "identity": identity(row), "name": row.canonical_name or "",
             "description": row.description or ""}
    if force or not rows or any(rows[-1].get(key) != value[key] for key in
                       ("identity", "name", "description")):
        rows.append(value)
        row.presentation_history_json = json.dumps(rows, ensure_ascii=False)


def baseline(row):
    if not history(row):
        capture(row, 0)


def initialize(row, cp, character=None):
    if character and history(character):
        row.presentation_history_json = character.presentation_history_json
    else:
        capture(row, cp)


def visible_value(raw, horizon, *, fallback):
    if horizon is None:
        return fallback
    rows = json.loads(raw or "[]")
    if not rows:
        return fallback
    eligible = [(row["cp"], index, row) for index, row in enumerate(rows)
                if horizon is None or row["cp"] <= horizon]
    return max(eligible, key=lambda item: (item[0], item[1]))[2] if eligible else {
        "identity": fallback["private_identity"], "name": "未确认说话人", "description": "",
    }


def chapter_end_for_quote(session, quote_id, fallback):
    end = session.scalar(select(Chapter.end_cp).join(Quote, Quote.chapter_id == Chapter.id)
                         .where(Quote.id == quote_id))
    return end if end is not None else fallback
