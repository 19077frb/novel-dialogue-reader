"""Synchronize only engine-generated attribution reasons, not user flags."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.enums import ReviewQueueStatus, ReviewReason
from ..storage.models import Annotation, ReviewItem

MODEL_REASONS = {ReviewReason.UNKNOWN_SPEAKER, ReviewReason.LOW_CONFIDENCE,
                 ReviewReason.AMBIGUOUS_SPEAKER}
ENGINE_REASONS = {"insufficient_evidence", "unknown_quote_kind", "style_only_basis",
                  "unverified_direct_basis", "unverified_coreference", "unverified_response_link"}


def sync_attribution_reviews(
    session: Session, annotation: Annotation, *, current_reason: ReviewReason | None,
    apply: bool = True,
) -> int:
    """Close superseded automatic reasons; preserve independently flagged items."""
    if annotation.stale:
        return 0
    changed = 0
    rows = session.scalars(select(ReviewItem).where(
        ReviewItem.quote_id == annotation.quote_id, ReviewItem.reason.in_(MODEL_REASONS),
        ReviewItem.queue_status != ReviewQueueStatus.RESOLVED,
    ))
    for row in rows:
        try:
            data = json.loads(row.candidates_json)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict) or data.get("reason") not in ENGINE_REASONS:
            continue
        if row.reason != current_reason:
            changed += 1
            if apply:
                row.queue_status = ReviewQueueStatus.RESOLVED
                row.version += 1
        elif apply:
            row.annotation_version = annotation.version
    return changed
