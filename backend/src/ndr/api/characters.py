"""章节人物名单与全书人物路由。

归属处理前必须先完成本章人物确认，并选择第一视角主人公。
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy.orm import Session

from ..characters.directory import directory, edit, merge
from ..characters.service import (
    confirm_roster,
    create_roster_job,
    get_roster,
    list_book_characters_out,
)
from ..domain.characters import (
    BookCharacterOut,
    ChapterRosterOut,
    CharacterDirectoryOut,
    CharacterEditIn,
    CharacterMergeIn,
    RosterAnalyzeIn,
    RosterConfirmIn,
)
from ..domain.common import DataEnvelope
from ..domain.jobs import JobDetailOut
from ..jobs.scheduler import run_job
from ..jobs.service import job_detail
from ..storage.models import Book, BookVersion, Chapter, ModelProfile
from ..storage.transactions import transaction
from .deps import get_session
from .errors import ApiError, current_request_id

router = APIRouter(tags=["characters"])


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
        result = merge(session, version, entry_id, payload)
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
