"""Add evidence-backed names without overwriting a confirmed identity."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..storage.models import BookCharacter
from .names import GENERIC_NAMES, matches_name, valid_display_name


def match_contextual_identity(candidate, facts, catalog, *, original):
    """Recover a missing ID only from a unique, concrete, already-visible role context."""
    roles = {fact.value for fact in facts if fact.kind == "designation"
             and fact.value not in GENERIC_NAMES}
    if candidate.character_id or not roles or not catalog:
        return None
    # Anchors must be named people in this request, not another generic role.
    anchors = {}
    for person in catalog:
        if not any(item.get("kind") == "name" for item in person.get("identity_records", [])):
            continue
        for name in [person.get("name", ""), *person.get("aliases", [])]:
            if name and len(name) >= 2 and name not in GENERIC_NAMES and name not in roles:
                anchors.setdefault(name, set()).add(person["character_id"])
    anchors = {name: next(iter(ids)) for name, ids in anchors.items() if len(ids) == 1}
    if not anchors:
        return None
    pattern = re.compile("|".join(
        re.escape(name) for name in sorted(anchors, key=len, reverse=True)
    ))

    def contexts(values, role):
        result = set()
        for value in values:
            # Compare the explicit identity clause, never today's action or a fuzzy summary.
            clause = re.split(r"[；;。！？!?，,\n]", value, maxsplit=1)[0].strip()
            if (not clause.endswith(role) or len(clause) > 80 or not pattern.search(clause)
                    or not re.search(r"(?:打工|任职|工作|任教|所属)(?:的|地点的|所在的)", clause)):
                continue
            result.add(pattern.sub(lambda match: "{" + anchors[match[0]] + "}",
                                   re.sub(r"\s+", "", clause)))
        return result

    proof = "\n".join(original.text[a:b] for fact in facts for a, b in fact.evidence_spans)
    # A new holder of the same office is not the previous person.
    if re.search(r"不是|并非|不同|另一位|前任|上一任|新任|接替|更换|换了|新来的", proof):
        return None
    if any(re.search(r"(?:新(?:的)?|前)" + re.escape(role), proof) for role in roles):
        return None
    if any(re.search(r"(?:另一|第二|两位|两名|多位|其他)\S{0,3}" + re.escape(role), proof)
           for role in roles):
        return None
    values = [fact.value for fact in facts if fact.kind in {"description", "relation"}]
    matches = set()
    for person in catalog:
        known_roles = {person.get("name"), *person.get("aliases", []),
                       *(item.get("value") for item in person.get("identity_records", [])
                         if item.get("kind") == "designation")}
        eligible_roles = roles & known_roles
        if not eligible_roles:
            continue
        known = [person.get("description", ""),
                 *(item.get("value", "") for item in person.get("relations", []))]
        if any(contexts(values, role) & contexts(known, role) for role in eligible_roles):
            matches.add(person["character_id"])
    return next(iter(matches)) if len(matches) == 1 else None


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
