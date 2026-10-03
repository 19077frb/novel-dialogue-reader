"""Linked review proposals: disagreement is not an automatic correction.

This module never writes a library. Decisions are combined by stable identity,
then compiled as a complete dependency block through the existing validator.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from ..domain.enums import Assignment, QuoteKind, SpeakerBasis
from ..llm.adapter import ProviderAdapter
from ..llm.schemas import LlmOutput
from .compact import CompactTask, compile_output
from .compact_trial import run_trial

REVIEW_VERSION = "linked-review-2"


@dataclass(frozen=True)
class Decision:
    kind: str
    character_id: str | None
    basis: str | None
    evidence: tuple[str, ...]
    # Anonymous identities cannot be matched across calls by similar names.
    anonymous_ref: str | None = None

    def signature(self) -> tuple[str, str | None, str | None]:
        return self.kind, self.character_id, self.anonymous_ref

    def supported(self, quote_id: str) -> bool:
        return (
            self.kind == "speech"
            and self.character_id is not None
            and self.basis in {"direct", "coreference", "response_link"}
            and any(e != quote_id for e in self.evidence)
        )


def decisions(task: CompactTask, output: LlmOutput, *, call_ref: str) -> dict[str, Decision]:
    """Keep scene-local anonymous IDs scoped to their source call, not their name."""
    identities = {c.existing_ref: c.character_id for c in task.candidates if c.existing_ref}
    identities.update({p.temp_ref: p.character_id for p in output.new_speakers})
    result = {}
    for label in output.labels:
        identity = identities.get(label.speaker_ref)
        anonymous = (
            f"{call_ref}:{label.speaker_ref}"
            if label.speaker_ref and identity is None and label.assignment is not Assignment.UNKNOWN
            else None
        )
        result[label.quote_id] = Decision(
            label.kind.value,
            identity,
            label.basis.value.lower() if label.basis else None,
            tuple(label.evidence_refs),
            anonymous,
        )
    return result


def candidate_review_task(
    task: CompactTask, prior_task: CompactTask, prior_output: LlmOutput
) -> CompactTask:
    """Explicit candidate arm; the independent arm uses the unmodified task."""
    prior = decisions(prior_task, prior_output, call_ref="previous")
    local = {c.character_id: c.ref for c in task.candidates if c.character_id}
    relay = []
    for ref in task.quote_ids:
        decision = prior.get(task.references[ref])
        if decision:
            relay.append(
                {
                    "ref": ref,
                    "candidate": local.get(decision.character_id),
                    "kind": decision.kind,
                    "basis": decision.basis,
                    "source": "unconfirmed_model_candidate",
                    "evidence": [
                        short
                        for short, stable in task.references.items()
                        if stable in decision.evidence
                    ],
                }
            )
    return replace(task, relay=tuple(relay))


async def run_linked_review(
    adapter: ProviderAdapter,
    task: CompactTask,
    prior_task: CompactTask,
    prior_output: LlmOutput,
    *,
    mode: Literal["independent", "candidate"] = "independent",
    max_format_retries: int = 1,
    max_tokens: int = 8192,
) -> dict:
    if mode not in {"independent", "candidate"}:
        raise ValueError("Unknown review arm")
    targets = {task.references[q] for q in task.quote_ids}
    if not targets <= {prior_task.references[q] for q in prior_task.quote_ids}:
        raise ValueError("Review must target a subset of the original block")
    review_task = (
        candidate_review_task(task, prior_task, prior_output) if mode == "candidate" else task
    )
    result = await run_trial(
        adapter, review_task, max_format_retries=max_format_retries, max_tokens=max_tokens
    )
    return {**result, "review_mode": mode, "review_version": REVIEW_VERSION}


def reconcile(
    original: dict[str, Decision],
    reviewed: dict[str, Decision],
    *,
    adjudicated: dict[str, Decision] | None = None,
    locked: dict[str, Decision] | None = None,
) -> tuple[dict[str, Decision], dict[str, str]]:
    """A third evidence-bearing decision may corroborate either candidate.

    Evidence presence is necessary, not proof of truth; semantic performance
    still requires gold evaluation. Non-speech/type conflicts remain unknown
    unless corroborated. Missing or invalid review does not erase a valid base.
    """
    adjudicated, locked = adjudicated or {}, locked or {}
    if (
        set(reviewed) - set(original)
        or set(adjudicated) - set(original)
        or set(locked) - set(original)
    ):
        raise ValueError("Review/lock includes a target outside the dependency block")
    result, reasons = {}, {}
    for quote_id, base in original.items():
        if quote_id in locked:
            result[quote_id], reasons[quote_id] = locked[quote_id], "user_locked"
            continue
        second = reviewed.get(quote_id)
        if second is None:
            result[quote_id], reasons[quote_id] = base, "review_unavailable"
            continue
        if base.signature() == second.signature():
            if base.supported(quote_id) and not second.supported(quote_id):
                # Same identity is not a new proof. A weaker repeated answer
                # must not erase the existing admissible original evidence.
                result[quote_id], reasons[quote_id] = base, "agreement_kept_supported_base"
            else:
                result[quote_id], reasons[quote_id] = second, "agreement"
            continue
        third = adjudicated.get(quote_id)
        if third is not None:
            matches = [d for d in (base, second) if d.signature() == third.signature()]
            if matches and (
                third.kind not in {QuoteKind.SPEECH.value, QuoteKind.UNKNOWN.value}
                or third.supported(quote_id)
            ):
                result[quote_id], reasons[quote_id] = third, "evidence_corroborated"
                continue
        # A conflict in speech type cannot be represented as certain speech.
        kind = "speech" if base.kind == second.kind == "speech" else "unknown"
        result[quote_id] = Decision(
            kind, None, SpeakerBasis.INSUFFICIENT.value.lower() if kind == "speech" else None, ()
        )
        reasons[quote_id] = "unresolved_conflict"
    return result, reasons


def compile_decisions(
    task: CompactTask,
    resolved: dict[str, Decision],
    *,
    breaks: tuple[str, ...] = (),
    anonymous_characters: dict[str, dict] | None = None,
) -> LlmOutput:
    """Rebuild first-use slots atomically; never patch NEW/EXISTING by hand.

    The caller must supply the original anonymous declaration and explicitly
    approved boundaries. Review evidence outside this task must first be added
    as original context, not discarded or rewritten into a fabricated proof.
    """
    expected = {task.references[q] for q in task.quote_ids}
    if set(resolved) != expected:
        raise ValueError("Resolved decisions must cover the full dependency block")
    short = {stable: ref for ref, stable in task.references.items()}
    candidates = {c.character_id: c.ref for c in task.candidates if c.character_id}
    anonymous_characters = anonymous_characters or {}
    new_refs, discoveries, labels = {}, [], []
    for q in task.quote_ids:
        decision = resolved[task.references[q]]
        if decision.kind != "speech":
            labels.append({"q": q, "kind": decision.kind})
            continue
        character = candidates.get(decision.character_id)
        if decision.character_id is not None and character is None:
            raise ValueError("Resolved identity is not in the task snapshot")
        if decision.anonymous_ref:
            key = decision.anonymous_ref
            if key not in anonymous_characters:
                raise ValueError("Missing source declaration for anonymous identity")
            if key not in new_refs:
                new_refs[key] = f"N{len(new_refs) + 1}"
                source = anonymous_characters[key]
                discoveries.append(
                    {
                        "ref": new_refs[key],
                        "name": source["name"],
                        "description": source["description"],
                        "evidence": [short[e] for e in source["evidence"]],
                    }
                )
            character = new_refs[key]
        labels.append(
            {
                "q": q,
                "kind": "speech",
                "character": character,
                "basis": decision.basis,
                "evidence": [short[e] for e in decision.evidence],
            }
        )
    return compile_output(
        {"labels": labels, "breaks": list(breaks), "new_characters": discoveries}, task
    )
