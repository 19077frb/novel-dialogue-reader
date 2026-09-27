"""书籍与内容的读取路径（T02）。

只读查询：书籍列表/详情、目录、正文节点。正文文本来自版本 canonical 文件，
节点通过 ``[start_cp, end_cp)`` 切片返回，前端不需要自己猜偏移。
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.errors import ApiError
from ..api.pagination import decode_cursor, encode_cursor
from ..config import Settings
from ..domain.documents import (
    BookOut,
    BookVersionOut,
    ChapterOut,
    ContentNodeOut,
    ContentResponse,
    JobOut,
)
from ..domain.enums import ErrorCode
from ..storage.models import Book, BookVersion, Chapter, ContentNode, Job
from ..storage.paths import resolve_within


def version_out(version: BookVersion) -> BookVersionOut:
    try:
        warnings = json.loads(version.warnings_json or "[]")
    except json.JSONDecodeError:
        warnings = []
    return BookVersionOut(
        id=version.id,
        encoding=version.encoding,
        parser_version=version.parser_version,
        normalization_version=version.normalization_version,
        canonical_sha256=version.canonical_sha256,
        canonical_length_cp=version.canonical_length_cp,
        warnings=[str(item) for item in warnings],
        created_at=version.created_at,
    )


def book_out(book: Book, version: BookVersion | None) -> BookOut:
    return BookOut(
        id=book.id,
        title=book.title,
        format=book.format,
        source_sha256=book.source_sha256,
        import_status=book.import_status,
        read_position_cp=book.read_position_cp,
        version=book.version,
        active_version_id=book.active_version_id,
        active_version=version_out(version) if version is not None else None,
        created_at=book.created_at,
        updated_at=book.updated_at,
    )


def active_version(session: Session, book: Book) -> BookVersion | None:
    if book.active_version_id is None:
        return None
    return session.get(BookVersion, book.active_version_id)


def get_book_or_404(session: Session, book_id: str) -> Book:
    book = session.get(Book, book_id)
    if book is None:
        raise ApiError.not_found("书籍不存在", book_id=book_id)
    return book


def list_books(
    session: Session, *, limit: int, cursor: str | None
) -> tuple[list[BookOut], str | None]:
    stmt = select(Book).order_by(Book.created_at, Book.id).limit(limit + 1)
    if cursor:
        parts = decode_cursor(cursor)
        if not parts:
            raise ApiError(ErrorCode.VALIDATION_ERROR, "cursor 内容为空")
        stmt = stmt.where(Book.id > str(parts[0]))
    rows = list(session.execute(stmt).scalars())
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [book_out(book, active_version(session, book)) for book in rows]
    next_cursor = encode_cursor([rows[-1].id]) if has_more and rows else None
    return items, next_cursor


def list_chapters(session: Session, version_id: str) -> list[ChapterOut]:
    rows = session.execute(
        select(Chapter).where(Chapter.book_version_id == version_id).order_by(Chapter.ordinal)
    ).scalars()
    return [
        ChapterOut(
            id=row.id,
            ordinal=row.ordinal,
            title=row.title,
            start_cp=row.start_cp,
            end_cp=row.end_cp,
            source_href=row.source_href,
        )
        for row in rows
    ]


def load_canonical_text(settings: Settings, version: BookVersion) -> str:
    if not version.canonical_path:
        raise ApiError(
            ErrorCode.NOT_FOUND,
            "该版本没有 canonical 全文（可能尚未完成导入）",
            details={"book_version_id": version.id},
            status_code=409,
        )
    path = resolve_within(settings, version.canonical_path)
    if not path.exists():
        raise ApiError(
            ErrorCode.NOT_FOUND,
            "canonical 全文文件缺失，请重新导入该书籍",
            details={"book_version_id": version.id},
            status_code=409,
        )
    return path.read_text(encoding="utf-8")


def job_out(job: Job) -> JobOut:
    def _load(raw: str | None) -> dict[str, object] | None:
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    return JobOut(
        id=job.id,
        kind=job.kind,
        purpose=job.purpose,
        state=job.state,
        book_id=job.book_id,
        book_version_id=job.book_version_id,
        progress=_load(job.progress_json),
        checkpoint=_load(job.checkpoint_json),
        last_error=job.last_error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _load_payload(tree_json: str | None) -> dict[str, object]:
    if not tree_json:
        return {}
    try:
        value = json.loads(tree_json)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def content_nodes(
    session: Session,
    settings: Settings,
    *,
    book: Book,
    version: BookVersion,
    chapter_id: str | None,
    start_cp: int | None,
    end_cp: int | None,
    limit: int,
    cursor: str | None,
) -> ContentResponse:
    """按章节或码点范围返回正文节点；``next_cursor`` 可直接作为下一次的 cursor。"""

    canonical_length = version.canonical_length_cp
    chapter: Chapter | None = None
    if chapter_id is not None:
        chapter = session.get(Chapter, chapter_id)
        if chapter is None or chapter.book_version_id != version.id:
            raise ApiError.not_found("章节不存在或不属于当前版本", chapter_id=chapter_id)
        range_start, range_end = chapter.start_cp, chapter.end_cp
    else:
        range_start = 0 if start_cp is None else start_cp
        range_end = canonical_length if end_cp is None else end_cp

    if cursor:
        parts = decode_cursor(cursor)
        if not parts:
            raise ApiError(ErrorCode.VALIDATION_ERROR, "cursor 内容为空")
        range_start = max(range_start, int(parts[0]))

    if range_start < 0 or range_end > canonical_length or range_start > range_end:
        raise ApiError.validation(
            "范围不合法",
            start_cp=range_start,
            end_cp=range_end,
            canonical_length_cp=canonical_length,
        )
    # 允许空范围（例如只有插图的 EPUB，canonical 长度为 0；或零长度章节）：
    # 返回该范围内的零长度节点（图片/分隔符），而不是报错。

    stmt = (
        select(ContentNode, Chapter)
        .join(Chapter, ContentNode.chapter_id == Chapter.id)
        .where(Chapter.book_version_id == version.id)
        .where(ContentNode.start_cp >= range_start)
        .where(ContentNode.end_cp <= range_end)
        .order_by(ContentNode.start_cp)
        .limit(limit + 1)
    )
    rows = list(session.execute(stmt))
    has_more = len(rows) > limit
    rows = rows[:limit]

    text = load_canonical_text(settings, version)
    nodes = [
        ContentNodeOut(
            node_id=node.node_id,
            node_type=node.node_type,
            ordinal=node.ordinal,
            start_cp=node.start_cp,
            end_cp=node.end_cp,
            chapter_id=chapter_row.id,
            chapter_ordinal=chapter_row.ordinal,
            text=text[node.start_cp : node.end_cp],
            payload=_load_payload(node.tree_json),
        )
        for node, chapter_row in rows
    ]
    next_cursor = encode_cursor([nodes[-1].end_cp]) if has_more and nodes else None
    return ContentResponse(
        book_id=book.id,
        book_version_id=version.id,
        canonical_length_cp=canonical_length,
        chapter_id=chapter.id if chapter is not None else None,
        start_cp=range_start,
        end_cp=range_end,
        nodes=nodes,
        next_cursor=next_cursor,
    )
