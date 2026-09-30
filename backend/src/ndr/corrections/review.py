"""待确认队列与对白详情。

- `review_items` 的目标恰好一种（quote 或 gap），同一个目标 + 原因只有一条当前项。
- 用户主动标记（`POST /api/quotes/{id}/review-items`）是幂等的：已解决的项目会被重新打开。
- 详情接口对**任何候选对白**都可用，不要求它已经在队列里。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from ..api.errors import ApiError
from ..domain.corrections import (
    AnnotationStateOut,
    ReviewItemCountsOut,
    ReviewItemDetailOut,
    ReviewItemOut,
    SceneGroupRefOut,
    SceneRefOut,
)
from ..domain.enums import ErrorCode, ReviewQueueStatus, ReviewReason
from ..domain.quotes import QuoteDetailOut
from ..storage.models import Annotation, Gap, Quote, ReviewItem, Scene, SpeakerGroup
from .invalidator import upsert_review_item
from .scenes import label_map_for_scene


def load_json(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return default
    return value


def annotation_out(annotation: Annotation, label_map: Mapping[str, str]) -> AnnotationStateOut:
    return AnnotationStateOut(
        quote_id=annotation.quote_id,
        scene_id=annotation.scene_id,
        kind=annotation.kind,
        assignment=annotation.assignment,
        basis=annotation.basis,
        speaker_group_id=annotation.speaker_id,
        label=label_map.get(annotation.speaker_id or ""),
        status=annotation.status,
        source=annotation.source,
        stale=annotation.stale,
        user_locked=annotation.user_locked,
        visible_from_cp=annotation.visible_from_cp,
        version=annotation.version,
        updated_at=annotation.updated_at.isoformat() if annotation.updated_at else None,
    )


def scene_ref_out(scene: Scene) -> dict[str, Any]:
    return {
        "scene_id": scene.id,
        "status": scene.status.value,
        "start_cp": scene.start_cp,
        "end_cp": scene.end_cp,
        "version": scene.version,
    }


def review_target_text(session: Session, item: ReviewItem, canonical_text: str) -> str:
    """返回队列目标的原文；不依赖前端另行分页加载候选列表。"""

    target = (
        session.get(Quote, item.quote_id)
        if item.quote_id
        else session.get(Gap, item.gap_id)
        if item.gap_id
        else None
    )
    if target is None:
        return ""
    return canonical_text[target.start_cp : target.end_cp]


def review_item_out(item: ReviewItem, *, target_text: str = "") -> ReviewItemOut:
    return ReviewItemOut(
        id=item.id,
        target_type=item.target_type,
        quote_id=item.quote_id,
        gap_id=item.gap_id,
        target_text=target_text,
        reason=item.reason,
        queue_status=item.queue_status,
        candidates=load_json(item.candidates_json, {}),
        annotation_version=item.annotation_version,
        resolved_by_correction_id=item.resolved_by_correction_id,
        version=item.version,
        created_at=item.created_at.isoformat(),
        updated_at=item.updated_at.isoformat(),
    )


def _version_scope(book_version_id: str):  # noqa: ANN202
    quote_ids = select(Quote.id).where(Quote.book_version_id == book_version_id)
    gap_ids = select(Gap.id).where(Gap.book_version_id == book_version_id)
    return or_(ReviewItem.quote_id.in_(quote_ids), ReviewItem.gap_id.in_(gap_ids))


def review_counts(session: Session, book_version_id: str) -> ReviewItemCountsOut:
    # Count in SQLite: large candidate JSON payloads are irrelevant to counters.
    scope = _version_scope(book_version_id)
    target = case(
        (ReviewItem.quote_id.is_not(None), "quote:" + ReviewItem.quote_id),
        else_="gap:" + ReviewItem.gap_id,
    )
    status_rows = session.execute(
        select(
            ReviewItem.queue_status,
            func.count(),
            func.count(func.distinct(target)),
        )
        .where(scope)
        .group_by(ReviewItem.queue_status)
    ).all()
    reason_rows = session.execute(
        select(ReviewItem.reason, func.count()).where(scope).group_by(ReviewItem.reason)
    ).all()
    total_targets = session.scalar(select(func.count(func.distinct(target))).where(scope))
    return ReviewItemCountsOut(
        total=sum(count for _, count, _ in status_rows),
        by_status={status.value: count for status, count, _ in status_rows},
        by_reason={reason.value: count for reason, count in reason_rows},
        targets_total=total_targets or 0,
        targets_by_status={status.value: targets for status, _, targets in status_rows},
    )


def get_review_item_or_404(session: Session, item_id: str) -> ReviewItem:
    item = session.get(ReviewItem, item_id)
    if item is None:
        raise ApiError.not_found("待确认项不存在", review_item_id=item_id)
    return item


def list_review_items(
    session: Session,
    *,
    book_version_id: str,
    chapter_id: str | None = None,
    scene_id: str | None = None,
    reason: ReviewReason | None = None,
    queue_status: ReviewQueueStatus | None = None,
    limit: int = 100,
    cursor: str | None = None,
    canonical_text: str = "",
) -> tuple[list[ReviewItemOut], str | None]:
    from ..api.pagination import chronological_cursor, encode_cursor

    stmt = (
        select(
            ReviewItem,
            func.coalesce(Quote.start_cp, Gap.start_cp),
            func.coalesce(Quote.end_cp, Gap.end_cp),
        )
        .outerjoin(
            Quote,
            ReviewItem.quote_id == Quote.id,
        )
        .outerjoin(Gap, ReviewItem.gap_id == Gap.id)
        .where(_version_scope(book_version_id))
    )
    if chapter_id:
        stmt = stmt.where(
            ReviewItem.quote_id.in_(select(Quote.id).where(Quote.chapter_id == chapter_id))
        )
    if scene_id:
        stmt = stmt.where(
            ReviewItem.quote_id.in_(
                select(Annotation.quote_id).where(Annotation.scene_id == scene_id)
            )
        )
    if reason is not None:
        stmt = stmt.where(ReviewItem.reason == reason)
    if queue_status is not None:
        stmt = stmt.where(ReviewItem.queue_status == queue_status)
    if cursor:
        stmt = stmt.where(chronological_cursor(session, ReviewItem, cursor))
    stmt = stmt.order_by(ReviewItem.created_at, ReviewItem.id).limit(limit + 1)
    rows = list(session.execute(stmt))
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [
        review_item_out(row, target_text=canonical_text[start:end] if start is not None else "")
        for row, start, end in rows
    ]
    next_cursor = (
        encode_cursor([rows[-1][0].created_at.isoformat(), rows[-1][0].id])
        if has_more and rows
        else None
    )
    return items, next_cursor


def review_item_detail(
    session: Session, *, item: ReviewItem, canonical_text: str
) -> ReviewItemDetailOut:
    annotation: Annotation | None = None
    scene: Scene | None = None
    context_before = context_after = ""
    if item.quote_id:
        quote = session.get(Quote, item.quote_id)
        annotation = session.execute(
            select(Annotation).where(Annotation.quote_id == item.quote_id)
        ).scalar_one_or_none()
        if annotation is not None and annotation.scene_id:
            scene = session.get(Scene, annotation.scene_id)
        if quote is not None:
            context_before = canonical_text[max(0, quote.start_cp - 120) : quote.start_cp]
            context_after = canonical_text[quote.end_cp : quote.end_cp + 120]
    elif item.gap_id:
        gap = session.get(Gap, item.gap_id)
        if gap is not None:
            context_before = canonical_text[max(0, gap.start_cp - 120) : gap.start_cp]
            context_after = canonical_text[gap.end_cp : gap.end_cp + 120]
    label_map = label_map_for_scene(session, scene.id) if scene is not None else {}
    return ReviewItemDetailOut(
        item=review_item_out(item, target_text=review_target_text(session, item, canonical_text)),
        annotation=annotation_out(annotation, label_map) if annotation is not None else None,
        scene=scene_ref_out(scene) if scene is not None else None,
        context_before=context_before,
        context_after=context_after,
        allowed_actions=(
            ["assign_existing", "create_speaker", "set_kind", "mark_unknown"]
            if item.quote_id
            else ["set_gap_decision"]
        ),
    )


def flag_review_item(
    session: Session,
    *,
    quote_id: str | None = None,
    gap_id: str | None = None,
    reason: ReviewReason = ReviewReason.USER_FLAGGED,
    note: str = "",
) -> ReviewItem:
    """用户主动标记问题（幂等）：同一个目标 + 原因只有一条当前项。"""

    if quote_id is None and gap_id is None:
        raise ApiError.not_found("标记目标不存在")
    if quote_id is not None and session.get(Quote, quote_id) is None:
        raise ApiError.not_found("候选对白不存在", quote_id=quote_id)
    if gap_id is not None and session.get(Gap, gap_id) is None:
        raise ApiError.not_found("Gap 不存在", gap_id=gap_id)
    annotation = (
        session.execute(
            select(Annotation).where(Annotation.quote_id == quote_id)
        ).scalar_one_or_none()
        if quote_id
        else None
    )
    return upsert_review_item(
        session,
        quote_id=quote_id,
        gap_id=gap_id,
        reason=reason,
        candidates={"note": note, "flagged_by": "user"},
        annotation_version=annotation.version if annotation is not None else None,
    )


def defer_review_item(session: Session, *, item: ReviewItem, note: str = "") -> ReviewItemOut:
    if item.queue_status is ReviewQueueStatus.RESOLVED:
        raise ApiError(
            ErrorCode.RESOURCE_CONFLICT,
            "已解决的待确认项不能只做延后",
            details={"review_item_id": item.id, "queue_status": item.queue_status.value},
            status_code=409,
        )
    if note:
        candidates = load_json(item.candidates_json, {})
        candidates["defer_note"] = note
        item.candidates_json = json.dumps(candidates, ensure_ascii=False)
    item.queue_status = ReviewQueueStatus.DEFERRED
    session.flush()
    return review_item_out(item)


def build_quote_detail(
    session: Session,
    settings: Any,
    version: Any,
    quote_id: str,
    *,
    context_window_cp: int = 120,
) -> QuoteDetailOut:
    """普通对白详情：任何候选都能取，不要求它已经在待确认队列里。

    在候选对白与前置 Gap 的上下文之上补充：当前标注投影、所属场景、已有待确认项、可用的分组编号。
    """

    from ..quotes.service import get_quote_detail

    detail: QuoteDetailOut = get_quote_detail(
        session, settings, version, quote_id, context_window_cp=context_window_cp
    )
    annotation = session.execute(
        select(Annotation).where(Annotation.quote_id == quote_id)
    ).scalar_one_or_none()
    scene = session.get(Scene, annotation.scene_id) if annotation and annotation.scene_id else None
    label_map = label_map_for_scene(session, scene.id) if scene is not None else {}
    items = list(
        session.execute(
            select(ReviewItem)
            .where(ReviewItem.quote_id == quote_id)
            .order_by(ReviewItem.created_at)
        ).scalars()
    )
    detail.annotation = annotation_out(annotation, label_map) if annotation is not None else None
    detail.scene = SceneRefOut(**scene_ref_out(scene)) if scene is not None else None
    detail.review_items = [review_item_out(item) for item in items]
    detail.can_correct = True
    detail.scene_groups = (
        [
            SceneGroupRefOut(
                group_id=group.id,
                label=group.display_label,
                canonical_name=(group.canonical_name or "").strip() or None,
                description=(group.description or "").strip(),
            )
            for group in session.execute(
                select(SpeakerGroup)
                .where(SpeakerGroup.scene_id == scene.id)
                .order_by(SpeakerGroup.display_label)
            ).scalars()
        ]
        if scene is not None
        else []
    )
    return detail


def review_count_map(counts: ReviewItemCountsOut) -> dict[str, int]:
    """给更正响应用的扁平计数：状态值、原因值与 total（键集合天然不重叠）。"""

    return {**counts.by_status, **counts.by_reason, "total": counts.total}
