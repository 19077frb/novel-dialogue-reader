"""Shared, explicit policy for completed execution material; business results remain."""

import json

from sqlalchemy import func, select, update
from sqlalchemy.orm.attributes import set_committed_value

from ..domain.enums import InferenceRunState, JobKind, JobState
from .models import InferenceRun, Job, JobWindow

DIALOGUE_KINDS = {JobKind.INFERENCE, JobKind.RECHECK, JobKind.RECOMPUTE}


def completed_job_eligible(session, job):
    if job.state is not JobState.COMPLETED:
        return False
    windows = dict(session.execute(select(JobWindow.window_id, JobWindow.state).where(
        JobWindow.job_id == job.id,
    )).all())
    if any(state is not JobState.COMPLETED for state in windows.values()):
        return False
    if session.scalar(select(InferenceRun.id).where(
        InferenceRun.job_id == job.id,
        InferenceRun.state.in_([InferenceRunState.PREPARED, InferenceRunState.DISPATCHED]),
    ).limit(1)):
        return False
    try:
        checkpoint = json.loads(job.checkpoint_json or "{}")
        receipts = checkpoint.get("dialogue_receipts", {})
        return isinstance(receipts, dict) and all(
            windows.get(window_id) is JobState.COMPLETED for window_id in receipts
        )
    except (ValueError, TypeError, AttributeError):
        return False


def run_archive_eligible(run):
    if (run.state is not InferenceRunState.SUCCEEDED or run.error_code is not None
            or run.usage_json is None):
        return False
    try:
        usage = json.loads(run.usage_json)
        total = usage.get("total_tokens")
        return (not usage.get("unknown") and type(total) is int and total >= 0)
    except (ValueError, TypeError, AttributeError):
        return False


def plan_completed_job_retention(session, job, *, keep_call_details=False):
    plan = {"drop_scene_state": False, "archive_run_ids": [],
            "checkpoint_bytes": 0, "archive_bytes": 0}
    if not completed_job_eligible(session, job):
        return plan
    checkpoint = json.loads(job.checkpoint_json or "{}")
    if job.kind in DIALOGUE_KINDS and "scene_state" in checkpoint:
        reduced = {key: value for key, value in checkpoint.items() if key != "scene_state"}
        plan["drop_scene_state"] = True
        plan["checkpoint_bytes"] = max(0, len(job.checkpoint_json.encode("utf-8")) - len(
            json.dumps(reduced, ensure_ascii=False).encode("utf-8"),
        ))
    if not keep_call_details:
        runs = session.execute(select(
            InferenceRun.id, InferenceRun.state, InferenceRun.error_code,
            InferenceRun.usage_json, func.length(InferenceRun.call_archive).label("archive_bytes"),
        ).where(InferenceRun.job_id == job.id, InferenceRun.call_archive.is_not(None)))
        for run in runs:
            if run_archive_eligible(run):
                plan["archive_run_ids"].append(run.id)
                plan["archive_bytes"] += run.archive_bytes
    return plan


def apply_completed_job_retention(session, job, *, keep_call_details=False):
    """Apply in the caller's success transaction, preserving all metadata timestamps."""
    session.flush()
    plan = plan_completed_job_retention(session, job, keep_call_details=keep_call_details)
    if plan["drop_scene_state"]:
        checkpoint = json.loads(job.checkpoint_json)
        del checkpoint["scene_state"]
        reduced = json.dumps(checkpoint, ensure_ascii=False)
        session.execute(update(Job).where(Job.id == job.id).values(
            checkpoint_json=reduced, updated_at=Job.updated_at,
        ), execution_options={"synchronize_session": False})
        set_committed_value(job, "checkpoint_json", reduced)
    for offset in range(0, len(plan["archive_run_ids"]), 500):
        batch = plan["archive_run_ids"][offset:offset + 500]
        session.execute(update(InferenceRun).where(
            InferenceRun.id.in_(batch),
        ).values(call_archive=None, updated_at=InferenceRun.updated_at),
            execution_options={"synchronize_session": "fetch"})
    return plan
