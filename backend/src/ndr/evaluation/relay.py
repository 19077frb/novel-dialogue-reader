"""Bounded original-text relay, not a memory of supposedly confirmed answers."""

from __future__ import annotations

from dataclasses import replace

from ..llm.schemas import LlmOutput
from ..llm.validation import LabelingTargets, validate_output
from .compact import CompactTask
from .review import decisions

RELAY_VERSION = "original-turn-relay-2"


def _rows(task: CompactTask) -> dict[str, dict]:
    result = {}
    for row in task.context:
        a, b = row.get("start_cp"), row.get("end_cp")
        if (
            not isinstance(a, int)
            or isinstance(a, bool)
            or not isinstance(b, int)
            or isinstance(b, bool)
            or a < 0
            or b <= a
            or len(row["text"]) != b - a
        ):
            raise ValueError("Relay requires exact original-text coordinates")
        result[task.references[row["ref"]]] = row
    return result


def _consistent(rows: list[dict]) -> None:
    for i, left in enumerate(rows):
        for right in rows[i + 1 :]:
            a = max(left["start_cp"], right["start_cp"])
            b = min(left["end_cp"], right["end_cp"])
            if a < b and (
                left["text"][a - left["start_cp"] : b - left["start_cp"]]
                != right["text"][a - right["start_cp"] : b - right["start_cp"]]
            ):
                raise ValueError("Relay original-text snapshots disagree")


def attach_relay(
    task: CompactTask,
    prior_task: CompactTask,
    prior_output: LlmOutput,
    *,
    max_turns: int = 8,
    max_evidence_per_turn: int = 3,
    max_added_chars: int = 4000,
) -> CompactTask:
    """Transfer previous candidates with original proof, never promote identity.

    All transferred text precedes the new window. Unknown or unmapped identities
    remain explicit; same generic names do not establish a cross-call identity.
    Limits may omit whole turns/proofs, but never paraphrase or truncate a quote.
    """
    if not 0 <= max_turns <= 8 or not 0 <= max_evidence_per_turn <= 3 or max_added_chars < 0:
        raise ValueError("Invalid bounded relay limits")
    current, prior = _rows(task), _rows(prior_task)
    _consistent([*current.values(), *prior.values()])
    targets = [task.references[q] for q in task.quote_ids]
    old_targets = [prior_task.references[q] for q in prior_task.quote_ids]
    if not targets or not old_targets:
        raise ValueError("Relay requires nonempty windows")
    start = min(current[q]["start_cp"] for q in targets)
    if any(prior[q]["end_cp"] > start for q in old_targets):
        raise ValueError("Relay windows must follow original-text dependency order")
    labels = [label.quote_id for label in prior_output.labels]
    if len(labels) != len(set(labels)) or set(labels) != set(old_targets):
        raise ValueError("Relay requires a complete valid previous block")
    report = validate_output(
        prior_output,
        LabelingTargets(
            quote_ids=tuple(old_targets),
            gap_ids=tuple(prior_task.references[g] for g in prior_task.gap_next_quote),
            scene_refs=(prior_task.scene_ref,),
            speaker_refs=tuple(c.existing_ref for c in prior_task.candidates if c.existing_ref),
            evidence_ids=tuple(prior_task.references.values()),
            character_ids=tuple(c.character_id for c in prior_task.candidates if c.character_id),
            require_display_names=True,
        ),
    )
    if not report.ok:
        raise ValueError("Relay previous block failed strict internal validation")
    if any(set(label.evidence_refs) - set(prior) for label in prior_output.labels):
        raise ValueError("Relay cannot retrieve evidence not sent in the previous block")
    if max_turns == 0:
        return task
    answers = decisions(prior_task, prior_output, call_ref="previous_window")
    mapped = {c.character_id: c.ref for c in task.candidates if c.character_id}
    refs, context = dict(task.references), list(task.context)
    short = {stable: ref for ref, stable in refs.items()}
    used, relay = 0, []

    def include(stable: str) -> str | None:
        nonlocal used
        row = prior[stable]
        if row["end_cp"] > start:
            return None
        if stable in short:
            return short[stable]
        if used + len(row["text"]) > max_added_chars:
            return None
        number = len(refs) + 1
        while f"R{number}" in refs:
            number += 1
        ref = f"R{number}"
        used += len(row["text"])
        short[stable], refs[ref] = ref, stable
        context.append({**row, "ref": ref, "kind": "overlap"})
        return ref

    selected = sorted(old_targets, key=lambda q: prior[q]["start_cp"])[-max_turns:]
    # Spend the text budget on the most recent turns; present them in original order.
    for stable in reversed(selected):
        ref = include(stable)
        if ref is None:
            continue
        answer = answers[stable]
        proof = []
        for evidence in answer.evidence[:max_evidence_per_turn]:
            evidence_ref = include(evidence)
            if evidence_ref is not None:
                proof.append(evidence_ref)
        candidate = mapped.get(answer.character_id)
        relay.append(
            {
                "ref": ref,
                "candidate": candidate,
                "kind": answer.kind,
                "basis": answer.basis,
                "evidence": proof,
                "source": "previous_window_model_candidate",
                "status": "unconfirmed",
                "support": "external_evidence_candidate"
                if candidate and any(e != ref for e in proof) and answer.supported(stable)
                else "unresolved_or_weak",
                "identity_unmapped": bool(answer.character_id and not candidate)
                or bool(answer.anonymous_ref),
                "evidence_omitted": len(answer.evidence) - len(proof),
            }
        )
    return replace(task, references=refs, context=tuple(context), relay=tuple(reversed(relay)))


def attach_dependency_relay(
    task: CompactTask,
    predecessors: tuple[tuple[CompactTask, LlmOutput], ...],
    *,
    max_turns: int = 8,
    max_evidence_per_turn: int = 3,
    max_added_chars: int = 4000,
) -> CompactTask:
    """Share one bounded original-text budget across explicit dependency parents.

    Only answer/evidence candidates transfer; scene slots and identity facts do
    not. Most recent parents get budget first, then turns are displayed in text
    order. Every parent is validated even when the budget omits all its turns.
    """
    if (
        not isinstance(max_turns, int)
        or isinstance(max_turns, bool)
        or not 0 <= max_turns <= 8
        or not isinstance(max_evidence_per_turn, int)
        or isinstance(max_evidence_per_turn, bool)
        or not 0 <= max_evidence_per_turn <= 3
        or not isinstance(max_added_chars, int)
        or isinstance(max_added_chars, bool)
        or max_added_chars < 0
        or len(task.relay) > max_turns
    ):
        raise ValueError("Invalid shared dependency relay limits")
    ordered, targets = [], set()
    for prior_task, output in predecessors:
        attach_relay(task, prior_task, output, max_turns=0)
        rows = _rows(prior_task)
        stable = [prior_task.references[q] for q in prior_task.quote_ids]
        if set(stable) & targets:
            raise ValueError("Dependency relay parents repeat target identities")
        targets.update(stable)
        ordered.append((max(rows[q]["end_cp"] for q in stable), prior_task, output))
    prepared, original_refs, turns = task, set(task.references.values()), list(task.relay)
    for _, prior_task, output in sorted(ordered, key=lambda value: value[0], reverse=True):
        added_chars = sum(
            len(row["text"])
            for row in prepared.context
            if prepared.references[row["ref"]] not in original_refs
        )
        added = attach_relay(
            prepared,
            prior_task,
            output,
            max_turns=max_turns - len(turns),
            max_evidence_per_turn=max_evidence_per_turn,
            max_added_chars=max_added_chars - added_chars,
        )
        # attach_relay only returns this parent's turns, so merge explicitly.
        if max_turns - len(turns) > 0:
            if {turn["ref"] for turn in turns} & {turn["ref"] for turn in added.relay}:
                raise ValueError("Dependency relay repeats an existing turn")
            turns.extend(added.relay)
        prepared = replace(added, relay=())
    rows = {row["ref"]: row for row in prepared.context}
    turns.sort(key=lambda turn: rows[turn["ref"]]["start_cp"])
    return replace(prepared, relay=tuple(turns))
