"""Keep analysis-only identity equivalence outside native evidence/risk decisions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..domain.enums import QuoteKind
from ..llm.schemas import LlmOutput
from ..scenes.acceptance import decide_acceptance
from .compact import CompactTask, identity_scores
from .risk import detect_risks, risk_scores

SCORING_VERSION = "native-attribution-scoring-1"


def score_attribution_risks(
    task: CompactTask,
    output: LlmOutput,
    expected: Mapping[str, str],
    *,
    identity_mapping: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Score stable target IDs, never rewrite model or task identities for risk.

    Missing labels are measured, not accepted as a complete inference result.
    Anonymous speakers without a stable identity remain unresolved here; their
    grouping and display-name accuracy need independent evaluation.
    """
    targets = {task.references[q] for q in task.quote_ids}
    if set(expected) - targets:
        raise ValueError("Gold references an unsent target")
    if any(not isinstance(person, str) or not person for person in expected.values()):
        raise ValueError("Gold requires explicit stable identity strings")
    label_ids = [label.quote_id for label in output.labels]
    if len(label_ids) != len(set(label_ids)) or set(label_ids) - targets:
        raise ValueError("Scoring labels must be unique sent targets")
    declaration_refs = [person.temp_ref for person in output.new_speakers]
    if len(declaration_refs) != len(set(declaration_refs)):
        raise ValueError("Scoring declarations must have unique references")
    native_ids = {candidate.character_id for candidate in task.candidates if candidate.character_id}
    if any(
        person.character_id and person.character_id not in native_ids
        for person in output.new_speakers
    ):
        raise ValueError("Output identity is outside the native task namespace")
    people = {person.temp_ref: person.character_id for person in output.new_speakers}
    existing = {
        person.existing_ref: person.character_id
        for person in task.candidates
        if person.existing_ref
    }
    mapping = dict(identity_mapping or {})
    if set(mapping) - native_ids or any(
        not isinstance(person, str) or not person for person in mapping.values()
    ):
        raise ValueError("Identity mapping requires native source IDs and explicit scored IDs")
    predictions, native_predictions = {}, {}
    for label in output.labels:
        accepted = (
            label.kind is QuoteKind.SPEECH and decide_acceptance(label).status.value == "ACCEPTED"
        )
        native = (
            (people.get(label.speaker_ref) or existing.get(label.speaker_ref)) if accepted else None
        )
        native_predictions[label.quote_id] = native
        predictions[label.quote_id] = mapping.get(native, native) if native else None
    # Only the predictions above are mapped. Evidence, declarations and the
    # candidate directory below always retain the original, matching namespace.
    risks = detect_risks(task, output)
    selected = {task.references[risk.quote_id] for risk in risks}
    correct = {q for q, person in expected.items() if predictions.get(q) == person}
    wrong = {q for q, person in expected.items() if predictions.get(q) not in {None, person}}
    return {
        "scoring_version": SCORING_VERSION,
        "identities": identity_scores(
            dict(expected), {q: p for q, p in predictions.items() if q in expected}
        ),
        "predictions": predictions,
        "native_predictions": native_predictions,
        "risks": [
            {
                "quote_id": task.references[risk.quote_id],
                "ref": risk.quote_id,
                "reasons": list(risk.reasons),
            }
            for risk in risks
        ],
        "risk_metrics": risk_scores(wrong, correct, selected),
    }
