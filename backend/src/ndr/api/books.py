"""书籍导入与阅读路由（DEVELOPMENT.md 5.2；T02 实现 TXT 部分）。

- ``POST /api/books/import``：multipart + 可选 encoding；202 返回 book_id 与 IMPORT job_id。
  T02 的解析在请求内同步完成并立即写入终态；T10 引入调度器后改为后台执行，契约不变。
- ``GET /api/books``、``/books/{id}``、``/books/{id}/chapters``、``/books/{id}/content``：
  不调用模型，无 LLM 也能完整读取原文。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy.orm import Session

from ..config import Settings
from ..domain.common import CursorPage, DataEnvelope
from ..domain.documents import BookOut, ChapterOut, ContentResponse, ImportResult
from ..domain.enums import ErrorCode
from ..ingest.encoding import DecodeFailure
from ..ingest.query import (
    active_version,
    book_out,
    content_nodes,
    get_book_or_404,
    list_books,
    list_chapters,
)
from ..ingest.service import import_txt, record_failed_import
from ..storage.transactions import transaction
from .deps import get_session
from .errors import ApiError, current_request_id
from .pagination import parse_limit

router = APIRouter(prefix="/books", tags=["books"])

TXT_SUFFIX = ".txt"
MAX_IMPORT_BYTES_FALLBACK = 50 * 1024 * 1024


@router.post(
    "/import",
    status_code=202,
    response_model=DataEnvelope[ImportResult],
    summary="导入 TXT（202 + IMPORT 任务）",
)
async def import_book(
    request: Request,
    file: UploadFile = File(..., description="TXT 文件；EPUB 在 T03 支持"),
    encoding: str | None = Form(default=None, description="显式编码；留空自动检测"),
    title: str | None = Form(default=None, description="书名；留空用文件名"),
) -> DataEnvelope[ImportResult]:
    settings: Settings = request.app.state.settings
    filename = file.filename or f"upload{TXT_SUFFIX}"
    suffix = Path(filename).suffix.lower()
    if suffix != TXT_SUFFIX:
        raise ApiError(
            ErrorCode.UNSUPPORTED_MEDIA_TYPE,
            "T02 仅支持 .txt 文件；EPUB 导入在 T03 提供",
            details={"filename": filename, "supported": [TXT_SUFFIX]},
            status_code=415,
        )

    raw = await file.read()
    max_bytes = getattr(settings, "max_import_bytes", MAX_IMPORT_BYTES_FALLBACK)
    if not raw:
        raise ApiError.validation("文件为空，无法导入", filename=filename)
    if len(raw) > max_bytes:
        raise ApiError(
            ErrorCode.PAYLOAD_TOO_LARGE,
            "文件超过导入上限",
            details={"size_bytes": len(raw), "max_bytes": max_bytes},
            status_code=413,
        )

    factory = request.app.state.session_factory
    try:
        with transaction(factory) as session:
            outcome = import_txt(
                session,
                settings,
                filename=filename,
                raw=raw,
                encoding=encoding,
                title=title,
            )
            result = ImportResult(
                book_id=outcome.book.id,
                book_version_id=outcome.version.id,
                job_id=outcome.job.id,
                import_status=outcome.book.import_status,
                encoding=outcome.parsed.encoding,
                encoding_confidence=outcome.parsed.encoding_confidence,
                chapter_count=len(outcome.parsed.chapters),
                canonical_length_cp=outcome.parsed.canonical_length_cp,
                reused_book=outcome.reused_book,
                reused_version=outcome.reused_version,
                warnings=list(outcome.parsed.warnings),
            )
    except DecodeFailure as exc:
        # 失败也留痕：返回 422 时带上 job_id，便于用户/前端查询失败原因。
        with transaction(factory) as session:
            job = record_failed_import(
                session,
                message=str(exc),
                filename=filename,
                requested_encoding=encoding,
            )
            job_id = job.id
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            str(exc),
            details={"job_id": job_id, **exc.details},
            status_code=422,
        ) from exc

    return DataEnvelope(data=result, request_id=current_request_id(request))


@router.get("", response_model=DataEnvelope[CursorPage[BookOut]], summary="书籍列表")
def list_books_route(
    request: Request,
    limit: int | None = Query(default=None, ge=1, le=200),
    cursor: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> DataEnvelope[CursorPage[BookOut]]:
    effective_limit = parse_limit(limit)
    items, next_cursor = list_books(session, limit=effective_limit, cursor=cursor)
    page = CursorPage[BookOut](items=items, next_cursor=next_cursor)
    return DataEnvelope(data=page, request_id=current_request_id(request))


@router.get("/{book_id}", response_model=DataEnvelope[BookOut], summary="书籍详情")
def get_book_route(
    request: Request,
    book_id: str,
    session: Session = Depends(get_session),
) -> DataEnvelope[BookOut]:
    book = get_book_or_404(session, book_id)
    return DataEnvelope(
        data=book_out(book, active_version(session, book)),
        request_id=current_request_id(request),
    )


@router.get(
    "/{book_id}/chapters",
    response_model=DataEnvelope[list[ChapterOut]],
    summary="目录（按 ordinal）",
)
def list_chapters_route(
    request: Request,
    book_id: str,
    session: Session = Depends(get_session),
) -> DataEnvelope[list[ChapterOut]]:
    book = get_book_or_404(session, book_id)
    version = active_version(session, book)
    if version is None:
        raise ApiError(
            ErrorCode.NOT_FOUND,
            "书籍还没有可用版本",
            details={"book_id": book_id},
            status_code=409,
        )
    return DataEnvelope(
        data=list_chapters(session, version.id),
        request_id=current_request_id(request),
    )


@router.get(
    "/{book_id}/content",
    response_model=DataEnvelope[ContentResponse],
    summary="结构化正文节点（章节或码点范围）",
)
def content_route(
    request: Request,
    book_id: str,
    chapter_id: str | None = Query(default=None),
    start_cp: int | None = Query(default=None, ge=0),
    end_cp: int | None = Query(default=None, ge=1),
    limit: int | None = Query(default=None, ge=1, le=2000),
    cursor: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> DataEnvelope[ContentResponse]:
    settings: Settings = request.app.state.settings
    book = get_book_or_404(session, book_id)
    version = active_version(session, book)
    if version is None:
        raise ApiError(
            ErrorCode.NOT_FOUND,
            "书籍还没有可用版本",
            details={"book_id": book_id},
            status_code=409,
        )
    response = content_nodes(
        session,
        settings,
        book=book,
        version=version,
        chapter_id=chapter_id,
        start_cp=start_cp,
        end_cp=end_cp,
        limit=parse_limit(limit, default=500, maximum=2000),
        cursor=cursor,
    )
    return DataEnvelope(data=response, request_id=current_request_id(request))
