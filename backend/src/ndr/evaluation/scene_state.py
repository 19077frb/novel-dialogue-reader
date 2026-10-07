"""Program-owned scene slots; never infer identity by name or prior confidence."""

from __future__ import annotations

import hashlib
from dataclasses import replace

from ..llm.schemas import LlmOutput
from .compact import CompactTask
from .relay import attach_relay

SCENE_STATE_VERSION = "stable-slot-continuation-1"


def root_scene(task: CompactTask, scene_key: str) -> CompactTask:
    """Namespace independent planned chains without changing model-visible facts."""
    namespace = hashlib.sha256(scene_key.encode()).hexdigest()
    return replace(task, scene_ref=f"pipeline_scene_{namespace}")


def continue_scene(task: CompactTask, prior_task: CompactTask, output: LlmOutput) -> CompactTask:
    """Continue only the last original target's scene and stable identity slots.

    No text or candidate fact is added. Anonymous source IDs are not stable IDs:
    callers must obtain an explicit identity link before they can be reused.
    """
    # Reuse exact-coordinate, original-order and complete-contract checks without
    # adding answer candidates or evidence. State does not depend on relay policy.
    attach_relay(task, prior_task, output, max_turns=0)
    labels = {label.quote_id: label for label in output.labels}
    last_scene = labels[prior_task.references[prior_task.quote_ids[-1]]].scene_ref
    slots = {}

    def add(identity: str | None, slot: str | None):
        if not identity or not slot:
            return
        if identity in slots and slots[identity] != slot:
            raise ValueError("Stable identity has conflicting slots in the same scene")
        slots[identity] = slot

    if last_scene == prior_task.scene_ref:
        for candidate in prior_task.candidates:
            add(candidate.character_id, candidate.existing_ref)
    for person in output.new_speakers:
        if person.scene_ref == last_scene:
            add(person.character_id, person.temp_ref)
    candidates = []
    for candidate in task.candidates:
        slot = slots.get(candidate.character_id)
        if candidate.existing_ref is not None and candidate.existing_ref != slot:
            raise ValueError("Explicit current slot disagrees with prior scene state")
        candidates.append(replace(candidate, existing_ref=slot))
    return replace(task, scene_ref=last_scene, candidates=tuple(candidates))
