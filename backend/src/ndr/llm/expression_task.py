"""Bind short production requests to the server's effective identity view."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass

from ..evaluation.compact import Candidate, CompactTask
from ..scenes.state import ConfirmedCharacter, SceneState

PRODUCTION_EXPRESSION_VERSION = "expression-production-1"
PRODUCTION_CACHE_VERSION = "expression-1"


@dataclass(frozen=True)
class ProjectedCompactTask(CompactTask):
    effective_profiles: tuple[dict, ...] = ()
    auxiliary_protocol: str | None = None
    identity_prompt_version: str | None = None

    def validate_effective_profiles(self):
        self.__post_init__()
        from .expression_diagnostics import DIAGNOSTICS_VERSION

        if self.auxiliary_protocol not in (None, DIAGNOSTICS_VERSION):
            raise ValueError("Unsupported auxiliary isolation version")
        profiles = {p["character_id"]: p for p in self.effective_profiles}
        if len(profiles) != len(self.effective_profiles) or set(profiles) != {
            c.character_id for c in self.candidates
        }:
            raise ValueError("Production identity profiles do not match candidates")
        if [p["character_id"] for p in self.effective_profiles] != [
            c.character_id for c in self.candidates
        ]:
            raise ValueError("Production profiles must follow the candidate mapping order")
        for candidate in self.candidates:
            p = profiles[candidate.character_id]
            if (candidate.name, candidate.aliases, candidate.description) != (
                p["canonical_name"],
                tuple(p["aliases"]),
                p["description"],
            ):
                raise ValueError("Production candidate differs from its effective profile")
            for record in p.get("identity_records", ()):
                position = record.get("visible_from_cp")
                if (type(position) is not int or position < 0
                        or self.reading_mode == "initial" and position > self.visible_horizon_cp):
                    raise ValueError("Future or invalid identity provenance")
                if record.get("kind") == "profile_update" and "evidence_spans" in record:
                    raise ValueError("Profile edits cannot claim literal identity evidence")
                for span in record.get("evidence_spans", ()):
                    if (len(span) != 2 or any(type(v) is not int for v in span)
                            or not 0 <= span[0] < span[1] <= position):
                        raise ValueError("Invalid identity provenance evidence positions")

    def messages(self):
        from ..characters.prompt_catalog import check_version, compact_catalog

        check_version(self.identity_prompt_version)
        messages = super().messages()
        data = json.loads(messages[-1]["content"])
        profiles = self.effective_profiles
        if self.identity_prompt_version:
            profiles = compact_catalog(
                profiles, context="\n".join(record["text"] for record in self.context),
            )
            profiles = [{k: v for k, v in p.items() if k not in {
                "canonical_name", "aliases", "description",
            }} for p in profiles]
        data["effective_identity_profiles"] = [
            {**p, "candidate": c.ref, "character_id": c.ref}
            for c, p in zip(self.candidates, profiles, strict=True)
        ]
        messages[-1]["content"] = json.dumps(data, ensure_ascii=False)
        return messages


def known_declaration_ids(task):
    if not isinstance(task, ProjectedCompactTask):
        return ()
    task.validate_effective_profiles()
    return tuple(c.character_id for c in task.candidates if c.character_id)


def bind_sent_identity_profiles(task, state: SceneState):
    """Apply a server-built request using what it sent, not later chapter updates.

    The freshly loaded state still establishes identity existence and visibility.
    Only its transient identity view is restored; scene bindings and database
    records remain authoritative and are checked by the ordinary validator.
    """
    if not isinstance(task, ProjectedCompactTask):
        return
    task.validate_effective_profiles()
    if (not state.projected_identity_input
            or task.reading_mode != state.identity_input_mode
            or task.visible_horizon_cp != state.identity_input_horizon):
        raise ValueError("Production expression differs from its server visibility view")
    available = {person.character_id for person in state.identity_characters}
    profiles = {p["character_id"]: ConfirmedCharacter.from_dict(p) for p in task.effective_profiles}
    if not profiles.keys() <= available:
        raise ValueError("请求中的人物身份已删除或合并，请重新确认人物后重试")
    state.book_characters = list(profiles.values())
    state.confirmed_characters = [profiles[p.character_id] for p in state.confirmed_characters
                                  if p.character_id in profiles]
    state.pov_character_id = next((c.character_id for c in task.candidates
                                   if c.ref == task.pov_ref), None)
    for slot in state.participants:
        person = profiles.get(slot.character_id)
        slot.canonical_name = person.canonical_name if person else ""
        slot.description = person.description if person else ""
    counts = Counter(p.canonical_name for p in profiles.values() if p.canonical_name)
    state.known_characters = {p.canonical_name: p.description for p in profiles.values()
                              if p.canonical_name and counts[p.canonical_name] == 1}
    slots = {slot.display_label: slot for slot in state.participants}
    state.recent_turns = [{**turn, "speaker_name": slots[turn["speaker_ref"]].canonical_name}
                          for turn in state.recent_turns if turn.get("speaker_ref") in slots]


def build_production_expression_task(
    window, state: SceneState, *, auxiliary_protocol=None, identity_prompt_version=None,
):
    from ..scenes.runner import _reference_aliases

    if not state.projected_identity_input or state.identity_input_horizon is None:
        raise ValueError("Production expressions require a server-projected identity view")
    aliases, references = _reference_aliases(window)
    profiles = tuple(
        p.as_dict() for p in sorted(state.identity_characters, key=lambda p: p.character_id)
    )
    candidates = tuple(
        Candidate(
            f"C{i}",
            p["character_id"],
            p["canonical_name"],
            tuple(p["aliases"]),
            p["description"],
            existing_ref=(
                slot.display_label if (slot := state.find_by_character(p["character_id"])) else None
            ),
        )
        for i, p in enumerate(profiles, 1)
    )
    targets = tuple(aliases[q] for q in window.target_quote_ids)
    positions = {f.fragment_id: f for f in window.fragments}
    gaps = {
        aliases[f.fragment_id]: next(
            (aliases[q] for q in window.target_quote_ids if positions[q].start_cp >= f.end_cp), None
        )
        for f in window.fragments
        if f.kind.value in {"inner_gap", "outer_gap"}
    }
    task = ProjectedCompactTask(
        quote_ids=targets,
        references=references,
        context=tuple(
            {
                "ref": aliases[f.fragment_id],
                "kind": f.kind.value,
                "start_cp": f.start_cp,
                "end_cp": f.end_cp,
                "text": f.text,
            }
            for f in window.fragments
        ),
        candidates=candidates,
        gap_next_quote=gaps,
        scene_ref=state.scene_ref,
        pov_ref=next((c.ref for c in candidates if c.character_id == state.pov_character_id), None),
        reading_mode=state.identity_input_mode,
        visible_horizon_cp=state.identity_input_horizon,
        effective_profiles=profiles,
        auxiliary_protocol=auxiliary_protocol,
        identity_prompt_version=identity_prompt_version,
    )
    task.validate_effective_profiles()
    return task
