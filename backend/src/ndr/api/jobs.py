"""任务路由：创建、状态、暂停/恢复、对账、估算与用量。

- `POST /api/jobs`：幂等创建（同 key 同摘要 → 返回既有任务；摘要不同 → 409）。
- `GET /api/jobs/{id}`：状态 + 窗口 + usage + 剩余窗口。
- `POST /api/jobs/{id}/pause|resume|run|reconcile`。
- `POST /api/books/{id}/estimates` 与 `GET /api/books/{id}/usage` 在 `api/estimates.py`
  （同样挂 `/api`）。
"""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy import Integer, func, select, tuple_
from sqlalchemy.orm import Session

from ..domain.common import CursorPage, DataEnvelope
from ..domain.enums import CredentialMode, JobKind, JobState
from ..domain.jobs import (
    JobCreate,
    JobDetailOut,
    JobRunOut,
    ReconcileIn,
    TaskQueueItemOut,
)
from ..domain.recovery import JobRecoveryOut
from ..jobs.scheduler import reconcile_job, run_job
from ..jobs.service import (
    create_inference_job,
    credential_reference,
    job_detail,
    job_windows,
)
from ..recovery.service import job_recovery, profile_snapshot_of
from ..storage.models import Book, BookVersion, Chapter, Job, JobWindow, ModelProfile, Quote
from ..storage.transactions import admission_transaction, transaction
from .deps import get_session
from .errors import ApiError, current_request_id
from .pagination import decode_cursor, encode_cursor

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/queue", response_model=DataEnvelope[CursorPage[TaskQueueItemOut]],
            summary="跨书籍任务队列（分页摘要，不读取调用归档）")
def task_queue_route(request: Request, active_only: bool = True, book_id: str | None = None,
                     limit: int = Query(default=50, ge=1, le=100), cursor: str | None = None,
                     session: Session = Depends(get_session)):
    statement = select(Job.id, Job.kind, Job.state, Job.book_id, Book.title,
                       func.json_extract(Job.range_json, "$.chapter_id").label("chapter_id"),
                       func.json_extract(Job.range_json, "$.start_cp").label("start_cp"),
                       func.json_extract(Job.range_json, "$.end_cp").label("end_cp"),
                       func.json_extract(Job.range_json, "$.selected_window_ids")
                       .label("selected_window_ids"),
                       Job.progress_json, Job.last_error, Job.created_at).outerjoin(
                           Book, Book.id == Job.book_id)
    if active_only:
        statement = statement.where(Job.state.in_(
            (JobState.QUEUED, JobState.RUNNING, JobState.PAUSING)))
    if book_id:
        statement = statement.where(Job.book_id == book_id)
    if cursor:
        parts = decode_cursor(cursor)
        try:
            created = datetime.fromisoformat(str(parts[0]))
            if len(parts) != 2 or created.tzinfo is None:
                raise ValueError("invalid cursor")
        except (ValueError, IndexError) as exc:
            raise ApiError.validation("任务列表游标无效，请重新读取") from exc
        statement = statement.where(tuple_(Job.created_at, Job.id) < (created, str(parts[1])))
    rows = list(session.execute(statement.order_by(Job.created_at.desc(), Job.id.desc())
                                .limit(limit + 1)))
    has_more = len(rows) > limit
    rows = rows[:limit]
    chapter_ids = {row.chapter_id for row in rows if row.chapter_id}
    titles = dict(session.execute(select(Chapter.id, Chapter.title).where(
        Chapter.id.in_(chapter_ids))).all()) if chapter_ids else {}
    window_counts = {row.job_id: (row.total, row.done) for row in session.execute(
        select(JobWindow.job_id, func.count().label("total"),
               func.sum((JobWindow.state == JobState.COMPLETED).cast(Integer))
               .label("done")).where(JobWindow.job_id.in_([row.id for row in rows]))
        .group_by(JobWindow.job_id))} if rows else {}
    items = [TaskQueueItemOut(id=row.id, kind=row.kind, state=row.state, book_id=row.book_id,
                             book_title=row.title or "", chapter_id=row.chapter_id,
                             chapter_title=titles.get(row.chapter_id, ""),
                             start_cp=row.start_cp, end_cp=row.end_cp,
                             selected_window_ids=(json.loads(row.selected_window_ids)
                                                  if row.selected_window_ids else None),
                             progress=json.loads(row.progress_json or "{}"),
                             windows_total=window_counts.get(row.id, (0, 0))[0],
                             windows_done=window_counts.get(row.id, (0, 0))[1] or 0,
                             last_error=row.last_error, created_at=row.created_at.isoformat())
             for row in rows]
    return DataEnvelope(data=CursorPage(items=items, next_cursor=encode_cursor(
        [rows[-1].created_at.isoformat(), rows[-1].id]) if has_more else None),
        request_id=current_request_id(request))


@router.get(
    "/recent", response_model=DataEnvelope[list[JobDetailOut]], summary="找回书籍或对白最近的任务"
)
def recent_jobs_route(
    request: Request,
    book_id: str | None = None,
    book_version_id: str | None = None,
    chapter_id: str | None = None,
    quote_id: str | None = None,
    kind: JobKind | None = None,
    idempotency_key: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    session: Session = Depends(get_session),
) -> DataEnvelope[list[JobDetailOut]]:
    if quote_id:
        quote = session.get(Quote, quote_id)
        if not quote:
            raise ApiError.not_found("对白不存在")
        version = session.get(BookVersion, quote.book_version_id)
        if book_id and book_id != version.book_id:
            raise ApiError.validation("对白不属于该书籍")
        if book_version_id and book_version_id != version.id:
            raise ApiError.validation("对白不属于该版本")
        book_id, book_version_id = version.book_id, version.id
    if not book_id:
        raise ApiError.validation("请指定书籍或对白")
    book = session.get(Book, book_id)
    if not book:
        raise ApiError.not_found("书籍不存在")
    version = session.get(BookVersion, book_version_id or book.active_version_id)
    if not version or version.book_id != book.id:
        raise ApiError.validation("书籍版本无效")
    statement = select(Job).where(Job.book_id == book.id, Job.book_version_id == version.id)
    if kind:
        statement = statement.where(Job.kind == kind)
    if idempotency_key:
        statement = statement.where(Job.idempotency_key == idempotency_key)
    if chapter_id:
        statement = statement.where(func.json_extract(Job.range_json, "$.chapter_id") == chapter_id)
    if quote_id:
        statement = statement.where(
            func.json_extract(Job.range_json, "$.target_quote_id") == quote_id
        )
    jobs = session.scalars(statement.order_by(Job.created_at.desc(), Job.id.desc()).limit(limit))
    return DataEnvelope(
        data=[job_detail(session, job) for job in jobs], request_id=current_request_id(request)
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

    with admission_transaction(factory) as session:
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
            range_payload={
                **dict(payload.range),
                "selected_window_ids": payload.selected_window_ids,
                "force_reprocess": payload.force_reprocess,
            },
            budget=payload.budget.model_dump(),
            idempotency_key=payload.idempotency_key,
            reading_mode=payload.reading_mode,
            visible_horizon_cp=payload.visible_horizon_cp,
            kind=payload.kind,
            inference_options=(
                payload.inference_options.model_dump() if payload.inference_options else None
            ),
        )
        detail = job_detail(session, job)

    if payload.run_now and created:
        background.add_task(run_job, factory, settings, job_id=detail.id, credentials=credentials)
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
    return DataEnvelope(data=job_detail(session, job), request_id=current_request_id(request))


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
        detail = job_detail(session, job)
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
def reconcile_job_route(request: Request, job_id: str, payload: ReconcileIn) -> DataEnvelope[dict]:
    factory = request.app.state.session_factory
    with transaction(factory) as session:
        job = session.get(Job, job_id)
        if job is None:
            raise ApiError.not_found("任务不存在", job_id=job_id)
        result = reconcile_job(session, job, action=payload.action)
    return DataEnvelope(data=result, request_id=current_request_id(request))


@router.get(
    "/{job_id}/recovery",
    response_model=DataEnvelope[JobRecoveryOut],
    summary="非完成状态的恢复动作（含是否付费与是否缺凭据）",
)
def job_recovery_route(
    request: Request,
    job_id: str,
    session: Session = Depends(get_session),
) -> DataEnvelope[JobRecoveryOut]:
    """把任务状态翻译成可执行动作：读接口，不调用模型、不改任务。"""

    job = session.get(Job, job_id)
    if job is None:
        raise ApiError.not_found("任务不存在", job_id=job_id)
    credentials = request.app.state.credentials
    snapshot = profile_snapshot_of(job)
    mode = str(snapshot.get("credential_mode", CredentialMode.NONE.value))
    has_credential: bool | None = None
    if mode != CredentialMode.NONE.value:
        profile_id = snapshot.get("profile_id")
        profile = session.get(ModelProfile, profile_id) if profile_id else None
        ref = credential_reference(profile) if profile is not None else None
        if ref:
            has_credential = credentials.has(mode=CredentialMode(mode), ref=ref)
    data = job_recovery(session, job, has_credential=has_credential)
    return DataEnvelope(data=data, request_id=current_request_id(request))
