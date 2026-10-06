"""Bind short production requests to the server's effective identity view."""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..evaluation.compact import Candidate, CompactTask
from ..scenes.state import SceneState

PRODUCTION_EXPRESSION_VERSION = "expression-production-1"
PRODUCTION_CACHE_VERSION = "expression-1"


@dataclass(frozen=True)
class ProjectedCompactTask(CompactTask):
    effective_profiles: tuple[dict, ...] = ()

    def validate_effective_profiles(self):
        self.__post_init__()
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

    def messages(self):
        messages = super().messages()
        data = json.loads(messages[-1]["content"])
        data["effective_identity_profiles"] = [
            {**p, "candidate": c.ref, "character_id": c.ref}
            for c, p in zip(self.candidates, self.effective_profiles, strict=True)
        ]
        messages[-1]["content"] = json.dumps(data, ensure_ascii=False)
        return messages


def known_declaration_ids(task):
    if not isinstance(task, ProjectedCompactTask):
        return ()
    task.validate_effective_profiles()
    return tuple(c.character_id for c in task.candidates if c.character_id)


def build_production_expression_task(window, state: SceneState):
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
    )
    task.validate_effective_profiles()
    return task
