"""手动书签 CRUD；不写自动阅读位置，不调用模型。"""

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.bookmarks import BookmarkCreate, BookmarkOut, BookmarkPatch
from ..domain.common import CursorPage, DataEnvelope
from ..ingest.query import get_book_or_404, load_canonical_text
from ..storage.models import Bookmark, BookVersion, Chapter
from ..storage.transactions import apply_versioned_update, check_version, transaction
from .deps import get_session
from .errors import ApiError, current_request_id
from .pagination import chronological_cursor, encode_cursor

router = APIRouter(prefix="/books/{book_id}/bookmarks", tags=["bookmarks"])


def output(row: Bookmark, title: str | None) -> BookmarkOut:
    return BookmarkOut(
        id=row.id,
        book_id=row.book_id,
        book_version_id=row.book_version_id,
        chapter_id=row.chapter_id,
        chapter_title=title or "未命名章节",
        position_cp=row.position_cp,
        excerpt=row.excerpt,
        note=row.note,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def lookup(session: Session, book_id: str, bookmark_id: str) -> Bookmark:
    get_book_or_404(session, book_id)
    row = session.get(Bookmark, bookmark_id)
    if row is None or row.book_id != book_id:
        raise ApiError.not_found("书签不存在", bookmark_id=bookmark_id)
    return row


@router.get("", response_model=DataEnvelope[CursorPage[BookmarkOut]])
def list_bookmarks(
    request: Request,
    book_id: str,
    cursor: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    session: Session = Depends(get_session),
) -> DataEnvelope[CursorPage[BookmarkOut]]:
    get_book_or_404(session, book_id)
    stmt = select(Bookmark, Chapter.title).join(Chapter).where(Bookmark.book_id == book_id)
    if cursor:
        stmt = stmt.where(chronological_cursor(session, Bookmark, cursor))
    rows = session.execute(stmt.order_by(Bookmark.created_at, Bookmark.id).limit(limit + 1)).all()
    page = rows[:limit]
    next_cursor = (
        encode_cursor([page[-1][0].created_at.isoformat(), page[-1][0].id])
        if len(rows) > limit
        else None
    )
    return DataEnvelope(
        data=CursorPage(
            items=[output(row, title) for row, title in page],
            next_cursor=next_cursor,
        ),
        request_id=current_request_id(request),
    )


@router.post("", status_code=201, response_model=DataEnvelope[BookmarkOut])
def create_bookmark(
    request: Request, book_id: str, payload: BookmarkCreate
) -> DataEnvelope[BookmarkOut]:
    with transaction(request.app.state.session_factory) as session:
        book = get_book_or_404(session, book_id)
        version = session.get(BookVersion, payload.book_version_id)
        chapter = session.get(Chapter, payload.chapter_id)
        if version is None or version.book_id != book.id or book.active_version_id != version.id:
            raise ApiError.validation("只能为当前原文版本添加书签")
        if chapter is None or chapter.book_version_id != version.id:
            raise ApiError.validation("书签章节不属于当前原文版本")
        if not chapter.start_cp <= payload.position_cp < chapter.end_cp:
            raise ApiError.validation("书签位置必须位于所选章节正文内")
        text = load_canonical_text(request.app.state.settings, version)
        row = Bookmark(
            book_id=book_id,
            book_version_id=version.id,
            chapter_id=chapter.id,
            position_cp=payload.position_cp,
            note=payload.note.strip(),
            excerpt=text[payload.position_cp : min(payload.position_cp + 120, chapter.end_cp)],
        )
        session.add(row)
        session.flush()
        result = output(row, chapter.title)
    return DataEnvelope(data=result, request_id=current_request_id(request))


@router.patch("/{bookmark_id}", response_model=DataEnvelope[BookmarkOut])
def edit_bookmark(
    request: Request, book_id: str, bookmark_id: str, payload: BookmarkPatch
) -> DataEnvelope[BookmarkOut]:
    with transaction(request.app.state.session_factory) as session:
        row = lookup(session, book_id, bookmark_id)
        apply_versioned_update(
            session,
            row,
            expected_version=payload.expected_version,
            changes={"note": payload.note.strip()},
        )
        chapter = session.get(Chapter, row.chapter_id)
        result = output(row, chapter.title if chapter else None)
    return DataEnvelope(data=result, request_id=current_request_id(request))


@router.delete("/{bookmark_id}", status_code=204)
def delete_bookmark(
    request: Request, book_id: str, bookmark_id: str, expected_version: int = Query(ge=1)
) -> Response:
    with transaction(request.app.state.session_factory) as session:
        row = lookup(session, book_id, bookmark_id)
        check_version(row, expected_version)
        session.delete(row)
    return Response(status_code=204)
