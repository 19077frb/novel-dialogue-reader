"""Reject overlapping active work even when model settings differ."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.errors import ApiError
from ..domain.enums import ErrorCode, JobKind, JobState
from ..storage.models import Job


def guard_active_target(session: Session, version_id: str, kind: JobKind,
                        target: dict[str, Any]) -> None:
    kinds = ((JobKind.CHARACTER_ROSTER,) if kind is JobKind.CHARACTER_ROSTER else
             (JobKind.INFERENCE, JobKind.RECHECK, JobKind.RECOMPUTE))
    rows = session.execute(select(Job.id, Job.range_json).where(
        Job.book_version_id == version_id, Job.kind.in_(kinds),
        Job.state.in_((JobState.QUEUED, JobState.RUNNING, JobState.PAUSING,
                       JobState.NEEDS_RECONCILIATION))))
    for job_id, raw in rows:
        other = json.loads(raw or "{}")
        if kind is JobKind.CHARACTER_ROSTER:
            overlaps = target.get("chapter_id") == other.get("chapter_id")
        else:
            ours, theirs = target.get("selected_window_ids"), other.get("selected_window_ids")
            # Window IDs are stable within a version; two explicit disjoint selections
            # are safe even when both requests carry the whole chapter's bounds.
            if ours and theirs:
                overlaps = bool(set(ours) & set(theirs))
            else:
                overlaps = (int(target.get("start_cp", 0)) < int(other.get("end_cp", 0))
                            and int(other.get("start_cp", 0)) < int(target.get("end_cp", 0)))
        if overlaps:
            raise ApiError(
                ErrorCode.RESOURCE_CONFLICT,
                "该窗口已有任务在任务队列中等待执行或正在执行；结果未知时请先核实原任务",
                details={"job_id": job_id},
            )
