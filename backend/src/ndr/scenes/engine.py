"""单窗口推理流程。

顺序严格按 4.5：

1. 解析 JSON/schema。
2. 程序校验：目标覆盖、唯一 ID、场景/分组一致性、临时引用与证据存在性。
3. 校验人工锁定：锁定的对白**不采纳**模型结果（模型结果只进历史或被丢弃）。
4. 由后端按证据计算可见时点（模型自报不可信）。
5. 冷启动保守接受策略：ACCEPTED / PROVISIONAL / UNKNOWN。
6. 一个事务内提交：场景、分组、标注（含历史）、归属、Gap 转移、身份修订、待确认项。

引擎不调用网络：调用方负责发请求，把输出交给这里。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.enums import (
    AnnotationSource,
    AnnotationStatus,
    Assignment,
    GapDecision,
    IdentityOperation,
    QuoteKind,
    ReviewQueueStatus,
    ReviewReason,
    ReviewTargetType,
    SceneStatus,
    SpeakerBasis,
)
from ..llm.schemas import IdentityProposal, LlmOutput
from ..llm.validation import LabelingTargets, parse_and_validate
from ..speakers.groups import SpeakerRegistry
from ..speakers.revisions import IdentityProposalView, evaluate_identity_proposal
from ..storage.models import (
    Annotation,
    AnnotationHistory,
    BookCharacter,
    IdentityRevision,
    ReviewItem,
    Scene,
    SceneMembership,
    SpeakerGroup,
)
from .acceptance import compute_visible_from_cp, decide_acceptance
from .state import SCENE_STATE_VERSION, SceneState

ENGINE_VERSION = "attribution-engine-1"


@dataclass
class WindowApplication:
    window_id: str
    scene_state: SceneState
    validation_ok: bool
    validation_codes: list[str] = field(default_factory=list)
    scene_id: str | None = None
    closed_scene_ids: list[str] = field(default_factory=list)
    created_group_ids: list[str] = field(default_factory=list)
    annotation_ids: list[str] = field(default_factory=list)
    review_item_ids: list[str] = field(default_factory=list)
    identity_revision_ids: list[str] = field(default_factory=list)
    skipped_locked_quote_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "window_id": self.window_id,
            "validation_ok": self.validation_ok,
            "validation_codes": list(self.validation_codes),
            "scene_id": self.scene_id,
            "closed_scene_ids": list(self.closed_scene_ids),
            "created_group_ids": list(self.created_group_ids),
            "annotation_ids": list(self.annotation_ids),
            "review_item_ids": list(self.review_item_ids),
            "identity_revision_ids": list(self.identity_revision_ids),
            "skipped_locked_quote_ids": list(self.skipped_locked_quote_ids),
            "warnings": list(self.warnings),
            "scene_state": self.scene_state.snapshot(),
            "stats": self.stats,
        }


def _annotation_snapshot(annotation: Annotation) -> dict[str, Any]:
    return {
        "quote_id": annotation.quote_id,
        "scene_id": annotation.scene_id,
        "kind": annotation.kind.value,
        "assignment": annotation.assignment.value if annotation.assignment else None,
        "basis": annotation.basis.value if annotation.basis else None,
        "speaker_id": annotation.speaker_id,
        "status": annotation.status.value,
        "source": annotation.source.value,
        "visible_from_cp": annotation.visible_from_cp,
        "stale": annotation.stale,
        "user_locked": annotation.user_locked,
        "version": annotation.version,
    }


def _write_history(session: Session, annotation: Annotation, *, run_id: str | None = None) -> None:
    session.add(
        AnnotationHistory(
            annotation_id=annotation.id,
            revision=annotation.version,
            snapshot_json=json.dumps(_annotation_snapshot(annotation), ensure_ascii=False),
            visible_from_cp=annotation.visible_from_cp,
            run_id=run_id,
        )
    )


def _ensure_scene(session: Session, state: SceneState, book_version_id: str) -> str:
    if state.scene_id:
        return state.scene_id
    row = Scene(
        book_version_id=book_version_id,
        start_cp=state.start_cp,
        end_cp=None,
        status=SceneStatus.OPEN,
    )
    session.add(row)
    session.flush()
    state.scene_id = row.id
    return row.id


def _close_scene(session: Session, scene_id: str, *, end_cp: int, gap_id: str | None) -> None:
    row = session.get(Scene, scene_id)
    if row is None:
        return
    row.status = SceneStatus.CLOSED
    row.end_cp = end_cp
    row.version += 1
    session.flush()


def _ensure_group(
    session: Session,
    *,
    state: SceneState,
    slot,  # noqa: ANN001 - SpeakerSlot
    scene_id: str,
) -> str:
    # Book-wide manual edits remain authoritative even for a resumed window.
    character = session.get(BookCharacter, slot.character_id) if slot.character_id else None
    if character is not None and character.user_confirmed:
        slot.canonical_name = character.canonical_name or ""
        slot.description = character.description or ""
    if slot.group_id:
        row = session.get(SpeakerGroup, slot.group_id)
        if row is not None:
            row.canonical_name = slot.canonical_name or None
            row.description = slot.description or None
            row.character_id = slot.character_id
        return slot.group_id
    row = SpeakerGroup(
        scene_id=scene_id,
        first_quote_id=slot.first_quote_id or None,
        display_label=slot.display_label,
        canonical_name=slot.canonical_name or None,
        description=slot.description or None,
        character_id=slot.character_id,
        evidence_refs_json=json.dumps(list(slot.evidence_refs), ensure_ascii=False),
    )
    session.add(row)
    session.flush()
    slot.group_id = row.id
    return row.id


def _record_annotation(
    session: Session,
    *,
    quote_id: str,
    scene_id: str | None,
    kind: QuoteKind,
    assignment: Assignment | None,
    basis,  # noqa: ANN001 - SpeakerBasis | None
    speaker_id: str | None,
    status: AnnotationStatus,
    source: AnnotationSource,
    evidence_refs: Sequence[str],
    visible_from_cp: int,
    dependency_hash: str,
    run_id: str | None = None,
) -> tuple[Annotation, bool]:
    """写入当前标注投影；返回 ``(annotation, replaced_existing)``。"""

    existing = session.execute(
        select(Annotation).where(Annotation.quote_id == quote_id)
    ).scalar_one_or_none()
    payload = {
        "scene_id": scene_id,
        "kind": kind,
        "assignment": assignment,
        "basis": basis,
        "speaker_id": speaker_id,
        "status": status,
        "source": source,
        "evidence_refs_json": json.dumps(list(evidence_refs), ensure_ascii=False),
        "visible_from_cp": visible_from_cp,
        "dependency_hash": dependency_hash,
        "stale": False,
    }
    if existing is None:
        row = Annotation(
            quote_id=quote_id,
            user_locked=False,
            **payload,
        )
        session.add(row)
        session.flush()
        return row, False

    _write_history(session, existing, run_id=run_id)
    for key, value in payload.items():
        setattr(existing, key, value)
    existing.version += 1
    session.flush()
    return existing, True


def _member_of_scene(session: Session, *, quote_id: str, scene_id: str, revision: int) -> None:
    existing = session.execute(
        select(SceneMembership).where(
            SceneMembership.quote_id == quote_id,
            SceneMembership.scene_id == scene_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return
    session.add(
        SceneMembership(
            quote_id=quote_id,
            scene_id=scene_id,
            valid_from_revision=revision,
        )
    )


def _upsert_review_item(
    session: Session,
    *,
    quote_id: str | None,
    gap_id: str | None,
    reason: ReviewReason,
    candidates: Mapping[str, Any] | None = None,
    annotation_version: int | None = None,
) -> str:
    target_filter = [ReviewItem.quote_id == quote_id] if quote_id else [ReviewItem.gap_id == gap_id]
    existing = session.execute(
        select(ReviewItem).where(ReviewItem.reason == reason, *target_filter)
    ).scalar_one_or_none()
    if existing is not None:
        existing.queue_status = ReviewQueueStatus.PENDING
        if candidates is not None:
            existing.candidates_json = json.dumps(candidates, ensure_ascii=False)
        session.flush()
        return existing.id
    row = ReviewItem(
        target_type=ReviewTargetType.QUOTE if quote_id else ReviewTargetType.GAP,
        quote_id=quote_id,
        gap_id=gap_id,
        reason=reason,
        candidates_json=json.dumps(candidates or {}, ensure_ascii=False),
        queue_status=ReviewQueueStatus.PENDING,
        annotation_version=annotation_version,
    )
    session.add(row)
    session.flush()
    return row.id


def _resolve_speaker(
    registry: SpeakerRegistry,
    *,
    label,  # noqa: ANN001 - QuoteLabel
    visible_from_cp: int,
) -> tuple[str | None, str | None]:
    """解析 label 的说话人；返回 ``(speaker_id, warning)``。"""

    if label.assignment is Assignment.UNKNOWN or label.speaker_ref is None:
        return None, None
    slot = registry.resolve(label.speaker_ref)
    if slot is None:
        return None, f"unknown_speaker_ref:{label.speaker_ref}"
    return slot.group_id, None


def apply_window(
    session: Session,
    *,
    book_version_id: str,
    window,  # noqa: ANN001 - ProcessingWindow
    output: LlmOutput | Mapping[str, Any] | str,
    state: SceneState | None = None,
    quote_positions: Mapping[str, tuple[int, int]] | None = None,
    gap_positions: Mapping[str, int] | None = None,
    evidence_positions: Mapping[str, int] | None = None,
    locked_quote_ids: set[str] | None = None,
    locked_group_ids: set[str] | None = None,
    dependency_hash: str = "",
    source: AnnotationSource = AnnotationSource.MODEL,
    cold_start: bool = True,
    run_id: str | None = None,
    update_dependency_hash: bool = True,
) -> WindowApplication:
    """应用一次模型输出：校验 → 锁定检查 → 可见时点 → 接受策略 → 落库。"""

    state = state or SceneState()
    state.sync_confirmed_participants()
    registry = SpeakerRegistry(state)
    quote_positions = dict(quote_positions or {})
    gap_positions = dict(gap_positions or {})
    evidence_positions = dict(evidence_positions or {})
    locked_quote_ids = set(locked_quote_ids or ())

    targets = LabelingTargets(
        quote_ids=tuple(window.target_quote_ids),
        gap_ids=tuple(
            fragment.fragment_id
            for fragment in window.fragments
            if fragment.kind.value in {"inner_gap", "outer_gap"}
        ),
        scene_refs=(state.scene_ref,),
        speaker_refs=tuple(
            ref
            for slot in state.participants
            for ref in (slot.display_label, slot.group_id)
            if ref
        ),
        evidence_ids=tuple(window.fragment_ids),
    )
    report = parse_and_validate(output, targets)
    application = WindowApplication(
        window_id=window.window_id,
        scene_state=state,
        validation_ok=report.ok,
        validation_codes=report.error_codes,
    )
    if not report.ok or report.output is None:
        application.warnings.extend(report.messages[:5])
        return application

    parsed = report.output
    accepted = report.accepted_labels
    effective_hash = (
        dependency_hash or getattr(window, "dependency_hash", "")
    )

    # 事件顺序：按文档位置把 Gap 决策与对白标签交错处理
    events: list[tuple[int, str, Any]] = []
    for decision in parsed.gap_decisions:
        events.append((gap_positions.get(decision.gap_id, 0), "gap", decision))
    for label in accepted:
        start_cp = quote_positions.get(label.quote_id, (0, 0))[0]
        events.append((start_cp, "label", label))
    events.sort(key=lambda item: (item[0], 0 if item[1] == "gap" else 1))

    scene_update_refs = {update.after_gap_id: update.temp_ref for update in parsed.scene_updates}

    for _position, kind, payload in events:
        if kind == "gap":
            decision = payload
            previous_scene_id = state.scene_id
            next_quote_start = None
            for label in accepted:
                start_cp = quote_positions.get(label.quote_id, (0, 0))[0]
                if start_cp >= gap_positions.get(decision.gap_id, 0):
                    next_quote_start = start_cp
                    break
            transition = state.apply_gap(
                decision=decision.decision,
                gap_id=decision.gap_id,
                next_quote_id=None,
                next_quote_start_cp=next_quote_start,
                position_cp=gap_positions.get(decision.gap_id, state.start_cp),
            )
            if transition.closed_scene and previous_scene_id:
                _close_scene(
                    session,
                    previous_scene_id,
                    end_cp=gap_positions.get(decision.gap_id, state.start_cp),
                    gap_id=decision.gap_id,
                )
                application.closed_scene_ids.append(previous_scene_id)
            if transition.opened_scene:
                new_ref = scene_update_refs.get(decision.gap_id)
                if new_ref:
                    state.scene_ref = new_ref
                scene_id = _ensure_scene(session, state, book_version_id)
                application.scene_id = scene_id
            continue

        label = payload
        if label.quote_id in locked_quote_ids:
            application.skipped_locked_quote_ids.append(label.quote_id)
            application.warnings.append(f"locked_quote_kept:{label.quote_id}")
            continue

        evidence_ids = list(label.evidence_refs)
        visible_from_cp = compute_visible_from_cp(
            evidence_positions=[evidence_positions.get(ref, 0) for ref in evidence_ids],
            fallback_cp=quote_positions.get(label.quote_id, (0, 0))[0],
        )

        scene_id = _ensure_scene(session, state, book_version_id)
        application.scene_id = scene_id
        speaker_id: str | None = None
        if label.kind is QuoteKind.SPEECH and label.assignment is not None:
            if label.assignment is Assignment.NEW and label.speaker_ref:
                declaration = next(
                    (
                        speaker
                        for speaker in parsed.new_speakers
                        if speaker.temp_ref == label.speaker_ref
                    ),
                    None,
                )
                slot = registry.register_temp_speaker(
                    temp_ref=label.speaker_ref,
                    first_quote_id=label.quote_id,
                    description=declaration.description if declaration else "",
                    canonical_name=(declaration.name if declaration else None)
                    or label.speaker_name or "",
                    evidence_refs=tuple(evidence_ids),
                )
                speaker_id = _ensure_group(
                    session, state=state, slot=slot, scene_id=scene_id
                )
                if speaker_id not in application.created_group_ids:
                    application.created_group_ids.append(speaker_id)
            elif label.assignment is Assignment.EXISTING:
                slot = registry.resolve(label.speaker_ref)
                if slot is not None:
                    authoritative = next(
                        (
                            character
                            for character in state.confirmed_characters
                            if character.character_id == slot.character_id
                        ),
                        None,
                    )
                    if authoritative is not None:
                        # speaker_ref 已指向用户确认人物时，姓名与说明只能来自确认名单。
                        # 模型即使返回另一个已确认姓名，也只记录冲突，不反向改写。
                        incoming = state._confirmed_by_name(label.speaker_name)
                        if label.speaker_name and (
                            incoming is None
                            or incoming.character_id != authoritative.character_id
                        ):
                            application.warnings.append(
                                "confirmed_identity_conflict:"
                                f"{label.speaker_ref}:{label.speaker_name}"
                                f"->{authoritative.canonical_name}"
                            )
                        slot.canonical_name = authoritative.canonical_name
                        if authoritative.description:
                            slot.description = authoritative.description
                    elif label.speaker_name:
                        incoming = state._confirmed_by_name(label.speaker_name)
                        if incoming is not None:
                            slot.character_id = incoming.character_id
                            slot.canonical_name = incoming.canonical_name
                            if incoming.description:
                                slot.description = incoming.description
                        else:
                            slot.canonical_name = label.speaker_name.strip()
                    if not slot.first_quote_id:
                        slot.first_quote_id = label.quote_id
                    state.remember_character(slot.canonical_name, slot.description)
                    _ensure_group(session, state=state, slot=slot, scene_id=scene_id)
                speaker_id, warning = _resolve_speaker(
                    registry, label=label, visible_from_cp=visible_from_cp
                )
                if warning:
                    application.warnings.append(warning)

        stored_label = label
        if (
            label.kind is QuoteKind.SPEECH
            and label.assignment in {Assignment.EXISTING, Assignment.NEW}
            and speaker_id is None
        ):
            # 防御性不变量：声称已识别人但解析不到场景内分组时，绝不能保存成
            # ACCEPTED + 空 speaker_id。降级为 UNKNOWN，交给有限复核或人工确认。
            application.warnings.append(f"speaker_resolution_failed:{label.quote_id}")
            stored_label = label.model_copy(
                update={
                    "assignment": Assignment.UNKNOWN,
                    "speaker_ref": None,
                    "speaker_name": None,
                    "basis": SpeakerBasis.INSUFFICIENT,
                }
            )
        decision_out = decide_acceptance(stored_label, cold_start=cold_start)

        annotation, replaced = _record_annotation(
            session,
            quote_id=label.quote_id,
            scene_id=scene_id,
            kind=stored_label.kind,
            assignment=stored_label.assignment,
            basis=stored_label.basis,
            speaker_id=speaker_id,
            status=decision_out.status,
            source=source,
            evidence_refs=evidence_ids,
            visible_from_cp=visible_from_cp,
            dependency_hash=effective_hash,
            run_id=run_id,
        )
        application.annotation_ids.append(annotation.id)
        _member_of_scene(
            session,
            quote_id=label.quote_id,
            scene_id=scene_id,
            revision=state.version,
        )
        if decision_out.needs_review and decision_out.review_reason is not None:
            review_id = _upsert_review_item(
                session,
                quote_id=label.quote_id,
                gap_id=None,
                reason=decision_out.review_reason,
                candidates={
                    "basis": stored_label.basis.value if stored_label.basis else None,
                    "assignment": (
                        stored_label.assignment.value if stored_label.assignment else None
                    ),
                    "reason": decision_out.reason,
                },
                annotation_version=annotation.version,
            )
            application.review_item_ids.append(review_id)
        if replaced and annotation.user_locked:
            # 理论上被上面的 locked 检查拦住；这里再兜一次，绝不覆盖用户结果
            application.warnings.append(f"locked_annotation_kept:{label.quote_id}")

        if stored_label.kind is QuoteKind.SPEECH:
            resolved_slot = state.find(speaker_id)
            state.remember_turn(quote_id=stored_label.quote_id, slot=resolved_slot)
            state.last_speaker_ref = speaker_id or state.last_speaker_ref
        state.last_quote_id = stored_label.quote_id

    for gap_decision in parsed.gap_decisions:
        if gap_decision.decision is GapDecision.UNCERTAIN:
            review_id = _upsert_review_item(
                session,
                quote_id=None,
                gap_id=gap_decision.gap_id,
                reason=ReviewReason.SCENE_BOUNDARY,
                candidates={"decision": gap_decision.decision.value},
            )
            application.review_item_ids.append(review_id)

    # 身份修订：明确证据且不触碰人工锁定时才自动应用
    for proposal in parsed.identity_proposals:
        revision_id, deferred = _apply_identity_proposal(
            session,
            state=state,
            proposal=proposal,
            locked_group_ids=set(locked_group_ids or ()),
            evidence_positions=evidence_positions,
            dependency_hash=effective_hash,
            cold_start=cold_start,
        )
        if revision_id:
            application.identity_revision_ids.append(revision_id)
        if deferred:
            first_ref = proposal.input_refs[0] if proposal.input_refs else None
            slot = registry.resolve(first_ref)
            review_id = _upsert_review_item(
                session,
                quote_id=slot.first_quote_id if slot else None,
                gap_id=None,
                reason=ReviewReason.OTHER,
                candidates={
                    "operation": proposal.operation.value,
                    "input_refs": list(proposal.input_refs),
                    "output_refs": list(proposal.output_refs),
                },
            )
            application.review_item_ids.append(review_id)

    application.stats = {
        "engine_version": ENGINE_VERSION,
        "state_version": SCENE_STATE_VERSION,
        "labels": len(accepted),
        "accepted": sum(
            1
            for _id in application.annotation_ids
        ),
        "locked_skipped": len(application.skipped_locked_quote_ids),
        "groups": len(state.participants),
    }
    # new1/new2 是单次模型响应内的临时引用；带到下一窗口会误指旧人物。
    state.clear_request_aliases()
    return application


def _apply_identity_proposal(
    session: Session,
    *,
    state: SceneState,
    proposal: IdentityProposal,
    locked_group_ids: set[str],
    evidence_positions: Mapping[str, int],
    dependency_hash: str,
    cold_start: bool,
) -> tuple[str | None, bool]:
    """返回 ``(revision_id, deferred_for_review)``。"""

    view = IdentityProposalView(
        operation=proposal.operation,
        input_refs=tuple(proposal.input_refs),
        output_refs=tuple(proposal.output_refs),
        evidence_refs=tuple(proposal.evidence_refs),
        # §4.4 的 identity_proposals 没有 basis 字段：带证据视为明确证据（DIRECT），
        # 没有证据则交给待确认队列，绝不自动合并。
        basis=SpeakerBasis.DIRECT if proposal.evidence_refs else None,
        visible_from_cp=compute_visible_from_cp(
            evidence_positions=[evidence_positions.get(ref, 0) for ref in proposal.evidence_refs],
            fallback_cp=state.start_cp,
        ),
    )
    touched = bool(
        {ref for ref in proposal.input_refs if ref in locked_group_ids}
        | {ref for ref in proposal.output_refs if ref in locked_group_ids}
    )
    decision = evaluate_identity_proposal(
        view, touches_locked=touched, cold_start=cold_start
    )
    snapshot = {
        "participants": [slot.as_dict() for slot in state.participants],
        "operation": proposal.operation.value,
        "reason": decision.reason,
    }
    if not decision.applied:
        return None, True

    scene_id = state.scene_id
    if scene_id is None:
        return None, True

    # 记录「哪一句原本属于哪个分组」，初读投影才能在证据出现之前还原旧分组
    absorbed_quote_map: dict[str, str] = {}
    if proposal.operation is IdentityOperation.MERGE and len(proposal.input_refs) >= 2:
        survivor = state.find(proposal.input_refs[0])
        for ref in proposal.input_refs[1:]:
            victim = state.find(ref)
            if survivor is None or victim is None or victim is survivor:
                continue
            survivor_key = survivor.group_id or survivor.temp_ref
            victim_key = victim.group_id
            if survivor.character_id is not None:
                victim.character_id = survivor.character_id
            if victim_key and survivor_key and victim_key != survivor_key:
                # 当前投影指向幸存分组；旧值已经写入 annotation_history
                for annotation in session.execute(
                    select(Annotation).where(Annotation.speaker_id == victim_key)
                ).scalars():
                    if annotation.user_locked:
                        continue
                    _write_history(session, annotation)
                    annotation.speaker_id = survivor.group_id
                    annotation.version += 1
                    absorbed_quote_map[annotation.quote_id] = victim_key
            state.participants.remove(victim)
    snapshot["revert"] = {
        "quotes": absorbed_quote_map,
        "groups": {
            output: input_ref
            for input_ref in proposal.input_refs
            for output in proposal.output_refs
        },
    }

    row = IdentityRevision(
        scene_id=scene_id,
        operation=proposal.operation,
        input_ids_json=json.dumps(
            [
                (slot.group_id if (slot := state.find(ref)) else None) or ref
                for ref in proposal.input_refs
            ],
            ensure_ascii=False,
        ),
        output_ids_json=json.dumps(list(proposal.output_refs), ensure_ascii=False),
        snapshot_json=json.dumps(snapshot, ensure_ascii=False),
        evidence_refs_json=json.dumps(list(proposal.evidence_refs), ensure_ascii=False),
        visible_from_cp=view.visible_from_cp,
        version=1,
    )
    session.add(row)
    session.flush()
    return row.id, False
