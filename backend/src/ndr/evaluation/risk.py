"""Transparent risk triggers for evaluation, not calibrated truth probabilities."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..domain.enums import Assignment, QuoteKind, SpeakerBasis
from ..llm.schemas import LlmOutput
from .compact import CompactTask

RISK_VERSION = "evidence-risk-1"


@dataclass(frozen=True)
class QuoteRisk:
    quote_id: str
    reasons: tuple[str, ...]


def detect_risks(
    task: CompactTask, output: LlmOutput, *, audit_stride: int = 16
) -> list[QuoteRisk]:
    """Audit accepted answers too, including deterministic stratified samples.

    A name or speech verb is only a retrieval hint. Missing anchors request
    review; their presence never certifies a speaker conclusion.
    """
    if audit_stride < 1:
        raise ValueError("Audit stride must be positive")
    short = {stable: ref for ref, stable in task.references.items()}
    context = {task.references[row["ref"]]: row for row in task.context}
    declarations = {person.temp_ref: person for person in output.new_speakers}
    candidates = {person.character_id: person for person in task.candidates if person.character_id}
    labels = {label.quote_id: label for label in output.labels}
    evidence_users: dict[str, set[str]] = {}
    for label in output.labels:
        for evidence in label.evidence_refs:
            if label.speaker_ref:
                evidence_users.setdefault(evidence, set()).add(label.speaker_ref)
    result = []
    targets = tuple(task.references[q] for q in task.quote_ids)
    for index, quote_id in enumerate(targets):
        label = labels.get(quote_id)
        reasons = []
        if label is None:
            reasons.append("missing_label")
        else:
            if label.kind is not QuoteKind.SPEECH:
                reasons.append("speech_type_ambiguity")
            if label.assignment is Assignment.UNKNOWN or label.kind is QuoteKind.UNKNOWN:
                reasons.append("unknown")
            if label.basis in {SpeakerBasis.STYLE_ONLY, SpeakerBasis.INSUFFICIENT}:
                reasons.append("weak_basis")
            external = [e for e in label.evidence_refs if e != quote_id and e in context]
            if label.kind is QuoteKind.SPEECH and not external:
                reasons.append("no_external_evidence")
            person = declarations.get(label.speaker_ref)
            candidate = (
                candidates.get(person.character_id)
                if person
                else next((p for p in task.candidates if p.existing_ref == label.speaker_ref), None)
            )
            if person and not person.character_id:
                reasons.append("new_identity")
            evidence_text = "\n".join(context[e]["text"] for e in external)
            if label.basis is SpeakerBasis.DIRECT:
                names = (candidate.name, *candidate.aliases) if candidate else ()
                if not any(name in evidence_text for name in names if name) or not re.search(
                    r"说|问|答|喊|叫|开口|吐槽", evidence_text
                ):
                    reasons.append("direct_relation_unverified")
            if any(len(evidence_users.get(e, ())) > 1 for e in external):
                reasons.append("shared_evidence_conflict")
            for evidence in external:
                dependency = labels.get(evidence)
                if dependency and dependency.assignment is Assignment.UNKNOWN:
                    reasons.append("uncertain_evidence_chain")
            quote_text = context.get(quote_id, {}).get("text", "")
            if (
                candidate
                and any(name in quote_text for name in (candidate.name, *candidate.aliases) if name)
                and not re.search(r"我是|我叫|本人", quote_text)
            ):
                reasons.append("addressed_person_conflict")
        if index in {0, len(targets) - 1}:
            reasons.append("window_edge")
        if index % audit_stride == 0:
            reasons.append("accepted_sample_audit")
        if reasons:
            result.append(QuoteRisk(short.get(quote_id, quote_id), tuple(dict.fromkeys(reasons))))
    return result


def review_blocks(
    task: CompactTask, risks: list[QuoteRisk], *, neighbours: int = 2, max_targets: int = 16
) -> tuple[tuple[str, ...], ...]:
    """Merge nearby risky turns into linked blocks, never one API call per line."""
    if neighbours < 0 or max_targets < 1:
        raise ValueError("Invalid review limits")
    positions = {q: i for i, q in enumerate(task.quote_ids)}
    selected = set()
    for risk in risks:
        if risk.quote_id not in positions:
            raise ValueError("Risk refers to an unsent target")
        i = positions[risk.quote_id]
        selected.update(range(max(0, i - neighbours), min(len(positions), i + neighbours + 1)))
    groups = []
    current = []
    for i in sorted(selected):
        if current and (i != current[-1] + 1 or len(current) == max_targets):
            groups.append(tuple(task.quote_ids[n] for n in current))
            current = []
        current.append(i)
    if current:
        groups.append(tuple(task.quote_ids[n] for n in current))
    return tuple(groups)


def risk_scores(error_ids: set[str], correct_ids: set[str], selected_ids: set[str]) -> dict:
    """Only gold-backed errors count toward recall; unresolved is not 'correct'."""
    if error_ids & correct_ids:
        raise ValueError("Error and correct gold sets overlap")
    return {
        "error_recall": len(error_ids & selected_ids) / len(error_ids) if error_ids else None,
        "unnecessary_review_rate": len(correct_ids & selected_ids) / len(correct_ids)
        if correct_ids
        else None,
        "error_count": len(error_ids),
        "selected_error_count": len(error_ids & selected_ids),
        "selected_count": len(selected_ids),
    }
