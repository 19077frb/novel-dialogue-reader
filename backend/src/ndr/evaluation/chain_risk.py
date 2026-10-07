"""Opt-in evidence dependency risks; flags request review, never change labels."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict, deque

from ..domain.enums import QuoteKind, SpeakerBasis
from ..llm.schemas import LlmOutput
from .compact import CompactTask
from .risk import RISK_VERSION, QuoteRisk, detect_risks

CHAIN_RISK_VERSION = "evidence-chain-risk-1"
VOCATIVE_DELIMITERS = "，,、！!？?：:"
CHAIN_SEEDS = (
    "unknown",
    "weak_basis",
    "missing_label",
    "no_external_evidence",
    "new_identity",
    "speech_type_ambiguity",
    "direct_relation_unverified",
    "addressed_person_conflict",
    "uncertain_evidence_chain",
    "requested_context",
    "response_addressee_conflict",
)


def chain_risk_fingerprint(source_fingerprint: str) -> str:
    return hashlib.sha256(
        json.dumps(
            [
                CHAIN_RISK_VERSION,
                RISK_VERSION,
                VOCATIVE_DELIMITERS,
                CHAIN_SEEDS,
                source_fingerprint,
            ],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def detect_chain_risks(
    task: CompactTask, output: LlmOutput, *, audit_stride: int = 16
) -> list[QuoteRisk]:
    """Augment native risks with soft addressee conflicts and graph closure.

    A vocative may be quoted, joking or redirected; it does not determine the
    respondent. Shared names remain ambiguous. Only already-provided native
    candidates and evidence participate, so this does not retrieve future data.
    Audit samples and window edges are not uncertainty propagation seeds.
    """
    targets = tuple(task.references[q] for q in task.quote_ids)
    target_set = set(targets)
    label_ids = [label.quote_id for label in output.labels]
    if len(target_set) != len(targets) or len(set(label_ids)) != len(label_ids):
        raise ValueError("Evidence chains require unique targets and labels")
    if set(label_ids) - target_set:
        raise ValueError("Evidence chain label references an unsent target")
    original = detect_risks(task, output, audit_stride=audit_stride)
    reasons = {task.references[r.quote_id]: list(r.reasons) for r in original}
    labels = {label.quote_id: label for label in output.labels}
    context = {task.references[row["ref"]]: row for row in task.context}
    identities = {
        person.existing_ref: person.character_id
        for person in task.candidates
        if person.existing_ref
    }
    identities.update({person.temp_ref: person.character_id for person in output.new_speakers})
    reverse: dict[str, set[str]] = defaultdict(set)

    def add(quote: str, reason: str):
        current = reasons.setdefault(quote, [])
        if reason not in current:
            current.append(reason)

    for q, label in labels.items():
        if label.kind is not QuoteKind.SPEECH:
            continue
        for dependency in label.evidence_refs:
            if dependency == q:
                continue
            if dependency in target_set:
                reverse[dependency].add(q)
            if dependency not in labels or label.basis is not SpeakerBasis.RESPONSE_LINK:
                continue
            text = context.get(dependency, {}).get("text", "").lstrip(' 「『“"')
            addressed = {
                candidate.character_id
                for candidate in task.candidates
                if candidate.character_id
                for name in (candidate.name, *candidate.aliases)
                if name
                and re.match(re.escape(name) + f"[{VOCATIVE_DELIMITERS}]", text)
            }
            speaker = identities.get(label.speaker_ref)
            if len(addressed) == 1 and speaker and speaker not in addressed:
                add(q, "response_addressee_conflict")

    seeds = set(CHAIN_SEEDS)
    queue = deque(q for q, rs in reasons.items() if seeds.intersection(rs))
    visited = set(queue)
    while queue:
        source = queue.popleft()
        for q in reverse.get(source, ()):
            add(q, "uncertain_evidence_chain")
            if q not in visited:
                visited.add(q)
                queue.append(q)
    # Traversal order does not alter target/reason order or mutate input data.
    return [
        QuoteRisk(ref, tuple(reasons[task.references[ref]]))
        for ref in task.quote_ids
        if task.references[ref] in reasons
    ]
