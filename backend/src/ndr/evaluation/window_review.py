"""One complete window plus explicit bounded linked reviews, never a library write."""

from __future__ import annotations

from dataclasses import asdict, replace

from ..llm.schemas import LlmOutput
from .compact import CompactTask
from .compact_trial import run_trial
from .review import Decision, compile_decisions, decisions, reconcile, run_linked_review
from .risk import RISK_VERSION, detect_risks, review_blocks

WINDOW_REVIEW_VERSION = "complete-linked-window-2"


def expanded_review_task(
    text: str, task: CompactTask, selected: tuple[str, ...], *, margin: int = 1000
) -> CompactTask:
    """Rebuild linked original excerpts, not a generated summary or future lookup."""
    if not selected or not 0 <= margin <= 2000:
        raise ValueError("Nonempty bounded review required")
    order = {q: i for i, q in enumerate(task.quote_ids)}
    if any(q not in order for q in selected) or list(selected) != sorted(
        set(selected), key=order.get
    ):
        raise ValueError("Review must select distinct ordered original targets")
    old = {row["ref"]: row for row in task.context}
    rows = [old[q] for q in selected]
    horizon = task.visible_horizon_cp if task.reading_mode == "initial" else len(text)
    start, end = max(0, rows[0]["start_cp"] - margin), min(horizon, rows[-1]["end_cp"] + margin)
    references = {q: task.references[q] for q in selected}
    context = [dict(old[q]) for q in selected]
    gaps = {}

    def add(ref: str, a: int, b: int, kind: str, stable: str | None = None):
        if a >= b:
            return
        references[ref] = stable or f"review_context:{a}:{b}"
        context.append({"ref": ref, "start_cp": a, "end_cp": b, "kind": kind, "text": text[a:b]})

    add("ER_before", start, rows[0]["start_cp"], "overlap")
    for i, (left, right) in enumerate(zip(rows, rows[1:], strict=False), 1):
        if left["end_cp"] < right["start_cp"]:
            ref = f"GR{i}"
            add(ref, left["end_cp"], right["start_cp"], "inner_gap")
            gaps[ref] = selected[i]
    add("ER_after", rows[-1]["end_cp"], end, "overlap")
    for i, row in enumerate(task.context):
        if row.get("kind") == "overlap" and row["end_cp"] <= horizon:
            stable = task.references[row["ref"]]
            if stable not in references.values():
                add(f"ER_anchor{i}", row["start_cp"], row["end_cp"], "overlap", stable)
    return replace(
        task,
        quote_ids=selected,
        references=references,
        context=tuple(context),
        gap_next_quote=gaps,
        evidence_hints=(),
        relay=(),
    )


def union_review_context(base: CompactTask, reviews: list[CompactTask]) -> CompactTask:
    references, rows = dict(base.references), list(base.context)
    existing = {base.references[row["ref"]]: row for row in rows}
    for task in reviews:
        for row in task.context:
            stable = task.references[row["ref"]]
            if stable in existing:
                original = existing[stable]
                if any(original.get(k) != row.get(k) for k in ("text", "start_cp", "end_cp")):
                    raise ValueError("Review original snapshot changed")
                continue
            ref = f"EU{len(references) + 1}"
            while ref in references:
                ref += "u"
            references[ref] = stable
            added = {**row, "ref": ref, "kind": "overlap"}
            rows.append(added)
            existing[stable] = added
    return replace(base, references=references, context=tuple(rows))


async def refine_window(
    adapter,
    task: CompactTask,
    *,
    text: str,
    mode: str = "candidate",
    max_format_retries: int = 1,
    max_tokens: int = 8192,
    locked: dict[str, Decision] | None = None,
) -> dict:
    """Keep valid base on known review failures; never accept disagreement blindly.

    The processor uses only the supplied adapter so the pipeline's journal and
    concurrency remain authoritative. Unknown accounting stops immediately.
    Anonymous declarations stay scoped to each source, without name merging.
    """
    if mode not in {"candidate", "independent"}:
        raise ValueError("Explicit review mode required")
    initial = await run_trial(
        adapter, task, max_format_retries=max_format_retries, max_tokens=max_tokens
    )
    stages = [{"phase": "initial", "result": initial}]
    if not initial["ok"] or initial.get("reconciliation_required"):
        return {**initial, "stages": stages, "review_version": WINDOW_REVIEW_VERSION}
    output = LlmOutput.model_validate(initial["output"])
    original = decisions(task, output, call_ref="base")
    risks = detect_risks(task, output)
    groups = review_blocks(task, risks, neighbours=2, max_targets=16)
    reviewed, review_tasks, declarations = {}, [], {}

    def anonymous(source: str, proposed: LlmOutput):
        for person in proposed.new_speakers:
            if not person.character_id:
                declarations[f"{source}:{person.temp_ref}"] = {
                    "name": person.name,
                    "description": person.description,
                    "evidence": list(person.evidence_refs),
                }

    anonymous("base", output)
    for i, group in enumerate(groups, 1):
        fresh = expanded_review_task(text, task, group)
        proposal = await run_linked_review(
            adapter,
            fresh,
            task,
            output,
            mode=mode,
            max_format_retries=max_format_retries,
            max_tokens=max_tokens,
        )
        stages.append({"phase": f"review_{i}", "result": proposal})
        if proposal.get("reconciliation_required"):
            return {
                **initial,
                "ok": False,
                "reconciliation_required": True,
                "stages": stages,
                "review_version": WINDOW_REVIEW_VERSION,
            }
        if proposal["ok"]:
            proposed = LlmOutput.model_validate(proposal["output"])
            source = f"review_{i}"
            reviewed.update(decisions(fresh, proposed, call_ref=source))
            anonymous(source, proposed)
            review_tasks.append(fresh)
    resolved, reasons = reconcile(original, reviewed, locked=locked)
    complete_task = union_review_context(task, review_tasks)
    short = {stable: ref for ref, stable in task.references.items()}
    # Semantic scene decisions from the complete base block are kept explicit;
    # a review cannot silently rewrite unrelated scene/state dependencies.
    breaks = tuple(short[scene.after_gap_id] for scene in output.scene_updates)
    compiled = compile_decisions(
        complete_task, resolved, breaks=breaks, anonymous_characters=declarations
    )
    requested = set(output.needs_context)
    for stage in stages[1:]:
        if stage["result"]["ok"]:
            requested.update(stage["result"]["output"]["needs_context"])
    compiled.needs_context = [
        task.references[q] for q in task.quote_ids if task.references[q] in requested
    ]
    attempts = [
        {**attempt, "phase": stage["phase"]}
        for stage in stages
        for attempt in stage["result"]["attempts"]
    ]
    return {
        **initial,
        "ok": True,
        "output": compiled.model_dump(mode="json"),
        "compiled_task": asdict(complete_task),
        "attempts": attempts,
        "stages": stages,
        "known_tokens": sum(stage["result"]["known_tokens"] for stage in stages),
        "unknown_usage_calls": sum(stage["result"]["unknown_usage_calls"] for stage in stages),
        "review_mode": mode,
        "review_version": WINDOW_REVIEW_VERSION,
        "risk_version": RISK_VERSION,
        "review_failures": sum(not stage["result"]["ok"] for stage in stages[1:]),
        "review_complete": all(stage["result"]["ok"] for stage in stages[1:]),
        "risks": [asdict(risk) for risk in risks],
        "review_groups": [list(group) for group in groups],
        "resolution_reasons": reasons,
    }
