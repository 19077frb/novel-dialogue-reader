"""候选引语、Gap 与原文定位路由（T05）。

- `GET /api/books/{id}/quotes`：候选列表（可按章节过滤、cursor 分页）。
- `GET /api/books/{id}/gaps`：候选之间的叙述间隔。
- `GET /api/books/{id}/locate`：把码点范围映射回章节/节点/源文档（纯读，不调用模型）。
- `POST /api/books/{id}/quotes/scan`：重新扫描候选（派生数据；已有用户标注时返回 409）。
- `GET /api/quotes/{id}`：候选详情 + 上下文 + 前置 Gap。

这些接口**只返回扫描器提出的候选**，不含任何说话人判断。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from ..domain.common import CursorPage, DataEnvelope
from ..domain.enums import ErrorCode, JobKind, JobState
from ..domain.quotes import GapOut, LocateOut, QuoteDetailOut, QuoteOut, ScanResultOut
from ..ingest.query import active_version, get_book_or_404
from ..quotes.service import (
    ScanConflict,
    get_quote_detail,
    list_gaps,
    list_quotes,
    locate,
    scan_and_store,
)
from ..storage.models import BookVersion, Job, Quote
from ..storage.transactions import transaction
from .deps import get_session
from .errors import ApiError, current_request_id
from .pagination import parse_limit

router = APIRouter(prefix="/books", tags=["quotes"])
quote_router = APIRouter(prefix="/quotes", tags=["quotes"])


def _active_version_or_409(session: Session, book_id: str) -> tuple:  # noqa: ANN401
    book = get_book_or_404(session, book_id)
    version = active_version(session, book)
    if version is None:
        raise ApiError(
            ErrorCode.NOT_FOUND,
            "书籍还没有可用版本",
            details={"book_id": book_id},
            status_code=409,
        )
    return book, version


@router.get(
    "/{book_id}/quotes",
    response_model=DataEnvelope[CursorPage[QuoteOut]],
    summary="候选引语列表（不含说话人判断）",
)
def list_quotes_route(
    request: Request,
    book_id: str,
    chapter_id: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=500),
    cursor: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> DataEnvelope[CursorPage[QuoteOut]]:
    settings = request.app.state.settings
    _, version = _active_version_or_409(session, book_id)
    items, next_cursor = list_quotes(
        session,
        settings,
        version,
        chapter_id=chapter_id,
        cursor=cursor,
        limit=parse_limit(limit, default=200, maximum=500),
    )
    page = CursorPage[QuoteOut](items=items, next_cursor=next_cursor)
    return DataEnvelope(data=page, request_id=current_request_id(request))


@router.get(
    "/{book_id}/gaps",
    response_model=DataEnvelope[CursorPage[GapOut]],
    summary="Gap（相邻外层候选之间的叙述）",
)
def list_gaps_route(
    request: Request,
    book_id: str,
    limit: int | None = Query(default=None, ge=1, le=500),
    cursor: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> DataEnvelope[CursorPage[GapOut]]:
    settings = request.app.state.settings
    _, version = _active_version_or_409(session, book_id)
    items, next_cursor = list_gaps(
        session,
        settings,
        version,
        cursor=cursor,
        limit=parse_limit(limit, default=200, maximum=500),
    )
    page = CursorPage[GapOut](items=items, next_cursor=next_cursor)
    return DataEnvelope(data=page, request_id=current_request_id(request))


@router.get(
    "/{book_id}/locate",
    response_model=DataEnvelope[LocateOut],
    summary="按码点范围定位原文（章节/节点/源文档）",
)
def locate_route(
    request: Request,
    book_id: str,
    start_cp: int = Query(ge=0),
    end_cp: int = Query(ge=0),
    session: Session = Depends(get_session),
) -> DataEnvelope[LocateOut]:
    settings = request.app.state.settings
    _, version = _active_version_or_409(session, book_id)
    result = locate(session, settings, version, start_cp=start_cp, end_cp=end_cp)
    return DataEnvelope(data=result, request_id=current_request_id(request))


@router.post(
    "/{book_id}/quotes/scan",
    status_code=202,
    response_model=DataEnvelope[ScanResultOut],
    summary="重新扫描候选引语与 Gap（有用户标注时拒绝）",
)
def scan_quotes_route(
    request: Request,
    book_id: str,
) -> DataEnvelope[ScanResultOut]:
    settings = request.app.state.settings
    factory = request.app.state.session_factory
    try:
        with transaction(factory) as session:
            book, version = _active_version_or_409(session, book_id)
            outcome = scan_and_store(session, settings, version)
            job = Job(
                kind=JobKind.RECOMPUTE,
                purpose=None,
                book_id=book.id,
                book_version_id=version.id,
                state=JobState.COMPLETED,
                range_json="{}",
                progress_json=json.dumps(
                    {
                        "stage": "completed",
                        "scanner_version": outcome.scanner_version,
                        "quotes": outcome.quotes,
                        "gaps": outcome.gaps,
                    },
                    ensure_ascii=False,
                ),
            )
            session.add(job)
            session.flush()
            payload = ScanResultOut(
                book_id=book.id,
                book_version_id=version.id,
                job_id=job.id,
                scanner_version=outcome.scanner_version,
                quote_count=outcome.quotes,
                top_level_quote_count=outcome.top_level_quotes,
                gap_count=outcome.gaps,
                warnings=list(outcome.warnings),
                stats=outcome.stats,
            )
    except ScanConflict as exc:
        raise ApiError(
            ErrorCode.VERSION_CONFLICT,
            str(exc),
            details={"book_id": book_id, "reason": "USER_LABELING_PRESENT"},
            status_code=409,
        ) from exc
    return DataEnvelope(data=payload, request_id=current_request_id(request))


@quote_router.get(
    "/{quote_id}",
    response_model=DataEnvelope[QuoteDetailOut],
    summary="候选对白详情（含上下文与前置 Gap）",
)
def get_quote_route(
    request: Request,
    quote_id: str,
    context_window_cp: int = Query(default=120, ge=0, le=2000),
    session: Session = Depends(get_session),
) -> DataEnvelope[QuoteDetailOut]:
    settings = request.app.state.settings
    quote = session.get(Quote, quote_id)
    if quote is None:
        raise ApiError.not_found("候选对白不存在", quote_id=quote_id)
    version = session.get(BookVersion, quote.book_version_id)
    if version is None:
        raise ApiError.not_found("书籍版本不存在", quote_id=quote_id)
    detail = get_quote_detail(
        session, settings, version, quote_id, context_window_cp=context_window_cp
    )
    return DataEnvelope(data=detail, request_id=current_request_id(request))
