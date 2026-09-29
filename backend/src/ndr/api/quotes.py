"""候选引语、Gap 与原文定位路由。

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
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..corrections.review import build_quote_detail as _build_quote_detail
from ..domain.common import CursorPage, DataEnvelope
from ..domain.enums import (
    ErrorCode,
    JobKind,
    JobState,
    QuoteNormalizationSource,
    QuoteNormalizationStatus,
)
from ..domain.quotes import (
    GapOut,
    LocateOut,
    QuoteDetailOut,
    QuoteNormalizationOut,
    QuoteNormalizationRefreshOut,
    QuoteNormalizationUpdateIn,
    QuoteOut,
    ScanResultOut,
)
from ..ingest.query import active_version, get_book_or_404, load_canonical_text
from ..quotes.normalization import detect_auto_close_suggestions, upsert_suggestions
from ..quotes.service import (
    ScanConflict,
    list_gaps,
    list_quotes,
    locate,
    scan_and_store,
)
from ..storage.models import Annotation, BookVersion, Job, Quote, QuoteNormalization
from ..storage.transactions import check_version, transaction
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


def _normalization_out(row: QuoteNormalization) -> QuoteNormalizationOut:
    return QuoteNormalizationOut(
        id=row.id,
        book_version_id=row.book_version_id,
        opening_cp=row.opening_cp,
        close_cp=row.close_cp,
        replacement=row.replacement,
        source=row.source,
        status=row.status,
        original_text=row.original_text,
        normalized_text=row.normalized_text,
        reason=row.reason,
        version=row.version,
    )


def _scan_payload(book, version, job_id: str, outcome) -> ScanResultOut:  # noqa: ANN001 - internal models
    return ScanResultOut(
        book_id=book.id,
        book_version_id=version.id,
        job_id=job_id,
        scanner_version=outcome.scanner_version,
        quote_count=outcome.quotes,
        top_level_quote_count=outcome.top_level_quotes,
        gap_count=outcome.gaps,
        warnings=list(outcome.warnings),
        stats=outcome.stats,
    )


def _record_scan_job(session, book, version, outcome) -> Job:  # noqa: ANN001 - internal models
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
                "reason": "quote_normalization",
                "scanner_version": outcome.scanner_version,
                "quotes": outcome.quotes,
                "gaps": outcome.gaps,
            },
            ensure_ascii=False,
        ),
    )
    session.add(job)
    session.flush()
    return job


def _active_normalization_count(session: Session, version_id: str) -> int:
    return int(
        session.execute(
            select(func.count(QuoteNormalization.id)).where(
                QuoteNormalization.book_version_id == version_id,
                QuoteNormalization.status == QuoteNormalizationStatus.ACTIVE,
            )
        ).scalar_one()
    )


@router.get(
    "/{book_id}/quote-normalizations",
    response_model=DataEnvelope[list[QuoteNormalizationOut]],
    summary="查看引号修复建议与用户调整",
)
def list_quote_normalizations_route(
    request: Request,
    book_id: str,
    session: Session = Depends(get_session),
) -> DataEnvelope[list[QuoteNormalizationOut]]:
    _, version = _active_version_or_409(session, book_id)
    rows = session.execute(
        select(QuoteNormalization)
        .where(QuoteNormalization.book_version_id == version.id)
        .order_by(QuoteNormalization.opening_cp)
    ).scalars()
    return DataEnvelope(
        data=[_normalization_out(row) for row in rows],
        request_id=current_request_id(request),
    )


@router.post(
    "/{book_id}/quote-normalizations/refresh",
    status_code=202,
    response_model=DataEnvelope[QuoteNormalizationRefreshOut],
    summary="重新检测引号修复点并重建候选",
)
def refresh_quote_normalizations_route(
    request: Request,
    book_id: str,
) -> DataEnvelope[QuoteNormalizationRefreshOut]:
    settings = request.app.state.settings
    factory = request.app.state.session_factory
    try:
        with transaction(factory) as session:
            book, version = _active_version_or_409(session, book_id)
            text = load_canonical_text(settings, version)
            created = upsert_suggestions(
                session,
                version.id,
                detect_auto_close_suggestions(text),
            )
            outcome = scan_and_store(session, settings, version)
            job = _record_scan_job(session, book, version, outcome)
            payload = QuoteNormalizationRefreshOut(
                book_id=book.id,
                book_version_id=version.id,
                created=created,
                active=_active_normalization_count(session, version.id),
                scan=_scan_payload(book, version, job.id, outcome),
            )
    except ScanConflict as exc:
        raise ApiError(
            ErrorCode.VERSION_CONFLICT,
            str(exc),
            details={"book_id": book_id, "reason": "USER_LABELING_PRESENT"},
            status_code=409,
        ) from exc
    return DataEnvelope(data=payload, request_id=current_request_id(request))


@router.put(
    "/{book_id}/quote-normalizations/{normalization_id}",
    status_code=202,
    response_model=DataEnvelope[QuoteNormalizationRefreshOut],
    summary="调整引号修复点并重建候选",
)
def update_quote_normalization_route(
    request: Request,
    book_id: str,
    normalization_id: str,
    payload: QuoteNormalizationUpdateIn,
) -> DataEnvelope[QuoteNormalizationRefreshOut]:
    settings = request.app.state.settings
    factory = request.app.state.session_factory
    try:
        with transaction(factory) as session:
            book, version = _active_version_or_409(session, book_id)
            row = session.get(QuoteNormalization, normalization_id)
            if row is None or row.book_version_id != version.id:
                raise ApiError.not_found(
                    "引号修复点不存在",
                    normalization_id=normalization_id,
                )
            check_version(row, payload.expected_version)
            text = load_canonical_text(settings, version)
            close_cp = payload.close_cp if payload.close_cp is not None else row.close_cp
            replacement = (
                payload.replacement
                if payload.replacement is not None
                else row.replacement
            )
            if (
                close_cp <= row.opening_cp
                or close_cp > len(text)
                or text.find("\n", row.opening_cp, close_cp) != -1
            ):
                raise ApiError.validation(
                    "闭合点必须在开引号所在段落内",
                    details={"opening_cp": row.opening_cp, "close_cp": close_cp},
                )
            row.close_cp = close_cp
            row.replacement = replacement
            row.status = payload.status or row.status
            row.source = QuoteNormalizationSource.USER
            row.original_text = text[row.opening_cp : close_cp]
            row.normalized_text = row.original_text + replacement
            row.version += 1
            session.flush()

            outcome = scan_and_store(session, settings, version)
            job = _record_scan_job(session, book, version, outcome)
            payload_out = QuoteNormalizationRefreshOut(
                book_id=book.id,
                book_version_id=version.id,
                created=0,
                active=_active_normalization_count(session, version.id),
                scan=_scan_payload(book, version, job.id, outcome),
            )
    except ScanConflict as exc:
        raise ApiError(
            ErrorCode.VERSION_CONFLICT,
            str(exc),
            details={"book_id": book_id, "reason": "USER_LABELING_PRESENT"},
            status_code=409,
        ) from exc
    return DataEnvelope(data=payload_out, request_id=current_request_id(request))


@router.post(
    "/{book_id}/quote-normalizations/clear-labeling",
    status_code=202,
    response_model=DataEnvelope[QuoteNormalizationRefreshOut],
    summary="清除当前版本的标注投影并重新检测引号修复",
)
def clear_quote_labeling_route(
    request: Request,
    book_id: str,
) -> DataEnvelope[QuoteNormalizationRefreshOut]:
    """显式清除本版本所有标注后重扫；历史表保留，用于审计。"""

    settings = request.app.state.settings
    factory = request.app.state.session_factory
    try:
        with transaction(factory) as session:
            book, version = _active_version_or_409(session, book_id)
            session.execute(
                delete(Annotation).where(Annotation.quote_id.in_(
                    select(Quote.id).where(Quote.book_version_id == version.id)
                ))
            )
            text = load_canonical_text(settings, version)
            upsert_suggestions(
                session,
                version.id,
                detect_auto_close_suggestions(text),
            )
            outcome = scan_and_store(session, settings, version)
            job = _record_scan_job(session, book, version, outcome)
            payload = QuoteNormalizationRefreshOut(
                book_id=book.id,
                book_version_id=version.id,
                created=0,
                active=_active_normalization_count(session, version.id),
                scan=_scan_payload(book, version, job.id, outcome),
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
    detail = _build_quote_detail(
        session, settings, version, quote_id, context_window_cp=context_window_cp
    )
    return DataEnvelope(data=detail, request_id=current_request_id(request))
