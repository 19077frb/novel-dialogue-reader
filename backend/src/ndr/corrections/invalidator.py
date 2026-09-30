"""依赖失效器。

人工更正会改变同一推理窗口内的联合判断：把受影响的下游标注标为 `stale`，
并建立 `STALE_DEPENDENCY` 待确认项，让待确认队列能看到「需要重新确认」的对白。

不会做的事：不删除标注、不改写历史、不动 `user_locked` 的对白、不自动重新推理。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..domain.enums import ReviewQueueStatus, ReviewReason, ReviewTargetType
from ..storage.base import new_id
from ..storage.models import Annotation, ReviewItem


@dataclass
class DownstreamImpact:
    quote_ids: list[str] = field(default_factory=list)
    window_ids: list[str] = field(default_factory=list)
    review_item_ids: list[str] = field(default_factory=list)
    skipped_locked_quote_ids: list[str] = field(default_factory=list)


def upsert_review_item(
    session: Session,
    *,
    quote_id: str | None = None,
    gap_id: str | None = None,
    reason: ReviewReason,
    candidates: dict[str, Any] | None = None,
    annotation_version: int | None = None,
    existing_items: dict[str, ReviewItem] | None = None,
    flush: bool = True,
) -> ReviewItem:
    """按 (目标, reason) 幂等地取回或新建待确认项；已解决的项目会被重新打开。"""

    if (quote_id is None) == (gap_id is None):
        raise ValueError("review item 必须且只能有一个目标")
    filters = [ReviewItem.reason == reason]
    filters.append(ReviewItem.quote_id == quote_id if quote_id else ReviewItem.gap_id == gap_id)
    existing = (
        existing_items.get(quote_id or gap_id or "")
        if existing_items is not None
        else session.execute(select(ReviewItem).where(*filters)).scalar_one_or_none()
    )
    if existing is not None:
        if existing.queue_status is ReviewQueueStatus.RESOLVED:
            existing.queue_status = ReviewQueueStatus.PENDING
            existing.resolved_by_correction_id = None
        if candidates is not None:
            existing.candidates_json = json.dumps(candidates, ensure_ascii=False)
        if annotation_version is not None:
            existing.annotation_version = annotation_version
        if flush:
            session.flush()
        return existing
    row = ReviewItem(
        id=new_id(),
        target_type=ReviewTargetType.QUOTE if quote_id else ReviewTargetType.GAP,
        quote_id=quote_id,
        gap_id=gap_id,
        reason=reason,
        candidates_json=json.dumps(candidates or {}, ensure_ascii=False),
        queue_status=ReviewQueueStatus.PENDING,
        annotation_version=annotation_version,
    )
    session.add(row)
    if flush:
        session.flush()
    return row


def resolve_review_items(
    session: Session,
    *,
    quote_id: str | None = None,
    gap_id: str | None = None,
    correction_id: str | None = None,
) -> list[str]:
    """把目标上所有未解决的待确认项标记为已解决，并记录是哪次更正解决的。"""

    filters = [ReviewItem.quote_id == quote_id] if quote_id else [ReviewItem.gap_id == gap_id]
    rows = list(session.execute(select(ReviewItem).where(*filters)).scalars())
    resolved: list[str] = []
    for row in rows:
        if row.queue_status is ReviewQueueStatus.RESOLVED:
            continue
        row.queue_status = ReviewQueueStatus.RESOLVED
        row.resolved_by_correction_id = correction_id
        resolved.append(row.id)
    if resolved:
        session.flush()
    return resolved


def same_window_downstream(
    session: Session,
    *,
    annotation: Annotation,
    exclude_quote_ids: set[str],
    previous_speaker_id: str | None = None,
) -> list[Annotation]:
    """同一个场景里、同一推理窗口（或同一旧分组）产生的下游标注。

    - 只考虑同一窗口（`dependency_hash` 相同）的模型结果：它们与本次更正是同一批联合判断。
    - 若旧分组存在，也纳入仍指向该分组的对白——人工改了一个分组，它的成员需要重新确认。
    - 人工锁定的对白永不改动；调用方仍会再过滤一次。
    """

    if not annotation.scene_id:
        return []
    conditions = []
    if annotation.dependency_hash is not None:
        conditions.append(Annotation.dependency_hash == annotation.dependency_hash)
    if previous_speaker_id is not None:
        conditions.append(Annotation.speaker_id == previous_speaker_id)
    if not conditions:
        return []
    rows = list(
        session.execute(
            select(Annotation).where(
                Annotation.scene_id == annotation.scene_id,
                Annotation.user_locked.is_(False),
                or_(*conditions),
            )
        ).scalars()
    )
    result: list[Annotation] = []
    for row in rows:
        if row.quote_id in exclude_quote_ids or row.quote_id == annotation.quote_id:
            continue
        if row.user_locked:
            continue
        same_window = (
            annotation.dependency_hash is not None
            and row.dependency_hash == annotation.dependency_hash
        )
        same_group = previous_speaker_id is not None and row.speaker_id == previous_speaker_id
        if same_window or same_group:
            result.append(row)
    return result


def mark_stale(
    session: Session,
    annotations: list[Annotation],
    *,
    correction_id: str | None = None,
    exclude_quote_ids: set[str] | None = None,
) -> DownstreamImpact:
    """把标注标为 stale 并建立待确认项；返回受影响范围（不改历史、不动锁定项）。"""

    exclude = exclude_quote_ids or set()
    impact = DownstreamImpact()
    target_ids = [
        row.quote_id for row in annotations if row.quote_id not in exclude and not row.user_locked
    ]
    existing: dict[str, ReviewItem] = {}
    # Keep each IN below SQLite's conservative variable limit, even on old builds.
    for offset in range(0, len(target_ids), 500):
        existing.update(
            {
                row.quote_id: row
                for row in session.scalars(
                    select(ReviewItem).where(
                        ReviewItem.quote_id.in_(target_ids[offset : offset + 500]),
                        ReviewItem.reason == ReviewReason.STALE_DEPENDENCY,
                    )
                )
            }
        )
    for annotation in annotations:
        if annotation.quote_id in exclude:
            continue
        if annotation.user_locked:
            impact.skipped_locked_quote_ids.append(annotation.quote_id)
            continue
        if not annotation.stale:
            annotation.stale = True
        item = upsert_review_item(
            session,
            quote_id=annotation.quote_id,
            reason=ReviewReason.STALE_DEPENDENCY,
            candidates={"correction_id": correction_id},
            annotation_version=annotation.version,
            existing_items=existing,
            flush=False,
        )
        existing[annotation.quote_id] = item
        impact.quote_ids.append(annotation.quote_id)
        impact.review_item_ids.append(item.id)
        if annotation.dependency_hash:
            impact.window_ids.append(annotation.dependency_hash)
    impact.quote_ids = sorted(dict.fromkeys(impact.quote_ids))
    impact.window_ids = sorted(dict.fromkeys(impact.window_ids))
    impact.review_item_ids = list(dict.fromkeys(impact.review_item_ids))
    session.flush()
    return impact
