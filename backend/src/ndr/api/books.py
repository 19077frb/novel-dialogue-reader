"""书籍导入与阅读路由。

- ``POST /api/books/import``：multipart + 可选 encoding；202 返回 book_id 与 IMPORT job_id。
  解析在请求内同步完成并立即写入终态；引入调度器后改为后台执行，契约不变。
- ``GET /api/books``、``/books/{id}``、``/books/{id}/chapters``、``/books/{id}/content``：
  不调用模型，无 LLM 也能完整读取原文。
- ``GET /api/books/{id}/resources/{resource_id}``：只服务已登记且位于书籍包内的资源。
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..domain.common import CursorPage, DataEnvelope
from ..domain.documents import (
    BookOut,
    ChapterOut,
    ChapterProcessingCompleteIn,
    ChapterProcessingCompleteOut,
    ContentResponse,
    ImportResult,
    ReadingProgressIn,
    ReadingProgressOut,
)
from ..domain.enums import ErrorCode
from ..ingest.deletion import delete_book
from ..ingest.encoding import DecodeFailure
from ..ingest.epub import EpubError, EpubLimits
from ..ingest.query import (
    active_version,
    book_out,
    content_nodes,
    get_book_or_404,
    list_books,
    list_chapters,
    reading_chapter_titles,
)
from ..ingest.resources import get_resource_or_404, read_resource_bytes
from ..ingest.service import import_epub, import_txt, record_failed_import
from ..storage.models import Annotation, BookVersion, Chapter, Quote
from ..storage.transactions import apply_versioned_update, transaction
from .deps import get_session
from .errors import ApiError, current_request_id
from .pagination import parse_limit

router = APIRouter(prefix="/books", tags=["books"])

TXT_SUFFIX = ".txt"
EPUB_SUFFIX = ".epub"
SUPPORTED_SUFFIXES = (TXT_SUFFIX, EPUB_SUFFIX)
MAX_IMPORT_BYTES_FALLBACK = 50 * 1024 * 1024


@router.delete("/{book_id}", status_code=204, summary="删除书籍与关联记录，文件移入回收区")
def delete_book_route(request: Request, book_id: str) -> Response:
    try:
        delete_book(request.app.state.session_factory, request.app.state.settings, book_id)
    except OSError as exc:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            "删除时无法移动书籍文件，请关闭占用文件的程序或检查数据目录权限后重试",
            details={"book_id": book_id, "reason": str(exc)},
            status_code=503,
        ) from exc
    return Response(status_code=204)


def _epub_limits(settings: Settings) -> EpubLimits:
    return EpubLimits(
        max_entries=getattr(settings, "max_epub_entries", 2000),
        max_total_uncompressed_bytes=getattr(
            settings, "max_epub_total_uncompressed_bytes", 200 * 1024 * 1024
        ),
        max_entry_uncompressed_bytes=getattr(settings, "max_epub_entry_bytes", 32 * 1024 * 1024),
        max_spine_items=getattr(settings, "max_epub_spine_items", 500),
    )


@router.post(
    "/import",
    status_code=202,
    response_model=DataEnvelope[ImportResult],
    summary="导入 TXT/EPUB（202 + IMPORT 任务）",
)
async def import_book(
    request: Request,
    file: UploadFile = File(..., description="TXT 或 EPUB 文件"),
    encoding: str | None = Form(default=None, description="显式编码；留空自动检测（仅 TXT）"),
    title: str | None = Form(default=None, description="书名；留空用文件内元数据或文件名"),
) -> DataEnvelope[ImportResult]:
    settings: Settings = request.app.state.settings
    filename = file.filename or f"upload{TXT_SUFFIX}"
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ApiError(
            ErrorCode.UNSUPPORTED_MEDIA_TYPE,
            "只支持 .txt 与 .epub 文件",
            details={"filename": filename, "supported": list(SUPPORTED_SUFFIXES)},
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
            if suffix == EPUB_SUFFIX:
                outcome = import_epub(
                    session,
                    settings,
                    filename=filename,
                    raw=raw,
                    title=title,
                    limits=_epub_limits(settings),
                )
            else:
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
                format=outcome.book.format,
                import_status=outcome.book.import_status,
                encoding=outcome.parsed.encoding,
                encoding_confidence=outcome.parsed.encoding_confidence,
                chapter_count=len(outcome.parsed.chapters),
                node_count=len(outcome.parsed.nodes),
                resource_count=len(outcome.parsed.resources),
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
                stage="decode",
            )
            job_id = job.id
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            str(exc),
            details={"job_id": job_id, **exc.details},
            status_code=422,
        ) from exc
    except EpubError as exc:
        with transaction(factory) as session:
            job = record_failed_import(
                session,
                message=str(exc),
                filename=filename,
                requested_encoding=None,
                stage="epub_structure",
            )
            job_id = job.id
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            str(exc),
            details={"job_id": job_id, "reason_code": exc.code, **exc.details},
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
        data=book_out(
            book,
            active_version(session, book),
            reading_chapter_titles(session, [book]).get(book.id),
        ),
        request_id=current_request_id(request),
    )


@router.get(
    "/{book_id}/chapters",
    response_model=DataEnvelope[list[ChapterOut]],
    summary="目录（按 ordinal，EPUB 为 spine 顺序）",
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


@router.post(
    "/{book_id}/chapters/{chapter_id}/processing-complete",
    response_model=DataEnvelope[ChapterProcessingCompleteOut],
    summary="确认并发窗口已覆盖整章并标记为已处理",
)
def complete_chapter_processing_route(
    request: Request,
    book_id: str,
    chapter_id: str,
    payload: ChapterProcessingCompleteIn,
) -> DataEnvelope[ChapterProcessingCompleteOut]:
    """只在本章每条外层候选对白都有当前标注时写入完成状态。"""

    factory = request.app.state.session_factory
    with transaction(factory) as session:
        book = get_book_or_404(session, book_id)
        version = session.get(BookVersion, payload.book_version_id)
        if version is None or version.book_id != book.id:
            raise ApiError.validation(
                "book_version_id 不属于该书籍",
                book_id=book_id,
                book_version_id=payload.book_version_id,
            )
        chapter = session.get(Chapter, chapter_id)
        if chapter is None or chapter.book_version_id != version.id:
            raise ApiError.not_found(
                "章节不存在或不属于指定书籍版本",
                book_id=book_id,
                chapter_id=chapter_id,
            )

        quote_count = int(
            session.scalar(
                select(func.count(Quote.id)).where(
                    Quote.chapter_id == chapter.id,
                    Quote.nesting_depth == 0,
                )
            )
            or 0
        )
        annotated_quote_count = int(
            session.scalar(
                select(func.count(Quote.id))
                .select_from(Quote)
                .join(Annotation, Annotation.quote_id == Quote.id)
                .where(
                    Quote.chapter_id == chapter.id,
                    Quote.nesting_depth == 0,
                )
            )
            or 0
        )
        if annotated_quote_count != quote_count:
            raise ApiError(
                ErrorCode.RESOURCE_CONFLICT,
                "章节仍有未处理对白，不能标记为已处理",
                details={
                    "chapter_id": chapter.id,
                    "quote_count": quote_count,
                    "annotated_quote_count": annotated_quote_count,
                },
                status_code=409,
            )
        chapter.dialogue_processed = True
        result = ChapterProcessingCompleteOut(
            chapter_id=chapter.id,
            dialogue_processed=True,
            quote_count=quote_count,
            annotated_quote_count=annotated_quote_count,
        )
    return DataEnvelope(data=result, request_id=current_request_id(request))


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


@router.get(
    "/{book_id}/resources/{resource_id}",
    summary="受控资源（图片等，独立响应体）",
    response_class=Response,
)
def get_resource_route(
    request: Request,
    book_id: str,
    resource_id: str,
    session: Session = Depends(get_session),
) -> Response:
    settings: Settings = request.app.state.settings
    book = get_book_or_404(session, book_id)
    resource, version = get_resource_or_404(session, book, resource_id)
    payload = read_resource_bytes(settings, book, version, resource)

    name = Path(resource.relative_path).name or resource.resource_id
    ascii_name = name.encode("ascii", "ignore").decode("ascii") or "resource"
    disposition = f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"
    return Response(
        content=payload,
        media_type=resource.media_type or "application/octet-stream",
        headers={
            "Content-Disposition": disposition,
            "Cache-Control": "private, max-age=600",
            "X-Resource-Sha256": resource.sha256,
        },
    )


@router.put(
    "/{book_id}/reading-progress",
    response_model=DataEnvelope[ReadingProgressOut],
    summary="保存阅读位置与阅读模式（不调用模型）",
)
def save_reading_progress_route(
    request: Request,
    book_id: str,
    payload: ReadingProgressIn,
) -> DataEnvelope[ReadingProgressOut]:
    """保存最后阅读位置：只写数据库，不触发任何模型调用。"""

    factory = request.app.state.session_factory
    with transaction(factory) as session:
        book = get_book_or_404(session, book_id)
        version = session.get(BookVersion, payload.book_version_id)
        if version is None or version.book_id != book.id:
            raise ApiError.validation(
                "book_version_id 不属于该书籍",
                book_id=book_id,
                book_version_id=payload.book_version_id,
            )
        if payload.read_position_cp > version.canonical_length_cp:
            raise ApiError.validation(
                "阅读位置超出该版本正文范围",
                read_position_cp=payload.read_position_cp,
                canonical_length_cp=version.canonical_length_cp,
            )
        new_version = apply_versioned_update(
            session,
            book,
            expected_version=payload.expected_version,
            changes={
                "read_position_cp": payload.read_position_cp,
                "read_position_version_id": version.id,
                "reading_mode": payload.reading_mode,
            },
        )
        result = ReadingProgressOut(
            book_id=book.id,
            book_version_id=version.id,
            read_position_cp=book.read_position_cp,
            reading_mode=book.reading_mode,
            version=new_version,
        )
    return DataEnvelope(data=result, request_id=current_request_id(request))
