"""Local admission checks for explicit recovery, inside the caller's write transaction."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from ..api.errors import ApiError
from ..config import Settings
from ..domain.enums import ErrorCode, JobKind, JobState
from ..storage.models import Job
from .dialogue_lifecycle import DIALOGUE_KINDS, dialogue_job_is_active
from .local_finalization import settlement_pending
from .occupancy import guard_active_target
from .roster import roster_job_is_active


def guard_job_resumption(session: Session, settings: Settings, job: Job) -> None:
    """Do not race a finishing worker or another owner of the task's targets."""
    active = settlement_pending(job.id)
    if job.kind in DIALOGUE_KINDS:
        active = dialogue_job_is_active(job.id)
    elif job.kind is JobKind.CHARACTER_ROSTER:
        active = roster_job_is_active(job.id)
    if active:
        raise ApiError(
            ErrorCode.RESOURCE_CONFLICT,
            "原任务仍在完成本地保存，请等待保存结束后再恢复",
            details={"job_id": job.id},
        )
    if job.kind in DIALOGUE_KINDS or job.kind is JobKind.CHARACTER_ROSTER:
        if not job.book_version_id:
            raise ApiError.validation("任务缺少书籍版本", job_id=job.id)
        try:
            target = json.loads(job.range_json or "{}")
        except (TypeError, ValueError):
            target = {}
        if not isinstance(target, dict):
            target = {}
        guard_active_target(
            session, job.book_version_id, job.kind, target,
            settings=settings, exclude_job_id=job.id,
        )


def admit_job_resume(session: Session, settings: Settings, job: Job) -> bool:
    """Return whether this transaction actually resumes the task for dispatch."""
    checkpoint = json.loads(job.checkpoint_json or "{}")
    replacement = checkpoint.get("superseded_by")
    if replacement:
        raise ApiError(
            ErrorCode.RESOURCE_CONFLICT,
            "该人物任务已由新的本章任务接替，请查看新任务",
            details={"job_id": replacement},
        )
    recover_roster_receipt = False
    if job.kind is JobKind.CHARACTER_ROSTER and job.state in {
        JobState.FAILED, JobState.NEEDS_RECONCILIATION, JobState.PAUSED,
        JobState.PARTIAL, JobState.BUDGET_EXHAUSTED,
    }:
        from .roster_receipts import has_returned_result

        recover_roster_receipt = (
            not json.loads(job.range_json or "{}").get("roster_repair_protocol")
            and has_returned_result(session, job)
        )
    recover_dialogue_receipt = (
        job.state is JobState.FAILED and bool(checkpoint.get("dialogue_receipts"))
    )
    if not (recover_roster_receipt or recover_dialogue_receipt or job.state in {
        JobState.PAUSED, JobState.PARTIAL, JobState.BUDGET_EXHAUSTED,
    }):
        return False
    guard_job_resumption(session, settings, job)
    if recover_roster_receipt:
        checkpoint.pop("roster_retry_after_run_id", None)
        job.checkpoint_json = json.dumps(checkpoint, ensure_ascii=False)
    job.state = JobState.QUEUED
    return True
