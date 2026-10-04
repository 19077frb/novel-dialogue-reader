"""Isolated dependency execution with paid-call recovery, not a product queue."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import asdict, dataclass

from ..llm.schemas import LlmOutput
from ..llm.validation import LabelingTargets, validate_output
from .compact import Candidate, CompactTask
from .compact_trial import run_trial
from .journal import (
    CallJournal,
    JournaledAdapter,
    ReconciliationRequired,
    SnapshotChanged,
    TrialStopped,
)
from .relay import RELAY_VERSION, attach_relay
from .review import REVIEW_VERSION
from .risk import RISK_VERSION
from .scene_plan import WindowDependency
from .scene_state import SCENE_STATE_VERSION, continue_scene, root_scene

PIPELINE_VERSION = "dependency-execution-2"
WindowProcessor = Callable[[JournaledAdapter, CompactTask], Awaitable[dict]]


@dataclass(frozen=True)
class PipelinePolicy:
    # Credential-free endpoint/model/thinking/settings signature, not an API key.
    model_scope: str
    concurrency: int = 1
    relay: bool = False
    max_format_retries: int = 1
    max_tokens: int = 8192
    processor_version: str = "attribution-only-1"

    def __post_init__(self):
        if (
            not self.model_scope
            or not self.processor_version
            or not isinstance(self.concurrency, int)
            or self.concurrency not in {1, 2}
            or isinstance(self.concurrency, bool)
            or not isinstance(self.max_format_retries, int)
            or isinstance(self.max_format_retries, bool)
            or not 0 <= self.max_format_retries <= 5
            or not isinstance(self.max_tokens, int)
            or isinstance(self.max_tokens, bool)
            or self.max_tokens <= 0
            or not isinstance(self.relay, bool)
        ):
            raise ValueError("Explicit model/strategy and bounded pipeline policy required")


def restore_task(value: dict) -> CompactTask:
    return CompactTask(
        **{
            **value,
            "quote_ids": tuple(value["quote_ids"]),
            "context": tuple(value["context"]),
            "candidates": tuple(
                Candidate(**{**c, "aliases": tuple(c["aliases"])}) for c in value["candidates"]
            ),
            "evidence_hints": tuple(value.get("evidence_hints", ())),
            "identity_facts": tuple(value.get("identity_facts", ())),
            "relay": tuple(value.get("relay", ())),
        }
    )


def _snapshot(text: str, task: CompactTask) -> None:
    for row in task.context:
        a, b = row.get("start_cp"), row.get("end_cp")
        if (
            not isinstance(a, int)
            or isinstance(a, bool)
            or not isinstance(b, int)
            or isinstance(b, bool)
            or not 0 <= a < b <= len(text)
            or text[a:b] != row["text"]
        ):
            raise ValueError("Task evidence differs from the immutable original snapshot")


def _plan(tasks: dict[str, CompactTask], plan: tuple[WindowDependency, ...]) -> None:
    if not tasks or tuple(tasks) != tuple(node.window for node in plan):
        raise ValueError("Plan must cover each ordered window exactly once")
    seen, scenes, previous_scene = set(), {}, None
    spans, target_ids = [], set()
    for node in plan:
        if (
            not node.scene
            or not set(node.depends_on) <= seen
            or len(set(node.depends_on)) != len(node.depends_on)
        ):
            raise ValueError("Invalid forward or duplicate dependency")
        if node.scene in scenes and (
            node.scene != previous_scene or scenes[node.scene] not in node.depends_on
        ):
            raise ValueError("Same-scene windows must follow their previous window")
        scene = tasks[node.window]
        if not scene.quote_ids:
            raise ValueError("Do not dispatch an empty window")
        rows = {r["ref"]: r for r in scene.context}
        for q in scene.quote_ids:
            stable = scene.references[q]
            if stable in target_ids:
                raise ValueError("Repeated target across windows")
            target_ids.add(stable)
            spans.append((rows[q]["start_cp"], rows[q]["end_cp"]))
        seen.add(node.window)
        scenes[node.scene], previous_scene = node.window, node.scene
    if any(left[1] > right[0] for left, right in zip(spans, spans[1:], strict=False)):
        raise ValueError("Windows must follow nonoverlapping original targets")


def pipeline_fingerprint(
    text: str,
    tasks: dict[str, CompactTask],
    plan: tuple[WindowDependency, ...],
    policy: PipelinePolicy,
) -> str:
    _plan(tasks, plan)
    for task in tasks.values():
        _snapshot(text, task)
    value = {
        "pipeline": PIPELINE_VERSION,
        "relay": RELAY_VERSION,
        "risk": RISK_VERSION,
        "review": REVIEW_VERSION,
        "scene_state": SCENE_STATE_VERSION,
        "policy": asdict(policy),
        "original": hashlib.sha256(text.encode()).hexdigest(),
        "tasks": [(key, task.fingerprint()) for key, task in tasks.items()],
        "plan": [asdict(node) for node in plan],
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _validated_result(text: str, prepared: CompactTask, result: dict) -> CompactTask:
    compiled = restore_task(result["compiled_task"]) if "compiled_task" in result else prepared
    _snapshot(text, compiled)
    if (
        compiled.quote_ids != prepared.quote_ids
        or compiled.candidates != prepared.candidates
        or compiled.pov_ref != prepared.pov_ref
        or compiled.identity_facts != prepared.identity_facts
        or compiled.gap_next_quote != prepared.gap_next_quote
        or compiled.scene_ref != prepared.scene_ref
        or compiled.reading_mode != prepared.reading_mode
        or compiled.visible_horizon_cp != prepared.visible_horizon_cp
        or any(compiled.references[q] != prepared.references[q] for q in prepared.quote_ids)
    ):
        raise ValueError("Processor cannot change the target or identity snapshot")
    if not isinstance(result.get("ok"), bool):
        raise ValueError("Processor must report an explicit success or failure")
    if result.get("ok"):
        output = LlmOutput.model_validate(result["output"])
        report = validate_output(
            output,
            LabelingTargets(
                quote_ids=tuple(compiled.references[q] for q in compiled.quote_ids),
                gap_ids=tuple(compiled.references[g] for g in compiled.gap_next_quote),
                scene_refs=(compiled.scene_ref,),
                speaker_refs=tuple(c.existing_ref for c in compiled.candidates if c.existing_ref),
                evidence_ids=tuple(compiled.references.values()),
                character_ids=tuple(c.character_id for c in compiled.candidates if c.character_id),
                require_display_names=True,
            ),
        )
        if not report.ok:
            raise ValueError("Processor result failed strict internal validation")
    return compiled


async def execute_pipeline(
    adapter,
    journal: CallJournal,
    *,
    text: str,
    tasks: dict[str, CompactTask],
    plan: tuple[WindowDependency, ...],
    policy: PipelinePolicy,
    process_window: WindowProcessor | None = None,
) -> dict:
    """Drain independent chains up to the limit, preserving all paid responses.

    A failing parent blocks its dependants, not other scenes. Unknown outcomes
    stop new dispatch. Cancellation never cancels a dispatched paid request:
    stop/version protection prevents submission while responses are accounted.
    """
    tasks = deepcopy(tasks)
    if journal.fingerprint != pipeline_fingerprint(text, tasks, plan, policy):
        raise SnapshotChanged("Pipeline context/model/strategy differs from journal")
    if (process_window is None) != (policy.processor_version == "attribution-only-1"):
        raise ValueError("Custom processor requires an explicit, distinct strategy version")
    before = journal.stats()
    if before["stopped"]:
        raise TrialStopped("Resume the stopped experiment explicitly")
    if before["unknown_calls"]:
        raise ReconciliationRequired("Prior dispatched/unknown requests require reconciliation")
    epoch, began = before["epoch"], time.perf_counter()

    class FixedPolicyAdapter(JournaledAdapter):
        async def generate_labels(self, request):
            if request.get("max_tokens") != policy.max_tokens or (
                request.get("max_tokens_override") != policy.max_tokens
            ):
                raise ValueError("Processor cannot silently change the output budget")
            return await super().generate_labels(request)

    wrapped = FixedPolicyAdapter(adapter, journal)
    pending, active, completed, reused = dict((n.window, n) for n in plan), {}, {}, []

    def current_version():
        state = journal.stats()
        if state["stopped"] or state["epoch"] != epoch:
            raise SnapshotChanged("Stop or configuration change prevents new dispatch")
        if state["unresolved_calls"]:
            raise ReconciliationRequired("Unknown usage prevents new dispatch")

    async def window(node: WindowDependency) -> dict:
        prepared = tasks[node.window]
        parent = next(
            (p for p in reversed(node.depends_on) if completed[p]["scene"] == node.scene), None
        )
        if parent:
            source = completed[parent]
            prior_task = restore_task(source["compiled_task"])
            prior_output = LlmOutput.model_validate(source["result"]["output"])
            prepared = continue_scene(prepared, prior_task, prior_output)
            if policy.relay:
                prepared = attach_relay(prepared, prior_task, prior_output)
        else:
            prepared = root_scene(
                prepared,
                node.scene,
            )
        fingerprint = prepared.fingerprint()
        key = f"window:{node.window}"
        cached = journal.load_checkpoint(key)
        if cached is not None:
            if cached["input_fingerprint"] != fingerprint or cached["scene"] != node.scene:
                raise SnapshotChanged("Restored window dependencies changed")
            restored = _validated_result(text, prepared, cached["result"])
            if restore_task(cached["compiled_task"]).fingerprint() != restored.fingerprint():
                raise SnapshotChanged("Restored evidence snapshot changed")
            reused.append(node.window)
            return cached
        current_version()
        result = (
            await process_window(wrapped, prepared)
            if process_window
            else await run_trial(
                wrapped,
                prepared,
                max_format_retries=policy.max_format_retries,
                max_tokens=policy.max_tokens,
            )
        )
        if prepared.fingerprint() != fingerprint:
            raise SnapshotChanged("Processor modified the immutable input snapshot")
        if result.get("reconciliation_required"):
            raise ReconciliationRequired("Usage unknown; result kept in call ledger, not submitted")
        compiled = _validated_result(text, prepared, result)
        saved = {
            "scene": node.scene,
            "input_fingerprint": fingerprint,
            "compiled_task": asdict(compiled),
            "result": result,
            "state": "complete" if result.get("ok") else "failed",
        }
        journal.checkpoint(key, saved, expected_epoch=epoch)
        return saved

    try:
        while pending or active:
            current_version()
            for key, node in list(pending.items()):
                if not all(p in completed for p in node.depends_on):
                    continue
                if any(completed[p]["state"] != "complete" for p in node.depends_on):
                    completed[key] = {
                        "state": "blocked",
                        "scene": node.scene,
                        "blocked_by": list(node.depends_on),
                    }
                    del pending[key]
                elif len(active) < policy.concurrency:
                    active[asyncio.create_task(window(node))] = key
                    del pending[key]
            if not active:
                if pending:
                    raise ValueError("Unresolved dependency cycle")
                break
            done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
            for handle in done:
                key = active.pop(handle)
                completed[key] = handle.result()
    except asyncio.CancelledError:
        state = journal.stats()
        if state["epoch"] == epoch and not state["stopped"]:
            journal.configure(
                expected_epoch=epoch,
                stopped=True,
                max_calls=state["max_calls"],
                max_tokens=state["max_tokens"],
            )
        raise
    finally:
        # Account dispatched calls, even on an unknown response, stop or cancel.
        if active:
            await asyncio.gather(*active, return_exceptions=True)
    after = journal.stats()
    return {
        "pipeline_version": PIPELINE_VERSION,
        "windows": completed,
        "reused_windows": reused,
        "wall_seconds": time.perf_counter() - began,
        "paid_calls_this_invocation": after["calls"] - before["calls"],
        "known_tokens_this_invocation": after["known_tokens"] - before["known_tokens"],
        "ledger": after,
        "complete": all(row["state"] == "complete" for row in completed.values()),
    }
