"""Reject overlapping dialogue targets independently of window planning settings."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.errors import ApiError
from ..config import Settings
from ..context.budget import policy_for_version
from ..context.service import plan_range
from ..domain.enums import ErrorCode, JobKind, JobState, ReadingMode
from ..storage.models import BookVersion, Job, JobWindow, Quote


def _object(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _ids(raw: str) -> frozenset[str] | None:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return None
    return frozenset(value)


class _Targets:
    """Per-admission read cache; never writes windows or calls a provider."""

    def __init__(self, session: Session, version: BookVersion, settings: Settings | None,
                 windows: dict[str, dict[str, frozenset[str]]]):
        self.session = session
        self.version = version
        self.settings = settings
        self.windows = windows
        self.ranges: dict[tuple[int, int], frozenset[str]] = {}
        self.plans: dict[tuple[Any, ...], dict[str, frozenset[str]] | None] = {}

    def bounds(self, target: dict[str, Any]) -> tuple[int, int]:
        start = target.get("start_cp", 0)
        end = target.get("end_cp")
        end = self.version.canonical_length_cp if end is None else end
        # A damaged legacy scope must not silently free its targets. Chapter IDs
        # do not narrow scheduler._plan's default whole-version range either.
        if type(start) is not int or type(end) is not int or not 0 <= start <= end:
            return 0, self.version.canonical_length_cp
        return start, end

    def range_ids(self, target: dict[str, Any]) -> frozenset[str]:
        bounds = self.bounds(target)
        if bounds not in self.ranges:
            self.ranges[bounds] = frozenset(self.session.scalars(select(Quote.id).where(
                Quote.book_version_id == self.version.id,
                Quote.start_cp >= bounds[0], Quote.start_cp < bounds[1],
                Quote.nesting_depth == 0,
            )))
        return self.ranges[bounds]

    def planned(self, target: dict[str, Any]) -> dict[str, frozenset[str]] | None:
        if self.settings is None:
            return None
        start, end = self.bounds(target)
        try:
            mode = ReadingMode(target.get("reading_mode", ReadingMode.INITIAL.value))
            horizon = target.get("visible_horizon_cp")
            if horizon is not None and type(horizon) is not int:
                return None
            policy = policy_for_version(target.get("context_policy"))
            # Different selections of one plan should only scan its material once.
            key = (start, end, mode, horizon, policy)
            if key not in self.plans:
                self.plans[key] = None
                plan = plan_range(self.session, self.settings, self.version,
                                  start_cp=start, end_cp=end, reading_mode=mode,
                                  visible_horizon_cp=horizon, policy=policy)
                self.plans[key] = {window.window_id: frozenset(window.target_quote_ids)
                                   for window in plan.windows}
            return self.plans[key]
        except (ApiError, OSError, TypeError, ValueError):
            # Old policies/files/IDs may no longer be reconstructable. Their
            # whole original range remains occupied rather than assuming empty.
            return None

    def resolve(self, target: dict[str, Any], job_id: str | None = None) -> frozenset[str]:
        selected = target.get("selected_window_ids")
        if not isinstance(selected, list):
            # Existing windows may only cover part of a queued/retried range.
            return self.range_ids(target)
        if not selected:
            return frozenset()
        if any(not isinstance(item, str) for item in selected):
            return self.range_ids(target)
        selected_ids = set(selected)
        stored = self.windows.get(job_id or "", {})
        known = frozenset().union(*(stored[item] for item in selected_ids if item in stored))
        if selected_ids <= stored.keys():
            return known
        plan = self.planned(target)
        if plan is not None and selected_ids <= plan.keys() | stored.keys():
            return known.union(*(plan[item] for item in selected_ids if item in plan))
        return known | self.range_ids(target)


def guard_active_target(session: Session, version_id: str, kind: JobKind,
                        target: dict[str, Any], *, settings: Settings | None = None,
                        exclude_job_id: str | None = None) -> None:
    kinds = ((JobKind.CHARACTER_ROSTER,) if kind is JobKind.CHARACTER_ROSTER else
             (JobKind.INFERENCE, JobKind.RECHECK, JobKind.RECOMPUTE))
    statement = select(Job.id, Job.range_json).where(
        Job.book_version_id == version_id, Job.kind.in_(kinds),
        Job.state.in_((JobState.QUEUED, JobState.RUNNING, JobState.PAUSING,
                       JobState.NEEDS_RECONCILIATION)))
    if exclude_job_id is not None:
        statement = statement.where(Job.id != exclude_job_id)
    rows = [(job_id, _object(raw)) for job_id, raw in session.execute(statement)]
    if not rows:
        return
    targets = None
    if kind is not JobKind.CHARACTER_ROSTER:
        version = session.get(BookVersion, version_id)
        if version is None:
            raise ApiError.not_found("书籍版本不存在", book_version_id=version_id)
        windows: dict[str, dict[str, frozenset[str]]] = {}
        window_jobs = [job_id for job_id, _ in rows]
        if exclude_job_id is not None:
            window_jobs.append(exclude_job_id)
        for job_id, window_id, raw in session.execute(select(
            JobWindow.job_id, JobWindow.window_id, JobWindow.target_ids_json,
        ).where(JobWindow.job_id.in_(window_jobs))):
            ids = _ids(raw)
            if ids is not None:
                windows.setdefault(job_id, {})[window_id] = ids
        targets = _Targets(session, version, settings, windows)
    ours = None
    for job_id, other in rows:
        if targets is None:
            overlaps = target.get("chapter_id") == other.get("chapter_id")
        else:
            # Half-open target start ranges can cheaply rule out unrelated work.
            start, end = targets.bounds(target)
            other_start, other_end = targets.bounds(other)
            if end <= other_start or other_end <= start:
                continue
            if ours is None:
                ours = targets.resolve(target, exclude_job_id)
                if not ours:
                    return
            overlaps = not ours.isdisjoint(targets.resolve(other, job_id))
        if overlaps:
            raise ApiError(
                ErrorCode.RESOURCE_CONFLICT,
                "该范围的对白已有任务在任务队列中等待执行或正在执行；结果未知时请先核实原任务",
                details={"job_id": job_id},
            )
