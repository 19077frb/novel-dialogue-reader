"""人工更正与撤销。

一次请求 = 一个事务 = 一个一致结果：

1. 校验版本（并发旧页面 → 409，不改任何数据）。
2. 写 `corrections`（前值/后值/期望版本/实际版本）。
3. 写 `annotation_history` 旧快照（撤销靠它，历史永不硬删除）。
4. 改当前投影（`USER_CONFIRMED` / `UNKNOWN`）并 `user_locked=true`。
5. 解决选中目标上的待确认项；单句更正不否定其他对白。
6. 不做任何模型调用（本模块不导入任何适配器）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.errors import ApiError
from ..domain.corrections import (
    CorrectionOut,
    GapCorrectionIn,
    GapCorrectionOut,
    QuoteCorrectionIn,
    ReviewItemCountsOut,
    UndoOut,
)
from ..domain.enums import (
    EXPRESSION_OWNER_KINDS,
    AnnotationSource,
    AnnotationStatus,
    Assignment,
    CorrectionAction,
    CorrectionTargetType,
    ErrorCode,
    GapDecision,
    QuoteKind,
    ReviewReason,
    SceneStatus,
    SpeakerBasis,
)
from ..storage.models import (
    Annotation,
    Correction,
    Gap,
    Quote,
    Scene,
    SpeakerGroup,
)
from ..storage.transactions import VersionConflict, check_version
from .history import annotation_snapshot, apply_snapshot, write_annotation_history
from .invalidator import (
    mark_stale,
    resolve_review_items,
    upsert_review_item,
)
from .review import review_count_map, review_counts
from .scenes import (
    ensure_membership,
    ensure_scene_for_quote,
    label_map_for_scene,
    move_annotations_to_scene,
    next_display_label,
    quote_order,
)

CORRECTION_VERSION = "correction-1"
ALLOWED_QUOTE_ACTIONS = {
    CorrectionAction.ASSIGN_EXISTING,
    CorrectionAction.CREATE_SPEAKER,
    CorrectionAction.SET_KIND,
    CorrectionAction.MARK_UNKNOWN,
}


def load_json(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return default
    return value

@dataclass
class QuoteCorrectionOutcome:
    correction_ids: list[str] = field(default_factory=list)
    action: CorrectionAction = CorrectionAction.ASSIGN_EXISTING
    affected_quote_ids: list[str] = field(default_factory=list)
    stale_quote_ids: list[str] = field(default_factory=list)
    stale_window_ids: list[str] = field(default_factory=list)
    created_group_ids: list[str] = field(default_factory=list)
    resolved_review_item_ids: list[str] = field(default_factory=list)
    annotation_versions: dict[str, int] = field(default_factory=dict)
    scene_id: str | None = None
    scene_version: int | None = None


def _resolve_existing_group(session: Session, *, scene_id: str, ref: str) -> SpeakerGroup:
    group = session.get(SpeakerGroup, ref)
    if group is not None and group.scene_id == scene_id:
        return group
    by_label = session.execute(
        select(SpeakerGroup).where(
            SpeakerGroup.scene_id == scene_id, SpeakerGroup.display_label == ref
        )
    ).scalar_one_or_none()
    if by_label is not None:
        return by_label
    if group is not None and group.scene_id != scene_id:
        raise ApiError.validation(
            "该说话人分组属于其它场景，不能跨场景关联",
            reason="CROSS_SCENE_SPEAKER",
            speaker_ref=ref,
            speaker_scene_id=group.scene_id,
            scene_id=scene_id,
        )
    raise ApiError.validation(
        "场景内没有这个说话人分组",
        reason="SPEAKER_NOT_IN_SCENE",
        speaker_ref=ref,
        scene_id=scene_id,
        available_labels=sorted(label_map_for_scene(session, scene_id).values()),
    )


def _get_or_create_annotation(session: Session, *, quote: Quote, scene: Scene) -> Annotation:
    """普通对白也要能人工更正：还没有模型结果时先建一个「未处理」占位标注。"""

    annotation = session.execute(
        select(Annotation).where(Annotation.quote_id == quote.id)
    ).scalar_one_or_none()
    if annotation is not None:
        return annotation
    annotation = Annotation(
        quote_id=quote.id,
        scene_id=scene.id,
        kind=QuoteKind.SPEECH,
        assignment=Assignment.UNKNOWN,
        basis=SpeakerBasis.INSUFFICIENT,
        speaker_id=None,
        status=AnnotationStatus.UNKNOWN,
        source=AnnotationSource.USER,
        evidence_refs_json="[]",
        visible_from_cp=quote.start_cp,
        dependency_hash=None,
        stale=False,
        user_locked=False,
    )
    session.add(annotation)
    session.flush()
    ensure_membership(session, quote_id=quote.id, scene_id=scene.id, revision=scene.version)
    return annotation


def _apply_quote_action(
    session: Session,
    *,
    annotation: Annotation,
    quote: Quote,
    payload: QuoteCorrectionIn,
    scene: Scene,
    created_group_ids: list[str],
    reuse_group_id: str | None,
) -> str | None:
    """按动作改写当前投影；返回 create_speaker 建立（或复用）的分组 ID。"""

    action = CorrectionAction(payload.action)
    if action is CorrectionAction.ASSIGN_EXISTING:
        assert payload.speaker_ref is not None  # schema 已校验
        group = _resolve_existing_group(session, scene_id=scene.id, ref=payload.speaker_ref)
        annotation.kind = payload.kind or QuoteKind.SPEECH
        annotation.assignment = Assignment.EXISTING
        annotation.basis = SpeakerBasis.DIRECT
        annotation.speaker_id = group.id
        annotation.status = AnnotationStatus.USER_CONFIRMED
        return None
    if action is CorrectionAction.CREATE_SPEAKER:
        group_id = reuse_group_id
        if group_id is None:
            group = SpeakerGroup(
                scene_id=scene.id,
                first_quote_id=quote.id,
                display_label=next_display_label(session, scene.id),
                evidence_refs_json="[]",
            )
            session.add(group)
            session.flush()
            group_id = group.id
            created_group_ids.append(group_id)
        annotation.kind = payload.kind or QuoteKind.SPEECH
        annotation.assignment = Assignment.NEW
        annotation.basis = SpeakerBasis.DIRECT
        annotation.speaker_id = group_id
        annotation.status = AnnotationStatus.USER_CONFIRMED
        return group_id
    if action is CorrectionAction.SET_KIND:
        assert payload.kind is not None  # schema 已校验
        annotation.kind = payload.kind
        if payload.kind not in EXPRESSION_OWNER_KINDS:
            annotation.assignment = None
            annotation.basis = None
            annotation.speaker_id = None
            annotation.status = AnnotationStatus.USER_CONFIRMED
            return None
        if payload.speaker_ref:
            group = _resolve_existing_group(session, scene_id=scene.id, ref=payload.speaker_ref)
            annotation.assignment = Assignment.EXISTING
            annotation.basis = SpeakerBasis.DIRECT
            annotation.speaker_id = group.id
            annotation.status = AnnotationStatus.USER_CONFIRMED
        elif annotation.speaker_id:
            annotation.assignment = Assignment.EXISTING
            annotation.basis = annotation.basis or SpeakerBasis.DIRECT
            annotation.status = AnnotationStatus.USER_CONFIRMED
        else:
            annotation.assignment = Assignment.UNKNOWN
            annotation.basis = SpeakerBasis.INSUFFICIENT
            annotation.status = AnnotationStatus.UNKNOWN
        return None
    # MARK_UNKNOWN：锁定「未知」本身，不新建人物、不改类型
    annotation.speaker_id = None
    annotation.status = AnnotationStatus.UNKNOWN
    if annotation.kind in EXPRESSION_OWNER_KINDS:
        annotation.assignment = Assignment.UNKNOWN
        annotation.basis = SpeakerBasis.INSUFFICIENT
    else:
        annotation.assignment = None
        annotation.basis = None
    return None

def apply_quote_correction(
    session: Session, *, target: Quote, payload: QuoteCorrectionIn
) -> QuoteCorrectionOutcome:
    action = CorrectionAction(payload.action)
    if action not in ALLOWED_QUOTE_ACTIONS:
        raise ApiError.validation("该动作不属于对白更正", action=action.value)

    scene = ensure_scene_for_quote(
        session, book_version_id=target.book_version_id, quote=target
    )
    ordered = quote_order(session, [target.id, *(payload.quote_ids or [])])
    # 显式多目标必须落在同一个场景，避免把别的场景的对白误关联过来
    for quote_id in ordered:
        quote = session.get(Quote, quote_id)
        if quote is None or quote.book_version_id != target.book_version_id:
            raise ApiError.validation("对白不存在或不属于当前书籍版本", quote_id=quote_id)
        if quote.id == target.id:
            continue
        other_scene = ensure_scene_for_quote(
            session, book_version_id=quote.book_version_id, quote=quote
        )
        if other_scene.id != scene.id:
            raise ApiError.validation(
                "一次更正只能覆盖同一个场景内的对白",
                reason="CROSS_SCENE_SCOPE",
                quote_id=quote_id,
                scene_id=scene.id,
                other_scene_id=other_scene.id,
            )
    if payload.expected_scene_version is not None:
        check_version(scene, payload.expected_scene_version)

    outcome = QuoteCorrectionOutcome(action=action, scene_id=scene.id)
    reuse_group_id: str | None = None
    for index, quote_id in enumerate(ordered):
        quote = session.get(Quote, quote_id)
        assert quote is not None
        annotation = _get_or_create_annotation(session, quote=quote, scene=scene)
        if index == 0:
            check_version(annotation, payload.expected_version)
        before = annotation_snapshot(annotation)
        correction = Correction(
            target_type=CorrectionTargetType.QUOTE,
            target_id=quote.id,
            action=action,
            before_json=json.dumps(before, ensure_ascii=False),
            after_json="{}",
            expected_version=payload.expected_version if index == 0 else annotation.version,
            applied_version=None,
        )
        session.add(correction)
        session.flush()
        write_annotation_history(session, annotation, correction_id=correction.id)
        reuse_group_id = _apply_quote_action(
            session,
            annotation=annotation,
            quote=quote,
            payload=payload,
            scene=scene,
            created_group_ids=outcome.created_group_ids,
            reuse_group_id=reuse_group_id,
        )
        annotation.source = AnnotationSource.USER
        annotation.user_locked = True
        annotation.stale = False
        annotation.version = int(annotation.version) + 1
        session.flush()
        correction.after_json = json.dumps(annotation_snapshot(annotation), ensure_ascii=False)
        correction.applied_version = annotation.version

        outcome.correction_ids.append(correction.id)
        outcome.affected_quote_ids.append(quote.id)
        outcome.annotation_versions[quote.id] = annotation.version
        outcome.resolved_review_item_ids.extend(
            resolve_review_items(session, quote_id=quote.id, correction_id=correction.id)
        )

    if outcome.created_group_ids:
        # 参与者结构变了：场景版本递增，供场景级并发校验使用
        scene.version = int(scene.version) + 1
        session.flush()
    outcome.scene_version = scene.version
    return outcome


def correction_out(outcome: QuoteCorrectionOutcome, counts: ReviewItemCountsOut) -> CorrectionOut:
    return CorrectionOut(
        correction_id=outcome.correction_ids[0] if outcome.correction_ids else "",
        correction_ids=list(outcome.correction_ids),
        action=outcome.action,
        target_type=CorrectionTargetType.QUOTE,
        target_id=outcome.affected_quote_ids[0] if outcome.affected_quote_ids else "",
        affected_quote_ids=list(outcome.affected_quote_ids),
        stale_quote_ids=list(outcome.stale_quote_ids),
        stale_window_ids=list(outcome.stale_window_ids),
        created_group_ids=list(outcome.created_group_ids),
        resolved_review_item_ids=list(dict.fromkeys(outcome.resolved_review_item_ids)),
        annotation_versions=dict(outcome.annotation_versions),
        scene_id=outcome.scene_id,
        scene_version=outcome.scene_version,
        updated_review_counts=review_count_map(counts),
    )


def review_counts_for(session: Session, book_version_id: str) -> ReviewItemCountsOut:
    return review_counts(session, book_version_id)

@dataclass
class GapCorrectionOutcome:
    correction_id: str = ""
    gap_id: str = ""
    decision: GapDecision = GapDecision.CONTINUE
    previous_decision: GapDecision = GapDecision.CONTINUE
    affected_quote_ids: list[str] = field(default_factory=list)
    stale_quote_ids: list[str] = field(default_factory=list)
    stale_window_ids: list[str] = field(default_factory=list)
    closed_scene_ids: list[str] = field(default_factory=list)
    opened_scene_id: str | None = None
    resolved_review_item_ids: list[str] = field(default_factory=list)
    created_review_item_ids: list[str] = field(default_factory=list)


def annotations_by_position(
    session: Session, *, book_version_id: str, start_cp: int | None = None, descending: bool = False
) -> list[Annotation]:
    stmt = (
        select(Annotation)
        .join(Quote, Annotation.quote_id == Quote.id)
        .where(Quote.book_version_id == book_version_id, Annotation.scene_id.is_not(None))
    )
    if start_cp is not None:
        stmt = stmt.where(
            Quote.start_cp >= start_cp if not descending else Quote.start_cp < start_cp
        )
    stmt = stmt.order_by(Quote.start_cp.desc() if descending else Quote.start_cp)
    return list(session.execute(stmt).scalars())


def apply_gap_correction(
    session: Session, *, gap: Gap, payload: GapCorrectionIn
) -> GapCorrectionOutcome:
    """用户确认 Gap 的 CONTINUE/UPDATE/BREAK/UNCERTAIN，并返回场景修订影响。

    - BREAK：关闭左侧场景，把 gap 之后仍属于它的引语移入新场景（按新场景重新编号）。
    - CONTINUE/UPDATE：若右侧被模型切到了别的场景，合并回左侧场景（被合并场景标记为已关闭）。
    - UNCERTAIN：不做结构调整，只把边界问题留在待确认队列里。
    """

    previous = gap.proposed_decision
    outcome = GapCorrectionOutcome(
        gap_id=gap.id, decision=payload.decision, previous_decision=previous
    )

    left_scene: Scene | None = None
    for annotation in annotations_by_position(
        session, book_version_id=gap.book_version_id, start_cp=gap.start_cp, descending=True
    ):
        if annotation.scene_id:
            left_scene = session.get(Scene, annotation.scene_id)
            break
    if payload.expected_scene_version is not None and left_scene is not None:
        check_version(left_scene, payload.expected_scene_version)

    movable: list[Annotation] = []
    if left_scene is not None:
        movable = [
            row
            for row in annotations_by_position(
                session, book_version_id=gap.book_version_id, start_cp=gap.start_cp
            )
            if row.scene_id == left_scene.id
        ]

    before = {
        "proposed_decision": previous.value,
        "scene_id": left_scene.id if left_scene is not None else None,
        "scene_version": left_scene.version if left_scene is not None else None,
        "speaker_map": {row.quote_id: row.speaker_id for row in movable},
    }
    correction = Correction(
        target_type=CorrectionTargetType.GAP,
        target_id=gap.id,
        action=CorrectionAction.SET_GAP_DECISION,
        before_json=json.dumps(before, ensure_ascii=False),
        after_json="{}",
        expected_version=payload.expected_scene_version,
        applied_version=None,
    )
    session.add(correction)
    session.flush()
    outcome.correction_id = correction.id
    after: dict[str, Any] = {"proposed_decision": payload.decision.value}

    if payload.decision is GapDecision.BREAK and left_scene is not None:
        for annotation in movable:
            write_annotation_history(session, annotation, correction_id=correction.id)
        left_scene.status = SceneStatus.CLOSED
        left_scene.end_cp = gap.start_cp
        left_scene.version = int(left_scene.version) + 1
        session.flush()
        outcome.closed_scene_ids.append(left_scene.id)
        if movable:
            starts = [
                quote.start_cp
                for quote in session.execute(
                    select(Quote).where(Quote.id.in_([row.quote_id for row in movable]))
                ).scalars()
            ]
            new_scene = Scene(
                book_version_id=gap.book_version_id,
                start_cp=min(starts),
                end_cp=None,
                status=SceneStatus.OPEN,
            )
            session.add(new_scene)
            session.flush()
            move = move_annotations_to_scene(
                session, scene=new_scene, quote_ids=[row.quote_id for row in movable]
            )
            outcome.affected_quote_ids = move.affected_quote_ids
            outcome.opened_scene_id = new_scene.id
            after.update(
                {
                    "opened_scene_id": new_scene.id,
                    "moved_quote_ids": move.affected_quote_ids,
                    "created_group_ids": move.created_group_ids,
                }
            )
            impact = mark_stale(
                session,
                list(
                    session.execute(
                        select(Annotation).where(
                            Annotation.quote_id.in_(move.affected_quote_ids or [""])
                        )
                    ).scalars()
                ),
                correction_id=correction.id,
            )
            outcome.stale_quote_ids = impact.quote_ids
            outcome.stale_window_ids = impact.window_ids
    elif payload.decision in {GapDecision.CONTINUE, GapDecision.UPDATE}:
        right_scene: Scene | None = None
        if left_scene is not None:
            for annotation in annotations_by_position(
                session, book_version_id=gap.book_version_id, start_cp=gap.start_cp
            ):
                if annotation.scene_id and annotation.scene_id != left_scene.id:
                    right_scene = session.get(Scene, annotation.scene_id)
                    break
        if left_scene is not None and right_scene is not None:
            right_annotations = list(
                session.execute(
                    select(Annotation).where(Annotation.scene_id == right_scene.id)
                ).scalars()
            )
            for annotation in right_annotations:
                write_annotation_history(session, annotation, correction_id=correction.id)
            left_scene.status = SceneStatus.OPEN
            left_scene.end_cp = None
            left_scene.version = int(left_scene.version) + 1
            right_scene.status = SceneStatus.CLOSED
            right_scene.end_cp = right_scene.start_cp
            right_scene.version = int(right_scene.version) + 1
            session.flush()
            move = move_annotations_to_scene(
                session, scene=left_scene, quote_ids=[row.quote_id for row in right_annotations]
            )
            outcome.affected_quote_ids = move.affected_quote_ids
            outcome.closed_scene_ids.append(right_scene.id)
            after.update(
                {
                    "merged_scene_id": right_scene.id,
                    "moved_quote_ids": move.affected_quote_ids,
                    "created_group_ids": move.created_group_ids,
                }
            )
            impact = mark_stale(
                session,
                list(
                    session.execute(
                        select(Annotation).where(
                            Annotation.quote_id.in_(move.affected_quote_ids or [""])
                        )
                    ).scalars()
                ),
                correction_id=correction.id,
            )
            outcome.stale_quote_ids = impact.quote_ids
            outcome.stale_window_ids = impact.window_ids

    gap.proposed_decision = payload.decision
    session.flush()
    correction.after_json = json.dumps(after, ensure_ascii=False)
    correction.applied_version = left_scene.version if left_scene is not None else None

    if payload.decision is GapDecision.UNCERTAIN:
        item = upsert_review_item(
            session,
            gap_id=gap.id,
            reason=ReviewReason.SCENE_BOUNDARY,
            candidates={"decision": payload.decision.value},
        )
        outcome.created_review_item_ids.append(item.id)
    else:
        outcome.resolved_review_item_ids.extend(
            resolve_review_items(session, gap_id=gap.id, correction_id=correction.id)
        )
    return outcome


def gap_correction_out(
    outcome: GapCorrectionOutcome, counts: ReviewItemCountsOut
) -> GapCorrectionOut:
    return GapCorrectionOut(
        correction_id=outcome.correction_id,
        gap_id=outcome.gap_id,
        decision=outcome.decision,
        previous_decision=outcome.previous_decision,
        affected_quote_ids=list(outcome.affected_quote_ids),
        stale_quote_ids=list(outcome.stale_quote_ids),
        closed_scene_ids=list(outcome.closed_scene_ids),
        opened_scene_id=outcome.opened_scene_id,
        resolved_review_item_ids=list(dict.fromkeys(outcome.resolved_review_item_ids)),
        created_review_item_ids=list(dict.fromkeys(outcome.created_review_item_ids)),
        updated_review_counts=review_count_map(counts),
    )

@dataclass
class UndoOutcome:
    correction_id: str = ""
    undo_correction_id: str = ""
    target_type: CorrectionTargetType = CorrectionTargetType.QUOTE
    target_id: str = ""
    restored: dict[str, Any] = field(default_factory=dict)
    affected_quote_ids: list[str] = field(default_factory=list)
    stale_quote_ids: list[str] = field(default_factory=list)
    stale_window_ids: list[str] = field(default_factory=list)


def get_correction_or_404(session: Session, correction_id: str) -> Correction:
    row = session.get(Correction, correction_id)
    if row is None:
        raise ApiError.not_found("更正记录不存在", correction_id=correction_id)
    return row


def annotation_map(session: Session, quote_ids: list[str]) -> dict[str, Annotation]:
    return {
        row.quote_id: row
        for row in session.execute(
            select(Annotation).where(Annotation.quote_id.in_(quote_ids or [""]))
        ).scalars()
    }


def undo_correction(session: Session, *, correction: Correction) -> UndoOutcome:
    """撤销一次人工更正；目标必须仍停在这次更正产生的版本上。

    - 版本不一致（其它页面又改过、或已有更新的修订）→ 409，且不改动任何数据。
    - 撤销不是硬删除：写一条 `action=undo` 的更正记录 + 一条标注历史，再恢复旧快照。
    """

    if correction.undone_by:
        raise ApiError(
            ErrorCode.VERSION_CONFLICT,
            "该更正已经被撤销过",
            details={
                "correction_id": correction.id,
                "undone_by": correction.undone_by,
                "reason": "ALREADY_UNDONE",
            },
            status_code=409,
        )

    outcome = UndoOutcome(
        correction_id=correction.id,
        target_type=correction.target_type,
        target_id=correction.target_id,
    )
    before = load_json(correction.before_json, {})
    after = load_json(correction.after_json, {})

    if correction.target_type in {CorrectionTargetType.QUOTE, CorrectionTargetType.ANNOTATION}:
        annotation = session.execute(
            select(Annotation).where(Annotation.quote_id == correction.target_id)
        ).scalar_one_or_none()
        if annotation is None:
            raise ApiError.not_found("标注已不存在，无法撤销", quote_id=correction.target_id)
        if (
            correction.applied_version is not None
            and annotation.version != correction.applied_version
        ):
            raise VersionConflict(
                entity="Annotation",
                target_id=annotation.id,
                expected=correction.applied_version,
                current=annotation.version,
            )
        undo_row = Correction(
            target_type=correction.target_type,
            target_id=correction.target_id,
            action=CorrectionAction.UNDO,
            before_json=json.dumps(annotation_snapshot(annotation), ensure_ascii=False),
            after_json="{}",
            expected_version=annotation.version,
            applied_version=None,
        )
        session.add(undo_row)
        session.flush()
        write_annotation_history(session, annotation, correction_id=undo_row.id)
        apply_snapshot(annotation, before)
        annotation.version = int(annotation.version) + 1
        session.flush()
        undo_row.after_json = json.dumps(annotation_snapshot(annotation), ensure_ascii=False)
        undo_row.applied_version = annotation.version
        correction.undone_by = undo_row.id
        outcome.undo_correction_id = undo_row.id
        outcome.restored = before
        outcome.affected_quote_ids = [annotation.quote_id]
        resolve_review_items(session, quote_id=annotation.quote_id, correction_id=undo_row.id)
        session.flush()
        return outcome

    if correction.target_type is CorrectionTargetType.GAP:
        gap = session.get(Gap, correction.target_id)
        if gap is None:
            raise ApiError.not_found("Gap 已不存在，无法撤销", gap_id=correction.target_id)
        scene = session.get(Scene, before["scene_id"]) if before.get("scene_id") else None
        if (
            correction.applied_version is not None
            and scene is not None
            and scene.version != correction.applied_version
        ):
            raise VersionConflict(
                entity="Scene",
                target_id=scene.id,
                expected=correction.applied_version,
                current=scene.version,
            )
        moved = list(after.get("moved_quote_ids") or [])
        opened_scene_id = after.get("opened_scene_id")
        current = {
            "proposed_decision": gap.proposed_decision.value,
            "scene_id": before.get("scene_id"),
            "scene_version": scene.version if scene is not None else None,
            "speaker_map": {
                quote_id: annotation.speaker_id
                for quote_id, annotation in annotation_map(session, moved).items()
            },
        }
        undo_row = Correction(
            target_type=CorrectionTargetType.GAP,
            target_id=correction.target_id,
            action=CorrectionAction.UNDO,
            before_json=json.dumps(current, ensure_ascii=False),
            after_json="{}",
            expected_version=correction.applied_version,
            applied_version=None,
        )
        session.add(undo_row)
        session.flush()
        gap.proposed_decision = GapDecision(
            before.get("proposed_decision", gap.proposed_decision.value)
        )
        if scene is not None and moved:
            scene.status = SceneStatus.OPEN
            scene.end_cp = None
            scene.version = int(scene.version) + 1
            if opened_scene_id:
                opened = session.get(Scene, opened_scene_id)
                if opened is not None:
                    opened.status = SceneStatus.CLOSED
                    opened.end_cp = opened.start_cp
                    opened.version = int(opened.version) + 1
            session.flush()
            for annotation in annotation_map(session, moved).values():
                write_annotation_history(session, annotation, correction_id=undo_row.id)
            move = move_annotations_to_scene(
                session,
                scene=scene,
                quote_ids=moved,
                speaker_map=dict(before.get("speaker_map") or {}),
                stale=False,
            )
            outcome.affected_quote_ids = move.affected_quote_ids
        undo_row.after_json = json.dumps(
            {
                "proposed_decision": gap.proposed_decision.value,
                "scene_version": scene.version if scene is not None else None,
            },
            ensure_ascii=False,
        )
        undo_row.applied_version = scene.version if scene is not None else None
        correction.undone_by = undo_row.id
        outcome.undo_correction_id = undo_row.id
        outcome.restored = {"proposed_decision": gap.proposed_decision.value}
        resolve_review_items(session, gap_id=gap.id, correction_id=undo_row.id)
        session.flush()
        return outcome

    # SCENE：merge / split 的撤销（按 quote_id 精确还原 speaker_id）
    scene = session.get(Scene, correction.target_id)
    if scene is None:
        raise ApiError.not_found("场景已不存在，无法撤销", scene_id=correction.target_id)
    if correction.applied_version is not None and scene.version != correction.applied_version:
        raise VersionConflict(
            entity="Scene",
            target_id=scene.id,
            expected=correction.applied_version,
            current=scene.version,
        )
    speaker_map = {key: value for key, value in (before.get("speaker_map") or {}).items()}
    if not speaker_map:
        raise ApiError.validation("该更正没有可还原的分组归属", correction_id=correction.id)
    undo_row = Correction(
        target_type=CorrectionTargetType.SCENE,
        target_id=correction.target_id,
        action=CorrectionAction.UNDO,
        before_json=json.dumps(after, ensure_ascii=False),
        after_json="{}",
        expected_version=scene.version,
        applied_version=None,
    )
    session.add(undo_row)
    session.flush()
    for annotation in annotation_map(session, list(speaker_map)).values():
        write_annotation_history(session, annotation, correction_id=undo_row.id)
    move = move_annotations_to_scene(
        session,
        scene=scene,
        quote_ids=list(speaker_map),
        speaker_map=speaker_map,
        stale=False,
    )
    scene.version = int(scene.version) + 1
    session.flush()
    undo_row.after_json = json.dumps(
        {"speaker_map": move.new_speaker_map, "scene_version": scene.version},
        ensure_ascii=False,
    )
    undo_row.applied_version = scene.version
    correction.undone_by = undo_row.id
    outcome.undo_correction_id = undo_row.id
    outcome.restored = {"speaker_map": move.new_speaker_map}
    outcome.affected_quote_ids = move.affected_quote_ids
    session.flush()
    return outcome


def undo_out(outcome: UndoOutcome, counts: ReviewItemCountsOut) -> UndoOut:
    return UndoOut(
        correction_id=outcome.correction_id,
        undo_correction_id=outcome.undo_correction_id,
        target_type=outcome.target_type,
        target_id=outcome.target_id,
        restored=outcome.restored,
        affected_quote_ids=list(outcome.affected_quote_ids),
        stale_quote_ids=list(outcome.stale_quote_ids),
        updated_review_counts=review_count_map(counts),
    )


def recheck_range(session: Session, *, quote: Quote, version: Any) -> dict[str, Any]:
    """局部复核的范围：优先当前场景（含前后文），其次章节，最后整本。

    只返回范围，不写数据库：是否真的调用模型由任务与预算决定。
    """

    from ..storage.models import Chapter

    annotation = session.execute(
        select(Annotation).where(Annotation.quote_id == quote.id)
    ).scalar_one_or_none()
    if annotation is not None and annotation.scene_id:
        scene = session.get(Scene, annotation.scene_id)
        if scene is not None:
            payload: dict[str, Any] = {"start_cp": scene.start_cp}
            if scene.end_cp is not None:
                payload["end_cp"] = scene.end_cp
            else:
                payload["end_cp"] = version.canonical_length_cp
            return payload
    if quote.chapter_id:
        chapter = session.get(Chapter, quote.chapter_id)
        if chapter is not None:
            return {"start_cp": chapter.start_cp, "end_cp": chapter.end_cp}
    return {"start_cp": 0, "end_cp": version.canonical_length_cp}
