"""章节人物名单与全书人物路由。

归属处理前必须先完成本章人物确认，并选择第一视角主人公。
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..characters.auto_merge import auto_merge_result, confirm_auto_merge, create_auto_merge_job
from ..characters.directory import directory, edit, merge, set_color
from ..characters.service import (
    confirm_roster,
    create_roster_job,
    get_roster,
    list_book_characters_out,
)
from ..domain.characters import (
    BookCharacterOut,
    ChapterRosterOut,
    CharacterAutoMergeConfirmIn,
    CharacterAutoMergeIn,
    CharacterAutoMergeResultOut,
    CharacterColorIn,
    CharacterDirectoryOut,
    CharacterEditIn,
    CharacterMergeIn,
    RosterAnalyzeIn,
    RosterConfirmIn,
)
from ..domain.common import DataEnvelope
from ..domain.enums import JobKind
from ..domain.jobs import JobDetailOut
from ..jobs.scheduler import run_job
from ..jobs.service import job_detail
from ..storage.models import Book, BookVersion, Chapter, Job, ModelProfile
from ..storage.transactions import transaction
from .deps import get_session
from .errors import ApiError, current_request_id

router = APIRouter(tags=["characters"])


@router.get(
    "/books/{book_id}/character-directory/auto-merge",
    response_model=DataEnvelope[CharacterAutoMergeResultOut | None],
    summary="找回当前版本最近的自动合并任务",
)
def latest_auto_merge_route(
    request: Request,
    book_id: str,
    book_version_id: str | None = None,
    session: Session = Depends(get_session),
) -> DataEnvelope[CharacterAutoMergeResultOut | None]:
    version = _version_or_400(session, _book_or_404(session, book_id), book_version_id)
    job = session.scalar(
        select(Job)
        .where(
            Job.book_id == book_id,
            Job.book_version_id == version.id,
            Job.kind == JobKind.CHARACTER_MERGE,
        )
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
    )
    return DataEnvelope(
        data=auto_merge_result(session, job) if job else None,
        request_id=current_request_id(request),
    )


@router.put(
    "/books/{book_id}/character-directory/{entry_id}/color",
    response_model=DataEnvelope[CharacterDirectoryOut],
    summary="设置人物颜色或恢复自动配色（不调用模型）",
)
def set_character_color_route(request: Request, book_id: str, entry_id: str,
                              payload: CharacterColorIn) -> DataEnvelope[CharacterDirectoryOut]:
    with transaction(request.app.state.session_factory) as session:
        version = _version_or_400(session, _book_or_404(session, book_id), payload.book_version_id)
        result = set_color(session, version, entry_id, payload)
    return DataEnvelope(data=result, request_id=current_request_id(request))


@router.post(
    "/books/{book_id}/character-directory/auto-merge",
    status_code=202,
    response_model=DataEnvelope[JobDetailOut],
    summary="模型分析重复人物并生成待确认建议",
)
def auto_merge_characters_route(
    request: Request,
    book_id: str,
    payload: CharacterAutoMergeIn,
    background: BackgroundTasks,
) -> DataEnvelope[JobDetailOut]:
    factory = request.app.state.session_factory
    with transaction(factory) as session:
        book = _book_or_404(session, book_id)
        version = _version_or_400(session, book, payload.book_version_id)
        if book.active_version_id != version.id:
            raise ApiError.validation("只能自动合并当前书籍版本的人物")
        profile = session.get(ModelProfile, payload.profile_id)
        if profile is None:
            raise ApiError.not_found("模型配置不存在")
        job, created = create_auto_merge_job(session, book, version, profile, payload)
        detail = job_detail(session, job)
    if created and payload.run_now:
        background.add_task(
            run_job,
            factory,
            request.app.state.settings,
            job_id=job.id,
            credentials=request.app.state.credentials,
        )
    return DataEnvelope(data=detail, request_id=current_request_id(request))


@router.get(
    "/books/{book_id}/character-directory/auto-merge/{job_id}",
    response_model=DataEnvelope[CharacterAutoMergeResultOut],
    summary="自动合并进度和结果",
)
def auto_merge_result_route(
    request: Request,
    book_id: str,
    job_id: str,
    session: Session = Depends(get_session),
) -> DataEnvelope[CharacterAutoMergeResultOut]:
    _book_or_404(session, book_id)
    job = session.get(Job, job_id)
    if not job or job.book_id != book_id or job.kind is not JobKind.CHARACTER_MERGE:
        raise ApiError.not_found("自动合并任务不存在")
    return DataEnvelope(
        data=auto_merge_result(session, job), request_id=current_request_id(request)
    )


@router.post(
    "/books/{book_id}/character-directory/auto-merge/{job_id}/confirm",
    response_model=DataEnvelope[CharacterAutoMergeResultOut],
    summary="接受所选合并建议或放弃本次结果",
)
def confirm_auto_merge_route(
    request: Request, book_id: str, job_id: str, payload: CharacterAutoMergeConfirmIn
) -> DataEnvelope[CharacterAutoMergeResultOut]:
    with transaction(request.app.state.session_factory) as session:
        _book_or_404(session, book_id)
        job = session.get(Job, job_id)
        if not job or job.book_id != book_id or job.kind is not JobKind.CHARACTER_MERGE:
            raise ApiError.not_found("自动合并任务不存在")
        result = confirm_auto_merge(
            session, job, payload.selected_target_ids, payload.visible_from_cp,
            settings=request.app.state.settings,
        )
    return DataEnvelope(data=result, request_id=current_request_id(request))


@router.get(
    "/books/{book_id}/character-directory",
    response_model=DataEnvelope[list[CharacterDirectoryOut]],
    summary="全书人物管理目录",
)
def character_directory_route(
    request: Request,
    book_id: str,
    book_version_id: str | None = None,
    session: Session = Depends(get_session),
) -> DataEnvelope[list[CharacterDirectoryOut]]:
    version = _version_or_400(session, _book_or_404(session, book_id), book_version_id)
    return DataEnvelope(data=directory(session, version), request_id=current_request_id(request))


@router.put(
    "/books/{book_id}/character-directory/{entry_id}",
    response_model=DataEnvelope[CharacterDirectoryOut],
    summary="修改全书人物资料",
)
def edit_character_route(
    request: Request,
    book_id: str,
    entry_id: str,
    payload: CharacterEditIn,
) -> DataEnvelope[CharacterDirectoryOut]:
    with transaction(request.app.state.session_factory) as session:
        version = _version_or_400(session, _book_or_404(session, book_id), payload.book_version_id)
        result = edit(session, version, entry_id, payload)
    return DataEnvelope(data=result, request_id=current_request_id(request))


@router.post(
    "/books/{book_id}/character-directory/{entry_id}/merge",
    response_model=DataEnvelope[CharacterDirectoryOut],
    summary="合并到已有全书人物",
)
def merge_character_route(
    request: Request,
    book_id: str,
    entry_id: str,
    payload: CharacterMergeIn,
) -> DataEnvelope[CharacterDirectoryOut]:
    with transaction(request.app.state.session_factory) as session:
        version = _version_or_400(session, _book_or_404(session, book_id), payload.book_version_id)
        result = merge(session, version, entry_id, payload, settings=request.app.state.settings)
    return DataEnvelope(data=result, request_id=current_request_id(request))


def _book_or_404(session: Session, book_id: str) -> Book:
    book = session.get(Book, book_id)
    if book is None:
        raise ApiError.not_found("书籍不存在", book_id=book_id)
    return book


def _version_or_400(
    session: Session,
    book: Book,
    book_version_id: str | None,
) -> BookVersion:
    version_id = book_version_id or book.active_version_id
    if not version_id:
        raise ApiError.validation("书籍还没有可用版本", book_id=book.id)
    version = session.get(BookVersion, version_id)
    if version is None or version.book_id != book.id:
        raise ApiError.validation("book_version_id 不属于该书籍", book_version_id=version_id)
    return version


def _chapter_or_400(session: Session, version: BookVersion, chapter_id: str) -> Chapter:
    chapter = session.get(Chapter, chapter_id)
    if chapter is None or chapter.book_version_id != version.id:
        raise ApiError.validation("chapter_id 不属于该书籍版本", chapter_id=chapter_id)
    return chapter


@router.get(
    "/books/{book_id}/characters",
    response_model=DataEnvelope[list[BookCharacterOut]],
    summary="全书人物表",
)
def list_book_characters_route(
    request: Request,
    book_id: str,
    book_version_id: str | None = None,
    session: Session = Depends(get_session),
) -> DataEnvelope[list[BookCharacterOut]]:
    book = _book_or_404(session, book_id)
    version = _version_or_400(session, book, book_version_id)
    return DataEnvelope(
        data=list_book_characters_out(session, version),
        request_id=current_request_id(request),
    )


@router.post(
    "/books/{book_id}/chapters/{chapter_id}/character-roster/analyze",
    status_code=202,
    response_model=DataEnvelope[JobDetailOut],
    summary="分析本章人物",
)
def analyze_character_roster_route(
    request: Request,
    book_id: str,
    chapter_id: str,
    payload: RosterAnalyzeIn,
    background: BackgroundTasks,
) -> DataEnvelope[JobDetailOut]:
    settings = request.app.state.settings
    credentials = request.app.state.credentials
    factory = request.app.state.session_factory

    with transaction(factory) as session:
        book = _book_or_404(session, book_id)
        version = _version_or_400(session, book, payload.book_version_id)
        chapter = _chapter_or_400(session, version, chapter_id)
        profile = session.get(ModelProfile, payload.profile_id)
        if profile is None:
            raise ApiError.not_found("模型配置不存在", profile_id=payload.profile_id)
        job, created = create_roster_job(
            session,
            book=book,
            version=version,
            chapter=chapter,
            profile=profile,
            idempotency_key=payload.idempotency_key,
            max_input_tokens=payload.max_input_tokens,
            allow_overwrite_manual=payload.allow_overwrite_manual,
            inference_options=(
                payload.inference_options.model_dump() if payload.inference_options else None
            ),
        )
        detail = job_detail(session, job)

    if payload.run_now and created:
        background.add_task(
            run_job,
            factory,
            settings,
            job_id=detail.id,
            credentials=credentials,
        )
    return DataEnvelope(data=detail, request_id=current_request_id(request))


@router.get(
    "/books/{book_id}/chapters/{chapter_id}/character-roster",
    response_model=DataEnvelope[ChapterRosterOut],
    summary="读取本章人物名单",
)
def get_character_roster_route(
    request: Request,
    book_id: str,
    chapter_id: str,
    book_version_id: str | None = None,
    session: Session = Depends(get_session),
) -> DataEnvelope[ChapterRosterOut]:
    book = _book_or_404(session, book_id)
    version = _version_or_400(session, book, book_version_id)
    chapter = _chapter_or_400(session, version, chapter_id)
    return DataEnvelope(
        data=get_roster(session, version, chapter),
        request_id=current_request_id(request),
    )


@router.put(
    "/books/{book_id}/chapters/{chapter_id}/character-roster",
    response_model=DataEnvelope[ChapterRosterOut],
    summary="确认本章人物与第一视角主人公",
)
def confirm_character_roster_route(
    request: Request,
    book_id: str,
    chapter_id: str,
    payload: RosterConfirmIn,
) -> DataEnvelope[ChapterRosterOut]:
    factory = request.app.state.session_factory
    with transaction(factory) as session:
        book = _book_or_404(session, book_id)
        version = _version_or_400(session, book, payload.book_version_id)
        chapter = _chapter_or_400(session, version, chapter_id)
        roster = confirm_roster(session, version=version, chapter=chapter, payload=payload)
    return DataEnvelope(data=roster, request_id=current_request_id(request))
