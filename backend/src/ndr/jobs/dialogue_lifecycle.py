"""Local dialogue finalization and exact returned-receipt recovery; never sends requests."""

from __future__ import annotations

import json
import threading

from sqlalchemy import select

from ..domain.enums import InferenceRunState, JobKind, JobState
from ..llm.errors import ProviderError, ProviderErrorKind
from ..llm.receipt import ProviderResult
from ..storage.models import InferenceRun, Job, JobWindow
from ..storage.run_archive import decode_archive
from ..storage.transactions import database_error_detail, finish_local_write

DIALOGUE_KINDS = {JobKind.INFERENCE, JobKind.RECHECK, JobKind.RECOMPUTE}
_guard = threading.Lock()
_active: set[str] = set()


class ReceiptRecoveryError(ValueError):
    """Only fixed, safe local recovery diagnostics, never provider content."""


def acquire(job_id: str) -> bool:
    with _guard:
        if job_id in _active:
            return False
        _active.add(job_id)
        return True


def release(job_id: str) -> None:
    with _guard:
        _active.discard(job_id)


def returned_archive(run):
    try:
        archive = decode_archive(run.call_archive)
    except ValueError:
        return None
    if archive.get("phase") != "returned":
        return None
    if not isinstance(archive.get("adapter_result"), dict) and not isinstance(
        archive.get("error"), dict
    ):
        return None
    return archive


def settle_interrupted(factory, job_id: str, error: Exception, *, startup=False) -> JobState:
    """Close an exited worker, retaining completed data and unknown-call protection."""
    def finish(session):
        job = session.get(Job, job_id)
        if job is None:
            return JobState.FAILED
        if job.state in {JobState.COMPLETED, JobState.PAUSED}:
            return job.state
        rows = list(session.scalars(select(JobWindow).where(JobWindow.job_id == job_id)))
        completed = {row.window_id for row in rows if row.state is JobState.COMPLETED}
        receipts: dict[str, list[str]] = {}
        unknown: set[str] = set()
        runs = list(session.scalars(select(InferenceRun).where(
            InferenceRun.job_id == job_id,
        ).order_by(InferenceRun.created_at, InferenceRun.id)))
        latest = {run.window_id: run.id for run in runs}
        for run in runs:
            archive = returned_archive(run)
            if archive is not None:
                if run.window_id not in completed:
                    receipts.setdefault(run.window_id, []).append(run.id)
                raw = archive.get("adapter_result")
                raw = raw if isinstance(raw, dict) else {}
                remote_error = archive.get("error")
                remote_error = remote_error if isinstance(remote_error, dict) else {}
                details = remote_error.get("details")
                details = details if isinstance(details, dict) else {}
                usage = raw.get("_usage") or details.get("usage")
                if isinstance(usage, dict) and not usage.get("unknown"):
                    run.usage_json = json.dumps(usage, ensure_ascii=False)
                run.elapsed_ms = archive.get("elapsed_ms") or run.elapsed_ms
                if run.state is InferenceRunState.DISPATCHED:
                    # Receipt exists, but semantic application did not finish.
                    run.state = InferenceRunState.FAILED
                    run.error_code = "LOCAL_FINALIZATION_FAILED"
            elif (run.state is InferenceRunState.DISPATCHED or
                  run.window_id not in completed and run.id == latest[run.window_id]
                  and run.state in {
                      InferenceRunState.UNKNOWN_OUTCOME, InferenceRunState.SUCCEEDED,
                  }):
                run.state = InferenceRunState.UNKNOWN_OUTCOME
                run.error_code = "UNKNOWN_OUTCOME"
                unknown.add(run.window_id)
        checkpoint = json.loads(job.checkpoint_json or "{}")
        checkpoint["dialogue_receipts"] = receipts
        job.checkpoint_json = json.dumps(checkpoint, ensure_ascii=False)
        for row in rows:
            if row.state is JobState.COMPLETED:
                continue
            if row.window_id in unknown:
                row.state = JobState.NEEDS_RECONCILIATION
            elif row.state is JobState.RUNNING:
                row.state = JobState.FAILED
        job.state = (
            JobState.NEEDS_RECONCILIATION if unknown else
            JobState.PAUSED if job.state is JobState.PAUSING else
            JobState.PARTIAL if any(row.state is JobState.COMPLETED for row in rows) else
            JobState.FAILED
        )
        detail = database_error_detail(error)
        if isinstance(error, ReceiptRecoveryError):
            detail = str(error)
        if startup:
            detail = f"进程重启，{detail}"
        job.last_error = (
            f"对白任务执行中断：{detail}；已有返回保留，可从任务详情恢复。"
            if not unknown else
            f"对白任务执行中断：{detail}；部分调用结果未知，不会自动重发。"
        )
        progress = json.loads(job.progress_json or "{}")
        job.progress_json = json.dumps({
            **progress, "stage": "interrupted", "error_type": detail,
            "windows_done": sum(row.state is JobState.COMPLETED for row in rows),
        }, ensure_ascii=False)
        return job.state

    return finish_local_write(factory, finish)


def restore_dispatch(session, *, job_id, window_id, request, snapshot, fingerprint):
    """Reuse only the latest receipt for this exact stage and frozen request."""
    job = session.get(Job, job_id)
    ids = json.loads(job.checkpoint_json or "{}").get("dialogue_receipts", {}).get(window_id, [])
    stage = request.get("review_stage")
    found_stage = False
    for run_id in reversed(ids):
        run = session.get(InferenceRun, run_id)
        archive = returned_archive(run) if run is not None else None
        if (run is None or run.job_id != job_id or run.window_id != window_id
                or archive is None):
            raise ReceiptRecoveryError("保存的对白调用回执不完整，请核实原任务；不会自动重发")
        saved_request = archive.get("request", {})
        if saved_request.get("review_stage") != stage:
            continue
        found_stage = True
        if (run.request_fingerprint != fingerprint or saved_request != request
                or json.loads(run.profile_snapshot_json or "{}") != (snapshot or {})):
            # A format correction has the same stage but a different request.
            # Replay its earlier primary first, then the exact saved correction.
            continue
        error = archive.get("error")
        if error:
            restored = ProviderError(
                ProviderErrorKind(error["kind"]), error["message"],
                details=error.get("details"), receipt=archive.get("provider_receipt"),
            )
            restored.dispatch_attempts = 0
            return None, restored, run.id, run.elapsed_ms or 0
        result = ProviderResult(archive["adapter_result"], receipt=archive.get("provider_receipt"))
        result.restored_attempt = True
        result.dispatch_attempts = 0
        return result, None, run.id, run.elapsed_ms or 0
    if found_stage:
        raise ReceiptRecoveryError("对白回执与当前人物、原文或模型配置不一致；不会自动重发")
    return None


def verify_receipts(session, *, job_id, window_id, snapshot, fingerprint_for):
    """Check the whole saved chain even when an existing review checkpoint restores primary."""
    job = session.get(Job, job_id)
    ids = json.loads(job.checkpoint_json or "{}").get("dialogue_receipts", {}).get(window_id, [])
    for run_id in ids:
        run = session.get(InferenceRun, run_id)
        archive = returned_archive(run) if run is not None else None
        if (run is None or run.job_id != job_id or run.window_id != window_id
                or archive is None):
            raise ReceiptRecoveryError("保存的对白调用回执不完整；不会自动重发")
        request = archive.get("request")
        if (not isinstance(request, dict)
                or json.loads(run.profile_snapshot_json or "{}") != (snapshot or {})
                or fingerprint_for(request, snapshot) != run.request_fingerprint):
            raise ReceiptRecoveryError("保存的对白回执或模型配置已改变；不会自动重发")
