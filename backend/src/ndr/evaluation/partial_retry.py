"""Preserve independently valid proposals without partially accepting a window."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, replace

from pydantic import ValidationError

from ..llm.errors import InvalidModelOutput
from .compact import CompactOutput, CompactTask, DiscoveredCharacter, compile_output

PARTIAL_RETRY_VERSION = "dependency-block-retry-1"


def _shape(payload: dict, targets: tuple[str, ...]) -> dict[str, dict] | None:
    if set(payload) - {"labels", "breaks", "new_characters", "needs_context"}:
        return None
    rows = payload.get("labels")
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        return None
    labels = {}
    for row in rows:
        q = row.get("q")
        if not isinstance(q, str) or q not in targets or q in labels:
            return None
        labels[q] = row
    return labels if set(labels) == set(targets) else None


@dataclass(frozen=True)
class RetainedBlock:
    task: CompactTask
    payload: dict
    pending: tuple[str, ...]

    def retry_task(self) -> CompactTask:
        # Keep all original evidence, but a partial answer must not change the
        # entire window's scene decisions. Final compilation uses the full task.
        return replace(self.task, quote_ids=self.pending, gap_next_quote={})

    @property
    def retained_count(self) -> int:
        return len(self.task.quote_ids) - len(self.pending)

    @property
    def reserved_identities(self) -> tuple[str, ...]:
        return tuple(c["ref"] for c in self.payload["new_characters"])

    def combine(self, response: dict) -> dict:
        labels = _shape(response, self.pending)
        if labels is None:
            raise InvalidModelOutput("Partial retry must cover only its requested targets once")
        if response.get("breaks", []) != []:
            raise InvalidModelOutput("Partial retry cannot change retained scene boundaries")
        declarations = response.get("new_characters", [])
        if not isinstance(declarations, list) or any(not isinstance(c, dict) for c in declarations):
            raise InvalidModelOutput("Invalid partial identity declarations")
        if any(c.get("ref") in self.reserved_identities for c in declarations):
            raise InvalidModelOutput(
                "Partial retry reused a reserved identity; no name-based merge"
            )
        requests = response.get("needs_context", [])
        if not isinstance(requests, list) or any(not isinstance(q, str) for q in requests):
            raise InvalidModelOutput("Invalid partial context requests")
        if len(set(requests)) != len(requests) or set(requests) - set(self.pending):
            raise InvalidModelOutput(
                "Partial context requests must cite distinct requested targets"
            )
        kept = {r["q"]: r for r in self.payload["labels"]}
        kept.update(labels)
        return {
            "labels": [deepcopy(kept[q]) for q in self.task.quote_ids],
            "breaks": deepcopy(self.payload["breaks"]),
            "new_characters": deepcopy([*self.payload["new_characters"], *declarations]),
            "needs_context": list(dict.fromkeys([*self.payload["needs_context"], *requests])),
        }


def isolate_failed_block(payload: dict, task: CompactTask) -> RetainedBlock | None:
    """Return only a structurally proven partial proposal; otherwise retry all.

    Dependants of a bad label/declaration are quarantined together, including
    target evidence inside shared new-identity declarations. Semantic conflicts
    are never repaired here. UNKNOWN placeholders are validation probes only.
    """
    labels = _shape(payload, task.quote_ids)
    if labels is None:
        return None
    try:
        metadata = CompactOutput.model_validate(
            {
                "labels": [],
                "breaks": payload.get("breaks", []),
                "needs_context": payload.get("needs_context", []),
            }
        )
    except ValidationError:
        return None
    if (
        len(set(metadata.breaks)) != len(metadata.breaks)
        or any(task.gap_next_quote.get(g) is None for g in metadata.breaks)
        or len({task.gap_next_quote[g] for g in metadata.breaks}) != len(metadata.breaks)
        or len(set(metadata.needs_context)) != len(metadata.needs_context)
        or set(metadata.needs_context) - set(task.quote_ids)
    ):
        return None
    declared = payload.get("new_characters", [])
    if not isinstance(declared, list):
        return None
    declarations, bad_declarations = {}, set()
    for raw in declared:
        if not isinstance(raw, dict):
            return None
        ref = raw.get("ref")
        if (
            not isinstance(ref, str)
            or not re.fullmatch(r"N[1-9][0-9]*", ref)
            or ref in declarations
        ):
            return None
        declarations[ref] = raw
        try:
            person = DiscoveredCharacter.model_validate(raw)
            if set(person.evidence) - set(task.references):
                bad_declarations.add(ref)
        except ValidationError:
            bad_declarations.add(ref)

    edges = {q: set() for q in task.quote_ids}
    uses = {ref: [] for ref in declarations}
    for q, row in labels.items():
        evidence = row.get("evidence", [])
        if isinstance(evidence, list):
            for other in evidence:
                if isinstance(other, str) and other in edges:
                    edges[q].add(other)
                    edges[other].add(q)
        character = row.get("character")
        if isinstance(character, str) and character in uses:
            uses[character].append(q)
    for ref, users in uses.items():
        if not users:
            # An unused or ambiguously addressed declaration has no safe block.
            return None
        evidence = declarations[ref].get("evidence", [])
        proof_targets = (
            [q for q in evidence if isinstance(q, str) and q in edges]
            if isinstance(evidence, list)
            else []
        )
        members = [*users, *proof_targets]
        for q in members:
            edges[q].update(members)

    failed = set()
    for q, row in labels.items():
        character = row.get("character")
        if isinstance(character, str) and character in bad_declarations:
            failed.add(q)
            continue
        person = declarations.get(character) if isinstance(character, str) else None
        try:
            compile_output(
                {"labels": [row], "new_characters": [person] if person else []},
                replace(task, quote_ids=(q,), gap_next_quote={}),
            )
        except (InvalidModelOutput, ValidationError):
            failed.add(q)
    if not failed:
        return None
    while True:
        closure = failed | set().union(*(edges[q] for q in failed))
        if closure == failed:
            break
        failed = closure
    if failed == set(task.quote_ids):
        return None
    retained = [deepcopy(labels[q]) for q in task.quote_ids if q not in failed]
    retained_refs = {r.get("character") for r in retained if isinstance(r.get("character"), str)}
    kept = {
        "labels": retained,
        "breaks": list(metadata.breaks),
        "new_characters": [deepcopy(c) for ref, c in declarations.items() if ref in retained_refs],
        "needs_context": list(metadata.needs_context),
    }
    probe = {
        **kept,
        "labels": [
            deepcopy(labels[q])
            if q not in failed
            else {
                "q": q,
                "kind": "speech",
                "character": None,
                "basis": "insufficient",
                "evidence": [],
            }
            for q in task.quote_ids
        ],
    }
    try:
        compile_output(probe, task)
    except (InvalidModelOutput, ValidationError):
        return None
    return RetainedBlock(task, kept, tuple(q for q in task.quote_ids if q in failed))
