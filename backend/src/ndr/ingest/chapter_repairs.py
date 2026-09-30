"""Conservative chapter metadata repair; canonical text never changes."""

from __future__ import annotations

import re

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from ..api.errors import ApiError
from ..domain.documents import ChapterRepairsIn, ChapterRepairSuggestion
from ..domain.enums import ErrorCode, JobState
from ..storage.models import (
    Annotation,
    Bookmark,
    Chapter,
    ChapterCharacterRoster,
    ContentNode,
    Job,
    Quote,
    TextMapping,
)
from .query import get_book_or_404


def suspicious_heading(title: str) -> bool:
    return len(title) > 50 or bool(re.search(r"[，、。！!；;…]", title))


def duplicate_heading(previous: str, current: str) -> bool:
    return bool(
        current
        and current in {"序章", "序言", "后记", "後記", "终章", "終章"}
        and previous.strip().endswith(current)
    )


def suggestions(session: Session, book_id: str) -> list[ChapterRepairSuggestion]:
    book = get_book_or_404(session, book_id)
    chapters = list(
        session.scalars(
            select(Chapter)
            .where(
                Chapter.book_version_id == book.active_version_id,
            )
            .order_by(Chapter.ordinal)
        )
    )
    result = []
    for index, chapter in enumerate(chapters):
        title = chapter.title or ""
        repeated = index > 0 and duplicate_heading(chapters[index - 1].title or "", title)
        if repeated or suspicious_heading(title):
            result.append(
                ChapterRepairSuggestion(
                    chapter_id=chapter.id,
                    title=chapter.title,
                    suggested_title=(chapters[index - 1].title or "正文") if index else "正文",
                    merge_previous=index > 0,
                    reason="与上一章节标题重复，可能是重复识别的标题"
                    if repeated
                    else "标题包含正文式标点或过长，可能将叙述误判为章节",
                )
            )
    return result


def apply_repairs(
    session: Session, book_id: str, payload: ChapterRepairsIn, *, acquire_lock: bool = True
) -> None:
    if acquire_lock:
        session.execute(text("BEGIN IMMEDIATE"))
    book = get_book_or_404(session, book_id)
    if payload.book_version_id != book.active_version_id:
        raise ApiError.validation("书籍版本已变化，请重新读取目录")
    if session.scalar(
        select(Job.id)
        .where(
            Job.book_id == book_id,
            Job.state.in_(
                (
                    JobState.QUEUED,
                    JobState.RUNNING,
                    JobState.PAUSING,
                    JobState.PAUSED,
                    JobState.PARTIAL,
                    JobState.BUDGET_EXHAUSTED,
                )
            ),
        )
        .limit(1)
    ):
        raise ApiError(
            ErrorCode.RESOURCE_CONFLICT, "本书有运行或可恢复任务，不能修复目录", status_code=409
        )
    chapters = list(
        session.scalars(
            select(Chapter)
            .where(
                Chapter.book_version_id == payload.book_version_id,
            )
            .order_by(Chapter.ordinal)
        )
    )
    by_id = {chapter.id: chapter for chapter in chapters}
    changes = {item.chapter_id: item for item in payload.repairs}
    if len(changes) != len(payload.repairs):
        raise ApiError.validation("不能重复修复同一章节")
    for item in payload.repairs:
        chapter = by_id.get(item.chapter_id)
        if chapter is None:
            raise ApiError.not_found("章节不属于当前书籍版本")
        if chapter.title != item.expected_title:
            raise ApiError(
                ErrorCode.RESOURCE_CONFLICT, "章节名称已变化，请重新读取", status_code=409
            )
        if not item.title.strip():
            raise ApiError.validation("章节名不能为空")

    previous: Chapter | None = None
    for chapter in chapters:
        item = changes.get(chapter.id)
        if item and item.merge_previous:
            if previous is None:
                raise ApiError.validation("第一章不能并入上一章")
            chapter_ids = [previous.id, chapter.id]
            has_annotations = session.scalar(
                select(Annotation.id)
                .join(
                    Quote,
                    Quote.id == Annotation.quote_id,
                )
                .where(Quote.chapter_id.in_(chapter_ids))
                .limit(1)
            )
            has_roster = session.scalar(
                select(ChapterCharacterRoster.id)
                .where(
                    ChapterCharacterRoster.chapter_id.in_(chapter_ids),
                )
                .limit(1)
            )
            if (
                has_annotations
                or has_roster
                or previous.dialogue_processed
                or chapter.dialogue_processed
            ):
                raise ApiError(
                    ErrorCode.RESOURCE_CONFLICT,
                    "已有标注或人物名单的章节只能改名，不能合并；请在处理前修复边界",
                    status_code=409,
                )
            offset = (
                int(
                    session.scalar(
                        select(func.max(ContentNode.ordinal)).where(
                            ContentNode.chapter_id == previous.id,
                        )
                    )
                    or 0
                )
                + 1
            )
            prefix = chapter.id + ":"
            session.execute(
                update(ContentNode)
                .where(ContentNode.chapter_id == chapter.id)
                .values(
                    chapter_id=previous.id,
                    node_id=prefix + ContentNode.node_id,
                    ordinal=ContentNode.ordinal + offset,
                )
            )
            session.execute(
                update(TextMapping)
                .where(TextMapping.chapter_id == chapter.id)
                .values(
                    chapter_id=previous.id,
                    node_id=prefix + TextMapping.node_id,
                )
            )
            session.execute(
                update(Quote).where(Quote.chapter_id == chapter.id).values(chapter_id=previous.id)
            )
            session.execute(
                update(Bookmark)
                .where(Bookmark.chapter_id == chapter.id)
                .values(chapter_id=previous.id)
            )
            previous.end_cp = chapter.end_cp
            previous.title = item.title.strip()
            session.delete(chapter)
        else:
            if item:
                chapter.title = item.title.strip()
            previous = chapter
    book.version += 1
