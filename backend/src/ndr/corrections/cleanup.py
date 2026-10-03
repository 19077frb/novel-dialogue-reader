"""Explicit cleanup of legacy local-edit cascades, never structural uncertainty."""

from __future__ import annotations

import json

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..api.errors import ApiError
from ..domain.corrections import StaleReviewCleanupOut
from ..domain.enums import (
    AnnotationSource,
    CorrectionTargetType,
    ErrorCode,
    JobState,
    ReviewQueueStatus,
    ReviewReason,
)
from ..ingest.query import active_version, get_book_or_404
from ..storage.models import Annotation, AnnotationHistory, Correction, Job, Quote, ReviewItem
from .history import annotation_snapshot


def cleanup_local_edit_reviews(session: Session, book_id: str) -> StaleReviewCleanupOut:
    session.execute(text("BEGIN IMMEDIATE"))
    book = get_book_or_404(session, book_id)
    version = active_version(session, book)
    if version is None:
        raise ApiError.validation("书籍还没有可用版本")
    if session.scalar(select(Job.id).where(
        Job.book_id == book_id,
        Job.state.in_((JobState.QUEUED, JobState.RUNNING, JobState.PAUSING)),
    ).limit(1)):
        raise ApiError(ErrorCode.VERSION_CONFLICT,
                       "本书有排队或运行任务，请等待结束或停止任务后再清理。", status_code=409)
    rows = list(session.execute(select(ReviewItem, Annotation).join(
        Annotation, Annotation.quote_id == ReviewItem.quote_id,
    ).join(Quote, Quote.id == Annotation.quote_id).where(
        Quote.book_version_id == version.id,
        ReviewItem.reason == ReviewReason.STALE_DEPENDENCY,
        ReviewItem.queue_status != ReviewQueueStatus.RESOLVED,
    )))
    result = StaleReviewCleanupOut()
    for offset in range(0, len(rows), 500):
        chunk = rows[offset:offset + 500]
        causes = {}
        for item, _ in chunk:
            try:
                data = json.loads(item.candidates_json)
            except (ValueError, TypeError):
                continue
            if isinstance(data, dict) and isinstance(data.get("correction_id"), str):
                causes[item.id] = data["correction_id"]
        corrections = {row.id: row for row in session.scalars(select(Correction).join(
            Quote, Quote.id == Correction.target_id,
        ).where(Correction.id.in_(set(causes.values())), Quote.book_version_id == version.id))}
        histories = list(session.execute(select(
            AnnotationHistory.annotation_id, AnnotationHistory.revision, Correction.target_type,
        ).outerjoin(Correction, Correction.id == AnnotationHistory.correction_id).where(
            AnnotationHistory.annotation_id.in_([a.id for _, a in chunk]),
        )))
        structural = {annotation_id for annotation_id, _, target_type in histories
                      if target_type in {CorrectionTargetType.SCENE, CorrectionTargetType.GAP}}
        revisions = {(annotation_id, revision) for annotation_id, revision, _ in histories}
        for item, annotation in chunk:
            cause = corrections.get(causes.get(item.id))
            eligible = (
                item.queue_status is ReviewQueueStatus.PENDING
                and cause is not None
                and cause.target_type in {
                    CorrectionTargetType.QUOTE, CorrectionTargetType.ANNOTATION,
                }
                and cause.target_id != annotation.quote_id
                and item.annotation_version == annotation.version
                and not annotation.user_locked
                and annotation.source is AnnotationSource.MODEL
                and annotation.id not in structural
            )
            if not eligible:
                result.preserved_records += 1
                continue
            if annotation.stale:
                if (annotation.id, annotation.version) not in revisions:
                    session.add(AnnotationHistory(
                        annotation_id=annotation.id, revision=annotation.version,
                        snapshot_json=json.dumps(
                            annotation_snapshot(annotation), ensure_ascii=False,
                        ),
                        visible_from_cp=annotation.visible_from_cp,
                    ))
                annotation.stale = False
                annotation.version += 1
                result.restored_quotes += 1
            item.queue_status = ReviewQueueStatus.RESOLVED
            item.resolved_by_correction_id = None
            item.version += 1
            result.resolved_records += 1
    session.flush()
    return result
