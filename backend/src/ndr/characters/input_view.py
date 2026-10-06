"""Read-only identity projection for versioned production inference inputs."""

from __future__ import annotations

import json
from collections import Counter

from ..domain.enums import CharacterSource, ReadingMode
from ..scenes.state import ConfirmedCharacter, SceneState
from .facts import visible_identity_profile

IDENTITY_INPUT_VERSION = "identity-input-1"


def project_identity_state(state: SceneState, people, version, *, reading_mode, horizon: int):
    """Keep identity references, never carry unseen mutable fields into a request."""
    projected = {}
    for person in people:
        relations = ()
        records = json.loads(person.identity_facts_json or "[]")
        if not isinstance(records, list):
            raise ValueError("人物事实不是列表")
        if reading_mode is ReadingMode.REREAD and not records:
            name, aliases, description = (person.canonical_name or "",
                                          tuple(json.loads(person.aliases_json or "[]")),
                                          person.description or "")
        elif records:
            profile = visible_identity_profile(person, version, horizon=horizon)
            name, aliases, description = (profile["name"] or "", profile["aliases"],
                                          profile["description"])
            relations = tuple({"value": fact.value, "visible_from_cp": fact.visible_from_cp,
                               "source": fact.source, "source_ref": fact.source_ref,
                               "evidence_spans": [list(span) for span in fact.evidence_spans]}
                              for fact in profile["relations"])
        else:
            history = json.loads(person.presentation_history_json or "[]")
            if not isinstance(history, list):
                raise ValueError("人物显示历史不是列表")
            if any(not isinstance(item, dict) for item in history):
                raise ValueError("人物显示历史记录无效")
            eligible = [(item["cp"], index, item) for index, item in enumerate(history)
                        if type(item.get("cp")) is int and 0 <= item["cp"] <= horizon]
            current = max(eligible, default=(0, 0, {}))[2]
            name, aliases, description = current.get("name", ""), (), current.get("description", "")
        if (not isinstance(name, str) or not isinstance(description, str)
                or not isinstance(aliases, (list, tuple))
                or any(not isinstance(value, str) for value in aliases)):
            raise ValueError("人物可见资料格式无效")
        projected[person.id] = ConfirmedCharacter(
            person.id, name, tuple(aliases), description,
            source=CharacterSource(person.source).value, user_confirmed=person.user_confirmed,
            confirmation_source=person.confirmation_source or "unknown",
            relations=relations,
        )
    state.confirmed_characters = [projected[p.character_id] for p in state.confirmed_characters
                                  if p.character_id in projected]
    state.book_characters = list(projected.values())
    state.projected_identity_input = True
    for slot in state.participants:
        person = projected.get(slot.character_id)
        slot.canonical_name = person.canonical_name if person else ""
        slot.description = person.description if person else ""
    counts = Counter(p.canonical_name for p in projected.values() if p.canonical_name)
    state.known_characters = {p.canonical_name: p.description for p in projected.values()
                              if p.canonical_name and counts[p.canonical_name] == 1}
    slots = {slot.display_label: slot for slot in state.participants}
    state.recent_turns = [{"quote_id": item.get("quote_id"), "speaker_ref": item.get("speaker_ref"),
                           "speaker_name": slots[item["speaker_ref"]].canonical_name}
                          for item in state.recent_turns if item.get("speaker_ref") in slots]
    return projected
