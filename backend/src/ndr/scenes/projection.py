"""标注投影：把当前标注/分组整理成前端可直接渲染的有效投影。

规则：

- 已确认真实姓名的分组按人物身份跨场景共享颜色；未确认姓名的场景分组保持隔离。
- 初读（`initial`）模式下，`visible_from_cp` 晚于 `visible_horizon_cp` 的标注
  **不下发颜色与编号**（`withheld=true`），避免用后文证据提前同色；重读（`reread`）不限制。
- 未知（UNKNOWN）不下发分组，也不分配颜色；没有标注的候选计入 `unprocessed_quotes`。
- 只读投影：本模块不写数据库、不调用模型。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..domain.annotations import (
    AnnotationCountsOut,
    AnnotationItemOut,
    AnnotationsResponse,
    SpeakerLegendItemOut,
)
from ..domain.enums import AnnotationStatus, ReadingMode
from ..storage.models import (
    Annotation,
    BookCharacter,
    IdentityRevision,
    Quote,
    Scene,
    SpeakerGroup,
)


def horizon_identity_reverts(
    session: Session,
    *,
    book_version_id: str,
    horizon: int | None,
) -> tuple[dict[str, str], dict[str, str], int]:
    """初读 horizon 之前的身份修订要还原。

    返回 ``(quote_id→旧分组, 新分组→旧分组, 还原条数)``。只读：不写数据库、不调用模型。
    合并/拆分的快照里记录了 `revert`（哪一句原本属于哪个分组），按**最新优先**应用，
    这样后文才出现的“两个声音其实是一个人”不会在初读时提前同色。
    """

    if horizon is None:
        return {}, {}, 0
    rows = list(
        session.execute(
            select(IdentityRevision)
            .join(Scene, IdentityRevision.scene_id == Scene.id)
            .where(
                Scene.book_version_id == book_version_id,
                IdentityRevision.visible_from_cp.is_not(None),
                IdentityRevision.visible_from_cp > horizon,
            )
            .order_by(IdentityRevision.visible_from_cp.desc())
        ).scalars()
    )
    quotes: dict[str, str] = {}
    groups: dict[str, str] = {}
    applied = 0
    for row in rows:
        try:
            snapshot = json.loads(row.snapshot_json or "{}")
        except json.JSONDecodeError:
            continue
        revert = snapshot.get("revert") if isinstance(snapshot, dict) else None
        if not isinstance(revert, dict):
            continue
        touched = False
        for quote_id, old_group in (revert.get("quotes") or {}).items():
            if old_group:
                quotes.setdefault(str(quote_id), str(old_group))
                touched = True
        for new_group, old_group in (revert.get("groups") or {}).items():
            if old_group and str(new_group) != str(old_group):
                groups.setdefault(str(new_group), str(old_group))
                touched = True
        if touched:
            applied += 1
    return quotes, groups, applied


@dataclass
class ProjectionInput:
    book_id: str
    book_version_id: str
    reading_mode: ReadingMode = ReadingMode.INITIAL
    visible_horizon_cp: int | None = None
    start_cp: int = 0
    end_cp: int = 0
    items: list[AnnotationItemOut] = field(default_factory=list)
    legend: list[SpeakerLegendItemOut] = field(default_factory=list)
    scenes: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def build_projection(
    session: Session,
    *,
    book_id: str,
    book_version_id: str,
    start_cp: int,
    end_cp: int,
    reading_mode: ReadingMode = ReadingMode.INITIAL,
    visible_horizon_cp: int | None = None,
) -> AnnotationsResponse:
    """按范围返回有效投影（颜色/编号/图例/统计）。"""

    horizon = visible_horizon_cp if reading_mode is ReadingMode.INITIAL else None
    revert_quotes, revert_groups, reverted_revisions = horizon_identity_reverts(
        session, book_version_id=book_version_id, horizon=horizon
    )

    quote_rows = {
        row.id: row
        for row in session.execute(
            select(Quote).where(
                Quote.book_version_id == book_version_id,
                Quote.nesting_depth == 0,
                Quote.start_cp < end_cp,
                Quote.end_cp > start_cp,
            )
        ).scalars()
    }
    annotations = list(
        session.execute(
            select(Annotation)
            .join(Quote, Annotation.quote_id == Quote.id)
            .where(
                Quote.book_version_id == book_version_id,
                Quote.nesting_depth == 0,
                Quote.start_cp < end_cp,
                Quote.end_cp > start_cp,
            )
        ).scalars()
    )
    scenes = {
        row.id: row
        for row in session.execute(
            select(Scene).where(
                Scene.book_version_id == book_version_id,
                Scene.start_cp < end_cp,
                or_(Scene.end_cp.is_(None), Scene.end_cp == 0, Scene.end_cp > start_cp),
            )
        ).scalars()
    }
    groups = list(
        session.execute(
            select(
                SpeakerGroup.id,
                SpeakerGroup.scene_id,
                SpeakerGroup.first_quote_id,
                SpeakerGroup.character_id,
                SpeakerGroup.canonical_name,
                SpeakerGroup.description,
                Quote.start_cp.label("first_start_cp"),
                BookCharacter.preferred_color_index,
            )
            .join(Scene, SpeakerGroup.scene_id == Scene.id)
            .outerjoin(Quote, SpeakerGroup.first_quote_id == Quote.id)
            .outerjoin(BookCharacter, SpeakerGroup.character_id == BookCharacter.id)
            .where(Scene.book_version_id == book_version_id)
        )
    )

    # 已确认真实姓名是章节/书籍级身份键：跨场景的同名人物共享颜色。
    # 没有姓名的分组仍以 group_id 隔离，绝不因为都叫 S1 就误合并。
    groups.sort(
        key=lambda group: (
            group.first_start_cp if group.first_start_cp is not None else 2**63 - 1,
            group.id,
        )
    )
    reserved_colors = {
        row.preferred_color_index
        for row in groups
        if row.preferred_color_index is not None and row.preferred_color_index >= 0
    }
    identity_by_group: dict[str, str] = {}
    color_by_group: dict[str, int] = {}
    label_by_group: dict[str, str] = {}
    description_by_group: dict[str, str] = {}
    color_by_identity: dict[str, int] = {}
    representative_by_identity: dict[str, Any] = {}
    ordered_identities: list[str] = []
    used_colors: set[int] = set()
    next_free_color = 0
    for group in groups:
        name = (group.canonical_name or "").strip()
        if group.character_id:
            identity = f"character:{group.character_id}"
        elif name:
            identity = f"name:{name.casefold()}"
        else:
            identity = f"group:{group.id}"
        if identity not in color_by_identity:
            preferred = group.preferred_color_index
            if preferred is not None and preferred >= 0 and preferred not in used_colors:
                color = preferred
            else:
                while next_free_color in used_colors or next_free_color in reserved_colors:
                    next_free_color += 1
                color = next_free_color
            color_by_identity[identity] = color
            used_colors.add(color)
            representative_by_identity[identity] = group
            ordered_identities.append(identity)
        identity_by_group[group.id] = identity
        color_by_group[group.id] = color_by_identity[identity]
        description = (group.description or "").strip()
        label_by_group[group.id] = name or description or "未确认说话人"
        description_by_group[group.id] = description

    counts = {
        "total": 0,
        "accepted": 0,
        "provisional": 0,
        "unknown": 0,
        "stale": 0,
        "withheld": 0,
        "unprocessed_quotes": max(0, len(quote_rows) - len(annotations)),
    }
    items: list[AnnotationItemOut] = []
    quote_count_by_identity: dict[str, int] = {}
    description_by_identity: dict[str, str] = {}
    description_position_by_identity: dict[str, int] = {}
    for annotation in annotations:
        quote = quote_rows.get(annotation.quote_id)
        if quote is None:
            continue
        counts["total"] += 1
        if annotation.status is AnnotationStatus.ACCEPTED:
            counts["accepted"] += 1
        elif annotation.status is AnnotationStatus.PROVISIONAL:
            counts["provisional"] += 1
        elif annotation.status is AnnotationStatus.UNKNOWN:
            counts["unknown"] += 1
        if annotation.stale:
            counts["stale"] += 1

        group_id = (
            annotation.speaker_id if annotation.status is not AnnotationStatus.UNKNOWN else None
        )
        if group_id:
            # 初读：后文才揭示的身份修订在这里被还原（只影响展示，不改数据库）
            reverted = revert_quotes.get(annotation.quote_id) or revert_groups.get(group_id)
            if reverted:
                group_id = reverted
        withheld = bool(
            horizon is not None
            and annotation.visible_from_cp is not None
            and annotation.visible_from_cp > horizon
        )
        if withheld:
            counts["withheld"] += 1
        label = None if (withheld or group_id is None) else label_by_group.get(group_id)
        color_index = None if (withheld or group_id is None) else color_by_group.get(group_id)
        if group_id and not withheld:
            identity = identity_by_group.get(group_id)
            if identity:
                quote_count_by_identity[identity] = quote_count_by_identity.get(identity, 0) + 1
                description = description_by_group.get(group_id, "")
                # Only visible utterances may contribute identity descriptions.
                # A name-only first group must not hide a later informative description.
                if (
                    description
                    and description != label
                    and (
                        identity not in description_position_by_identity
                        or quote.start_cp < description_position_by_identity[identity]
                    )
                ):
                    description_by_identity[identity] = description
                    description_position_by_identity[identity] = quote.start_cp

        items.append(
            AnnotationItemOut(
                quote_id=annotation.quote_id,
                scene_id=annotation.scene_id,
                start_cp=quote.start_cp,
                end_cp=quote.end_cp,
                kind=annotation.kind,
                assignment=annotation.assignment,
                basis=annotation.basis,
                status=annotation.status,
                source=annotation.source.value,
                speaker_group_id=group_id,
                label=label,
                speaker_description=(
                    "" if (withheld or group_id is None) else description_by_group.get(group_id, "")
                ),
                color_index=color_index,
                visible_from_cp=annotation.visible_from_cp,
                stale=annotation.stale,
                user_locked=annotation.user_locked,
                withheld=withheld,
            )
        )

    for item in items:
        if item.speaker_group_id and not item.withheld:
            identity = identity_by_group.get(item.speaker_group_id, "")
            item.speaker_description = description_by_identity.get(identity, "")

    legend = []
    for identity in ordered_identities:
        quote_count = quote_count_by_identity.get(identity, 0)
        if quote_count <= 0:
            continue
        group = representative_by_identity[identity]
        legend.append(
            SpeakerLegendItemOut(
                group_id=group.id,
                label=(group.canonical_name or "").strip()
                or (group.description or "").strip()
                or "未确认说话人",
                scene_id=group.scene_id,
                color_index=color_by_identity[identity],
                first_quote_id=group.first_quote_id,
                description=description_by_identity.get(identity, ""),
                quote_count=quote_count,
            )
        )
    scene_summaries = [
        {
            "scene_id": scene.id,
            "status": scene.status.value,
            "start_cp": scene.start_cp,
            "end_cp": scene.end_cp,
        }
        for scene in sorted(scenes.values(), key=lambda row: row.start_cp)
        if scene.start_cp < end_cp and (scene.end_cp or end_cp) > start_cp
    ]

    return AnnotationsResponse(
        book_id=book_id,
        book_version_id=book_version_id,
        reading_mode=reading_mode,
        visible_horizon_cp=horizon,
        identity_reverts=reverted_revisions,
        start_cp=start_cp,
        end_cp=end_cp,
        items=items,
        legend=legend,
        counts=AnnotationCountsOut(**counts),
        scenes=scene_summaries,
    )
