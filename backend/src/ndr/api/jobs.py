"""任务路由（T10）：创建、状态、暂停/恢复、对账、估算与用量。

- `POST /api/jobs`：幂等创建（同 key 同摘要 → 返回既有任务；摘要不同 → 409）。
- `GET /api/jobs/{id}`：状态 + 窗口 + usage + 剩余窗口。
- `POST /api/jobs/{id}/pause|resume|run|reconcile`。
- `POST /api/books/{id}/estimates` 与 `GET /api/books/{id}/usage` 在 `api/estimates.py`
  （同样挂 `/api`）。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.common import DataEnvelope
from ..domain.enums import JobKind, JobState
from ..domain.jobs import (
    JobCreate,
    JobDetailOut,
    JobRunOut,
    JobWindowOut,
    ReconcileIn,
)
from ..jobs.scheduler import reconcile_job, run_job
from ..jobs.service import (
    create_inference_job,
    job_windows,
    spent_tokens,
)
from ..storage.models import Book, BookVersion, InferenceRun, Job, JobWindow, ModelProfile
from ..storage.transactions import transaction
from .deps import get_session
from .errors import ApiError, current_request_id

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _window_out(row: JobWindow, attempts: int = 0) -> JobWindowOut:
    try:
        targets = json.loads(row.target_ids_json or "[]")
    except json.JSONDecodeError:
        targets = []
    return JobWindowOut(
        window_id=row.window_id,
        state=row.state,
        target_count=len(targets) if isinstance(targets, list) else 0,
        dependency_hash=row.dependency_hash,
        attempts=attempts,
    )


def _job_detail(session: Session, job: Job) -> JobDetailOut:
    rows = job_windows(session, job.id)
    attempts_by_window: dict[str, int] = {}
    for run in session.execute(
        select(InferenceRun).where(InferenceRun.job_id == job.id)
    ).scalars():
        if run.window_id:
            attempts_by_window[run.window_id] = attempts_by_window.get(run.window_id, 0) + 1
    windows = [_window_out(row, attempts_by_window.get(row.window_id, 0)) for row in rows]
    usage = spent_tokens(session, job.id)
    runs = list(
        session.execute(select(InferenceRun).where(InferenceRun.job_id == job.id)).scalars()
    )
    unknown_runs = sum(1 for run in runs if run.usage_json is None)
    return JobDetailOut(
        id=job.id,
        kind=job.kind,
        purpose=job.purpose,
        state=job.state,
        book_id=job.book_id,
        book_version_id=job.book_version_id,
        progress=json.loads(job.progress_json) if job.progress_json else None,
        checkpoint=json.loads(job.checkpoint_json) if job.checkpoint_json else None,
        last_error=job.last_error,
        windows=windows,
        remaining_windows=sum(
            1 for row in rows if row.state in {JobState.QUEUED, JobState.NEEDS_RECONCILIATION}
        ),
        windows_total=len(rows),
        calls=len(runs),
        cached_windows=int(
            (
                json.loads(job.progress_json).get("cached_windows", 0)
                if job.progress_json
                else 0
            )
            or 0
        ),
        unknown_usage_runs=unknown_runs,
        usage=usage,
        created_at=job.created_at.isoformat(),
        updated_at=job.updated_at.isoformat(),
    )


@router.post("", status_code=202, response_model=DataEnvelope[JobDetailOut], summary="创建任务")
def create_job_route(
    request: Request,
    payload: JobCreate,
    background: BackgroundTasks,
) -> DataEnvelope[JobDetailOut]:
    settings = request.app.state.settings
    credentials = request.app.state.credentials
    factory = request.app.state.session_factory

    with transaction(factory) as session:
        book = session.get(Book, payload.book_id)
        if book is None:
            raise ApiError.not_found("书籍不存在", book_id=payload.book_id)
        version_id = payload.book_version_id or book.active_version_id
        if not version_id:
            raise ApiError.validation("书籍还没有可用版本", book_id=payload.book_id)
        version = session.get(BookVersion, version_id)
        if version is None or version.book_id != book.id:
            raise ApiError.validation("book_version_id 不属于该书籍", book_version_id=version_id)

        profile = None
        if payload.profile_id:
            profile = session.get(ModelProfile, payload.profile_id)
            if profile is None:
                raise ApiError.not_found("模型配置不存在", profile_id=payload.profile_id)

        if payload.kind not in {JobKind.INFERENCE, JobKind.RECOMPUTE, JobKind.RECHECK}:
            raise ApiError.validation("该任务类型尚未实现", kind=payload.kind.value)

        job, created = create_inference_job(
            session,
            book=book,
            version=version,
            profile=profile,
            purpose=payload.mode,
            range_payload=dict(payload.range),
            budget=payload.budget.model_dump(),
            idempotency_key=payload.idempotency_key,
            reading_mode=payload.reading_mode,
            visible_horizon_cp=payload.visible_horizon_cp,
            kind=payload.kind,
        )
        detail = _job_detail(session, job)

    if payload.run_now and created:
        background.add_task(
            run_job, factory, settings, job_id=detail.id, credentials=credentials
        )
    return DataEnvelope(data=detail, request_id=current_request_id(request))


@router.get("/{job_id}", response_model=DataEnvelope[JobDetailOut], summary="任务状态与用量")
def get_job_route(
    request: Request,
    job_id: str,
    session: Session = Depends(get_session),
) -> DataEnvelope[JobDetailOut]:
    job = session.get(Job, job_id)
    if job is None:
        raise ApiError.not_found("任务不存在", job_id=job_id)
    return DataEnvelope(data=_job_detail(session, job), request_id=current_request_id(request))


@router.post("/{job_id}/pause", status_code=202, response_model=DataEnvelope[JobDetailOut])
def pause_job_route(request: Request, job_id: str) -> DataEnvelope[JobDetailOut]:
    factory = request.app.state.session_factory
    with transaction(factory) as session:
        job = session.get(Job, job_id)
        if job is None:
            raise ApiError.not_found("任务不存在", job_id=job_id)
        if job.state is JobState.RUNNING:
            job.state = JobState.PAUSING  # 当前窗口结束后进入 PAUSED，不承诺远程请求已停止计费
        elif job.state in {JobState.QUEUED, JobState.PARTIAL}:
            job.state = JobState.PAUSED
        detail = _job_detail(session, job)
    return DataEnvelope(data=detail, request_id=current_request_id(request))


@router.post("/{job_id}/resume", status_code=202, response_model=DataEnvelope[JobRunOut])
def resume_job_route(
    request: Request, job_id: str, background: BackgroundTasks
) -> DataEnvelope[JobRunOut]:
    factory = request.app.state.session_factory
    settings = request.app.state.settings
    credentials = request.app.state.credentials
    with transaction(factory) as session:
        job = session.get(Job, job_id)
        if job is None:
            raise ApiError.not_found("任务不存在", job_id=job_id)
        if job.state in {JobState.PAUSED, JobState.PARTIAL, JobState.BUDGET_EXHAUSTED}:
            job.state = JobState.QUEUED
    # 后台继续跑；这里只返回当前快照，真实进度由 GET /api/jobs/{id} 轮询
    background.add_task(run_job, factory, settings, job_id=job_id, credentials=credentials)
    with transaction(factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        rows = job_windows(session, job_id)
        snapshot = JobRunOut(
            job_id=job_id,
            state=job.state,
            windows_total=len(rows),
            windows_done=sum(1 for row in rows if row.state is JobState.COMPLETED),
            cached_windows=0,
            calls=0,
            unknown_runs=0,
        )
    return DataEnvelope(data=snapshot, request_id=current_request_id(request))


@router.post(
    "/{job_id}/run",
    response_model=DataEnvelope[JobRunOut],
    summary="立即执行（测试/手动）",
)
def run_job_route(request: Request, job_id: str) -> DataEnvelope[JobRunOut]:
    factory = request.app.state.session_factory
    settings = request.app.state.settings
    credentials = request.app.state.credentials
    outcome = run_job(factory, settings, job_id=job_id, credentials=credentials)
    return DataEnvelope(data=JobRunOut(**outcome.as_dict()), request_id=current_request_id(request))


@router.post("/{job_id}/reconcile", response_model=DataEnvelope[dict])
def reconcile_job_route(
    request: Request, job_id: str, payload: ReconcileIn
) -> DataEnvelope[dict]:
    factory = request.app.state.session_factory
    with transaction(factory) as session:
        job = session.get(Job, job_id)
        if job is None:
            raise ApiError.not_found("任务不存在", job_id=job_id)
        result = reconcile_job(session, job, action=payload.action)
    return DataEnvelope(data=result, request_id=current_request_id(request))
