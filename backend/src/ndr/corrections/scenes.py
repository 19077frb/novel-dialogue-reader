"""场景归属调整。

Gap 更正会改变场景边界：BREAK 之后的引语要移入新场景，CONTINUE/UPDATE 则可能把
被模型错误切开的两段合成一个场景。这里只做**归属与编号**的结构调整：

- 移动时按新场景内的首次发言顺序重建分组（编号只在场景内有意义）。
- 旧分组行保留（可能变成空分组），历史与 `scene_memberships` 都不删除。
- 撤销时用记录下来的原 speaker_id 精确还原，不重新编号。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.enums import SceneStatus
from ..storage.models import Annotation, Quote, Scene, SceneMembership, SpeakerGroup


@dataclass
class SceneMoveResult:
    affected_quote_ids: list[str] = field(default_factory=list)
    created_group_ids: list[str] = field(default_factory=list)
    emptied_group_ids: list[str] = field(default_factory=list)
    previous_speaker_map: dict[str, str | None] = field(default_factory=dict)
    new_speaker_map: dict[str, str | None] = field(default_factory=dict)


def ensure_scene_for_quote(session: Session, *, book_version_id: str, quote: Quote) -> Scene:
    """找到包含该位置的场景；没有就新建一个（用户可以先更正未处理的对白）。"""

    rows = session.execute(
        select(Scene)
        .where(Scene.book_version_id == book_version_id, Scene.start_cp <= quote.start_cp)
        .order_by(Scene.start_cp.desc())
    ).scalars()
    for scene in rows:
        if scene.end_cp is None or scene.end_cp > quote.start_cp:
            return scene
    created = Scene(
        book_version_id=book_version_id,
        start_cp=quote.start_cp,
        end_cp=None,
        status=SceneStatus.OPEN,
    )
    session.add(created)
    session.flush()
    return created


def next_display_label(session: Session, scene_id: str) -> str:
    used = {
        row.display_label
        for row in session.execute(
            select(SpeakerGroup).where(SpeakerGroup.scene_id == scene_id)
        ).scalars()
    }
    index = 1
    while f"S{index}" in used:
        index += 1
    return f"S{index}"


def quote_order(session: Session, quote_ids: list[str]) -> list[str]:
    """按原文位置排序的 quote_id（场景编号依赖首次发言顺序）。"""

    rows = list(session.execute(select(Quote).where(Quote.id.in_(quote_ids or [""]))).scalars())
    return [row.id for row in sorted(rows, key=lambda item: (item.start_cp, item.id))]


def move_annotations_to_scene(
    session: Session,
    *,
    scene: Scene,
    quote_ids: list[str],
    speaker_map: Mapping[str, str | None] | None = None,
    stale: bool = True,
) -> SceneMoveResult:
    """把 quote_ids 的标注移入 `scene`。

    - 未给 `speaker_map`（正向操作）：按首次发言顺序在目标场景内重建分组。
    - 给了 `speaker_map`（撤销）：按 quote_id 精确还原 speaker_id，不重新编号。
    """

    result = SceneMoveResult()
    ordered = quote_order(session, quote_ids)
    annotations = {
        row.quote_id: row
        for row in session.execute(
            select(Annotation).where(Annotation.quote_id.in_(ordered or [""]))
        ).scalars()
    }

    # 正向：旧分组 → 目标场景内的新分组（已在目标场景的同名分组直接沿用）
    group_mapping: dict[str, str] = {}
    if speaker_map is None:
        for quote_id in ordered:
            annotation = annotations.get(quote_id)
            if annotation is None or annotation.speaker_id is None:
                continue
            previous = session.get(SpeakerGroup, annotation.speaker_id)
            if previous is not None and previous.scene_id == scene.id:
                group_mapping[annotation.speaker_id] = annotation.speaker_id
                continue
            created = SpeakerGroup(
                scene_id=scene.id,
                first_quote_id=quote_id,
                display_label=next_display_label(session, scene.id),
                evidence_refs_json=previous.evidence_refs_json if previous else "[]",
            )
            session.add(created)
            session.flush()
            group_mapping[annotation.speaker_id] = created.id
            result.created_group_ids.append(created.id)

    def resolve(quote_id: str, current: str | None) -> str | None:
        if speaker_map is not None:
            return speaker_map.get(quote_id, current)
        if current is None:
            return None
        return group_mapping.get(current, current)

    for quote_id in ordered:
        annotation = annotations.get(quote_id)
        if annotation is None:
            continue
        old_scene = annotation.scene_id
        old_speaker = annotation.speaker_id
        result.previous_speaker_map[quote_id] = old_speaker
        new_speaker = resolve(quote_id, old_speaker)
        annotation.scene_id = scene.id
        annotation.speaker_id = new_speaker
        if stale:
            annotation.stale = True
        annotation.version = int(annotation.version) + 1
        result.new_speaker_map[quote_id] = new_speaker
        result.affected_quote_ids.append(quote_id)
        if old_scene and old_scene != scene.id:
            _close_membership(session, quote_id=quote_id, scene_id=old_scene)
        ensure_membership(session, quote_id=quote_id, scene_id=scene.id, revision=scene.version)

    # 目标场景内不再被任何标注引用的分组（历史保留，只是不再出现在图例里）
    remaining = {
        row.speaker_id
        for row in session.execute(
            select(Annotation).where(Annotation.scene_id == scene.id)
        ).scalars()
        if row.speaker_id
    }
    candidates: set[str] = set(group_mapping.values())
    if speaker_map is not None:
        candidates.update(value for value in speaker_map.values() if value)
    for group_id in sorted(candidates):
        if group_id not in remaining:
            result.emptied_group_ids.append(group_id)
    session.flush()
    return result


def _close_membership(session: Session, *, quote_id: str, scene_id: str) -> None:
    row = session.execute(
        select(SceneMembership).where(
            SceneMembership.quote_id == quote_id,
            SceneMembership.scene_id == scene_id,
            SceneMembership.valid_to_revision.is_(None),
        )
    ).scalar_one_or_none()
    if row is not None:
        scene = session.get(Scene, scene_id)
        row.valid_to_revision = int(scene.version) if scene is not None else 1


def ensure_membership(session: Session, *, quote_id: str, scene_id: str, revision: int) -> None:
    row = session.execute(
        select(SceneMembership).where(
            SceneMembership.quote_id == quote_id,
            SceneMembership.scene_id == scene_id,
            SceneMembership.valid_to_revision.is_(None),
        )
    ).scalar_one_or_none()
    if row is not None:
        return
    session.add(
        SceneMembership(quote_id=quote_id, scene_id=scene_id, valid_from_revision=revision)
    )


def label_map_for_scene(session: Session, scene_id: str) -> dict[str, str]:
    return {
        row.id: row.display_label
        for row in session.execute(
            select(SpeakerGroup).where(SpeakerGroup.scene_id == scene_id)
        ).scalars()
    }
