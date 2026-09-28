"""场景内说话人分组的合并与拆分。

规则：

- 只改**本场景内**的分组；跨场景的分组引用一律 422（避免把两个场景的声音当成一个人）。
- 合并：吸收方的分组行保留（被吸收的分组自然变空，历史记录在 `identity_revisions`）。
- 拆分：为每个桶新建分组；原分组保留给未列出的引语。
- 两者都写 `identity_revisions` + 一条 `action=undo` 可撤销的 `corrections` 记录，
  历史只追加，不硬删除。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.errors import ApiError
from ..domain.corrections import SpeakerRevisionIn, SpeakerRevisionOut
from ..domain.enums import CorrectionAction, CorrectionTargetType, IdentityOperation
from ..storage.models import (
    Annotation,
    Correction,
    IdentityRevision,
    Quote,
    Scene,
    SpeakerGroup,
)
from ..storage.transactions import check_version
from .history import annotation_snapshot, write_annotation_history
from .invalidator import mark_stale
from .scenes import next_display_label, quote_order


@dataclass
class SpeakerRevisionOutcome:
    revision_id: str = ""
    correction_id: str = ""
    operation: IdentityOperation = IdentityOperation.MERGE
    scene_id: str = ""
    scene_version: int = 1
    group_ids: list[str] = field(default_factory=list)
    created_group_ids: list[str] = field(default_factory=list)
    empty_group_ids: list[str] = field(default_factory=list)
    affected_quote_ids: list[str] = field(default_factory=list)
    stale_quote_ids: list[str] = field(default_factory=list)


def _scene_groups(session: Session, scene_id: str) -> dict[str, SpeakerGroup]:
    return {
        row.id: row
        for row in session.execute(
            select(SpeakerGroup).where(SpeakerGroup.scene_id == scene_id)
        ).scalars()
    }


def _scene_annotations(session: Session, scene_id: str) -> list[Annotation]:
    return list(
        session.execute(
            select(Annotation).where(Annotation.scene_id == scene_id)
        ).scalars()
    )

def apply_speaker_revision(
    session: Session, *, scene: Scene, payload: SpeakerRevisionIn
) -> SpeakerRevisionOutcome:
    """人工 merge / split；一个事务内完成，可撤销，绝不调用模型。"""

    if payload.expected_scene_version is not None:
        check_version(scene, payload.expected_scene_version)
    groups = _scene_groups(session, scene.id)
    annotations = _scene_annotations(session, scene.id)
    by_quote = {row.quote_id: row for row in annotations}

    # 涉及的分组必须都属于本场景
    referenced: set[str] = set(payload.source_group_ids)
    if payload.operation is IdentityOperation.SPLIT:
        referenced.update(
            row.speaker_id for row in by_quote.values() if row.speaker_id is not None
        )
    for group_id in payload.source_group_ids:
        group = groups.get(group_id)
        if group is None:
            existing = session.get(SpeakerGroup, group_id)
            raise ApiError.validation(
                "说话人分组不属于该场景",
                reason="CROSS_SCENE_SPEAKER" if existing is not None else "SPEAKER_NOT_FOUND",
                group_id=group_id,
                scene_id=scene.id,
                group_scene_id=existing.scene_id if existing is not None else None,
            )

    outcome = SpeakerRevisionOutcome(
        operation=payload.operation, scene_id=scene.id, scene_version=scene.version
    )
    before_map: dict[str, str | None] = {}
    after_map: dict[str, str | None] = {}
    touched: list[Annotation] = []

    if payload.operation is IdentityOperation.MERGE:
        ordered_groups = quote_order(
            session,
            [
                group.first_quote_id
                for group_id in payload.source_group_ids
                if (group := groups.get(group_id)) and group.first_quote_id
            ],
        )
        survivor_id = payload.source_group_ids[0]
        if ordered_groups:
            for group_id in payload.source_group_ids:
                group = groups.get(group_id)
                if group is not None and group.first_quote_id == ordered_groups[0]:
                    survivor_id = group.id
                    break
        survivor = groups[survivor_id]
        absorbed = [gid for gid in payload.source_group_ids if gid != survivor_id]
        for annotation in annotations:
            if annotation.speaker_id in absorbed:
                before_map[annotation.quote_id] = annotation.speaker_id
                after_map[annotation.quote_id] = survivor.id
                touched.append(annotation)
        outcome.group_ids = [survivor_id]
        outcome.empty_group_ids = absorbed
        if touched:
            survivor.version = int(survivor.version) + 1
        snapshot = {
            "operation": IdentityOperation.MERGE.value,
            "input_group_ids": list(payload.source_group_ids),
            "survivor_group_id": survivor_id,
            "absorbed_group_ids": absorbed,
            # 人工合并是用户当下的决定：可见时点为空 → 始终生效（不参与初读还原）
            "revert": {"quotes": before_map, "groups": {}},
        }
    else:
        # 拆分：桶里的引语必须当前都属于同一个分组
        buckets: list[list[str]] = [list(bucket) for bucket in payload.buckets]
        bucket_groups: list[str] = []
        for bucket in buckets:
            for quote_id in bucket:
                annotation = by_quote.get(quote_id)
                if annotation is None:
                    raise ApiError.validation(
                        "bucket 里的对白不属于该场景", quote_id=quote_id, scene_id=scene.id
                    )
                if annotation.speaker_id is None:
                    raise ApiError.validation(
                        "bucket 里的对白还没有说话人分组", quote_id=quote_id
                    )
                bucket_groups.append(annotation.speaker_id)
        unique_groups = set(bucket_groups)
        if len(unique_groups) != 1:
            raise ApiError.validation(
                "拆分只能针对同一个说话人分组",
                reason="SPLIT_MIXED_GROUPS",
                group_ids=sorted(unique_groups),
            )
        source_group_id = unique_groups.pop()
        source_group = groups.get(source_group_id)
        assert source_group is not None
        created: list[str] = []
        for bucket in buckets:
            new_group = SpeakerGroup(
                scene_id=scene.id,
                first_quote_id=quote_order(session, bucket)[0] if bucket else None,
                display_label=next_display_label(session, scene.id),
                evidence_refs_json="[]",
            )
            session.add(new_group)
            session.flush()
            created.append(new_group.id)
            for quote_id in bucket:
                annotation = by_quote[quote_id]
                before_map[quote_id] = annotation.speaker_id
                after_map[quote_id] = new_group.id
                touched.append(annotation)
            outcome.group_ids.append(new_group.id)
        outcome.created_group_ids = created
        if created:
            source_group.version = int(source_group.version) + 1
        snapshot = {
            "operation": IdentityOperation.SPLIT.value,
            "source_group_id": source_group_id,
            "output_group_ids": created,
            "bucket_sizes": [len(bucket) for bucket in buckets],
            "revert": {
                "quotes": before_map,
                "groups": {group_id: source_group_id for group_id in created},
            },
        }

    revision = IdentityRevision(
        scene_id=scene.id,
        operation=payload.operation,
        input_ids_json=json.dumps(
            (
                payload.source_group_ids
                if payload.operation is IdentityOperation.MERGE
                else list(before_map.values())
            ),
            ensure_ascii=False,
        ),
        output_ids_json=json.dumps(outcome.group_ids, ensure_ascii=False),
        snapshot_json=json.dumps(snapshot, ensure_ascii=False),
        evidence_refs_json="[]",
        visible_from_cp=None,
        version=1,
    )
    session.add(revision)
    session.flush()
    outcome.revision_id = revision.id

    correction = Correction(
        target_type=CorrectionTargetType.SCENE,
        target_id=scene.id,
        action=(
            CorrectionAction.MERGE_SPEAKERS
            if payload.operation is IdentityOperation.MERGE
            else CorrectionAction.SPLIT_SPEAKERS
        ),
        before_json=json.dumps(
            {
                "scene_id": scene.id,
                "speaker_map": before_map,
                "scene_version": scene.version,
            },
            ensure_ascii=False,
        ),
        after_json="{}",
        expected_version=payload.expected_scene_version,
        applied_version=None,
    )
    session.add(correction)
    session.flush()
    outcome.correction_id = correction.id

    for annotation in touched:
        write_annotation_history(session, annotation, correction_id=correction.id)
        annotation.speaker_id = after_map[annotation.quote_id]
        annotation.stale = True
        annotation.version = int(annotation.version) + 1
    scene.version = int(scene.version) + 1
    session.flush()

    correction.after_json = json.dumps(
        {
            "scene_id": scene.id,
            "speaker_map": after_map,
            "scene_version": scene.version,
            "created_group_ids": outcome.created_group_ids,
            "empty_group_ids": outcome.empty_group_ids,
        },
        ensure_ascii=False,
    )
    correction.applied_version = scene.version
    outcome.scene_version = scene.version
    outcome.affected_quote_ids = list(after_map)

    touched_annotations = [
        row for row in _scene_annotations(session, scene.id) if row.quote_id in after_map
    ]
    impact = mark_stale(
        session, touched_annotations, correction_id=correction.id, exclude_quote_ids=set()
    )
    outcome.stale_quote_ids = impact.quote_ids
    return outcome


def speaker_revision_out(
    outcome: SpeakerRevisionOutcome, counts: dict[str, int]
) -> SpeakerRevisionOut:
    return SpeakerRevisionOut(
        revision_id=outcome.revision_id,
        correction_id=outcome.correction_id,
        operation=outcome.operation,
        scene_id=outcome.scene_id,
        scene_version=outcome.scene_version,
        group_ids=list(outcome.group_ids),
        created_group_ids=list(outcome.created_group_ids),
        empty_group_ids=list(outcome.empty_group_ids),
        affected_quote_ids=list(outcome.affected_quote_ids),
        stale_quote_ids=list(outcome.stale_quote_ids),
        updated_review_counts={**counts},
    )


def scene_annotations_after(session: Session, scene_id: str) -> list[Annotation]:
    """便于测试断言：场景内当前标注（含 stale 标记）。"""

    return _scene_annotations(session, scene_id)


def quote_annotation(session: Session, quote_id: str) -> Annotation | None:
    return session.execute(
        select(Annotation).where(Annotation.quote_id == quote_id)
    ).scalar_one_or_none()


def quote_position(session: Session, quote_id: str) -> int | None:
    quote = session.get(Quote, quote_id)
    return quote.start_cp if quote is not None else None


def annotation_snapshot_of(annotation: Annotation) -> dict[str, object]:
    return annotation_snapshot(annotation)
