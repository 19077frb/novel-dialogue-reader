"""Explicit bounded third judgments; no implicit thinking or production writes."""

from __future__ import annotations

from dataclasses import asdict

from ..llm.schemas import LlmOutput
from .compact import CompactTask
from .compact_trial import run_trial
from .review import Decision, compile_decisions, decisions, reconcile
from .risk import QuoteRisk, review_blocks
from .window_review import expanded_review_task, union_review_context

ADJUDICATION_VERSION = "explicit-risk-adjudication-1"


async def adjudicate_window(
    adapter,
    task: CompactTask,
    *,
    text: str,
    original: dict[str, Decision],
    reviewed: dict[str, Decision],
    model_scope: str,
    needs_context: tuple[str, ...] = (),
    locked: dict[str, Decision] | None = None,
    breaks: tuple[str, ...] = (),
    anonymous_characters: dict[str, dict] | None = None,
    max_tokens: int = 16384,
    max_format_retries: int = 1,
) -> dict:
    """One independent judgment per linked risk block, including all failure cost.

    The caller chooses and journals a credential-free model/thinking scope.
    This module never switches model or increases output caps. A third answer
    cannot introduce a different identity just because it used more reasoning.
    Neighbours supply context, but only triggered targets may change.
    """
    if not model_scope or not 0 <= max_format_retries <= 5 or max_tokens <= 0:
        raise ValueError("Explicit model scope and bounded budget required")
    for row in task.context:
        a, b = row.get("start_cp"), row.get("end_cp")
        horizon = task.visible_horizon_cp if task.reading_mode == "initial" else len(text)
        if (
            not isinstance(a, int)
            or isinstance(a, bool)
            or not isinstance(b, int)
            or isinstance(b, bool)
            or not 0 <= a < b <= horizon
            or text[a:b] != row["text"]
        ):
            raise ValueError("Adjudication requires the immutable visible original")
    resolved, reasons = reconcile(original, reviewed, locked=locked)
    # Validate all supplied decisions, not just the eventually selected ones.
    compile_decisions(task, original, breaks=breaks, anonymous_characters=anonymous_characters)
    compile_decisions(
        task,
        {**original, **reviewed},
        breaks=breaks,
        anonymous_characters=anonymous_characters,
    )
    prior = compile_decisions(
        task, resolved, breaks=breaks, anonymous_characters=anonymous_characters
    )
    targets = {task.references[q] for q in task.quote_ids}
    requested = set(needs_context)
    if requested - targets:
        raise ValueError("Context request outside the original block")
    prior.needs_context = [
        task.references[q] for q in task.quote_ids if task.references[q] in requested
    ]
    eligible = {
        q
        for q, decision in resolved.items()
        if q not in (locked or {})
        and (
            reasons[q] == "unresolved_conflict"
            or q in requested
            or decision.kind == "speech"
            and not decision.supported(q)
        )
    }
    groups = review_blocks(
        task,
        [
            QuoteRisk(q, ("adjudication_trigger",))
            for q in task.quote_ids
            if task.references[q] in eligible
        ],
        neighbours=2,
        max_targets=16,
    )
    stages, review_tasks = [], []
    third = {}
    for index, group in enumerate(groups, 1):
        fresh = expanded_review_task(text, task, group)
        result = await run_trial(
            adapter, fresh, max_tokens=max_tokens, max_format_retries=max_format_retries
        )
        stages.append({"phase": f"adjudication_{index}", "targets": list(group), "result": result})
        if result.get("reconciliation_required"):
            break
        if result["ok"]:
            proposed = LlmOutput.model_validate(result["output"])
            third.update(
                {
                    q: d
                    for q, d in decisions(fresh, proposed, call_ref=f"adjudication_{index}").items()
                    if q in eligible
                }
            )
            review_tasks.append(fresh)
    final, final_reasons = reconcile(original, reviewed, adjudicated=third, locked=locked)
    for q, decision in third.items():
        if (
            reasons[q] != "unresolved_conflict"
            and decision.signature() == resolved[q].signature()
            and decision.supported(q)
        ):
            final[q], final_reasons[q] = decision, "evidence_strengthened"
    complete = union_review_context(task, review_tasks)
    output = compile_decisions(
        complete, final, breaks=breaks, anonymous_characters=anonymous_characters
    )
    # Explicit uncertainty is not cleared merely because another call succeeded.
    output.needs_context = list(prior.needs_context)
    attempts = [
        {**attempt, "phase": stage["phase"]}
        for stage in stages
        for attempt in stage["result"]["attempts"]
    ]
    unknown = sum(stage["result"]["unknown_usage_calls"] for stage in stages)
    return {
        "ok": not bool(unknown),
        "reconciliation_required": bool(unknown),
        "output": output.model_dump(mode="json") if not unknown else None,
        "retained_output": prior.model_dump(mode="json"),
        "compiled_task": asdict(complete),
        "stages": stages,
        "attempts": attempts,
        "known_tokens": sum(stage["result"]["known_tokens"] for stage in stages),
        "unknown_usage_calls": unknown,
        "resolution_reasons": final_reasons,
        "eligible_targets": [
            task.references[q] for q in task.quote_ids if task.references[q] in eligible
        ],
        "adjudication_groups": [list(group) for group in groups],
        "adjudication_failures": sum(not s["result"]["ok"] for s in stages),
        "adjudication_version": ADJUDICATION_VERSION,
        "model_scope": model_scope,
        "max_tokens": max_tokens,
        "max_format_retries": max_format_retries,
    }
