"""Automatic completion must never overwrite a concurrent manual choice."""

from sqlalchemy import update
from sqlalchemy.orm import Session

from .models import Chapter


def complete_chapter_automatically(session: Session, chapter: Chapter) -> bool:
    session.execute(update(Chapter).where(
        Chapter.id == chapter.id, Chapter.processing_status_override.is_(None),
    ).values(dialogue_processed=True), execution_options={"synchronize_session": False})
    session.expire(chapter, ["dialogue_processed", "processing_status_override"])
    return chapter.dialogue_processed
