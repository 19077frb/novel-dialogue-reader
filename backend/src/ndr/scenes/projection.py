"""标注投影（T11）：把当前标注/分组整理成前端可直接渲染的有效投影。

规则（DEVELOPMENT.md 6.3 / 6.5）：

- 颜色与编号都来自**场景内稳定分组**；同一场景内同一分组永远同色同编号。
- 初读（`initial`）模式下，`visible_from_cp` 晚于 `visible_horizon_cp` 的标注
  **不下发颜色与编号**（`withheld=true`），避免用后文证据提前同色；重读（`reread`）不限制。
- 未知（UNKNOWN）不下发分组，也不分配颜色；没有标注的候选计入 `unprocessed_quotes`。
- 只读投影：本模块不写数据库、不调用模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.annotations import (
    AnnotationCountsOut,
    AnnotationItemOut,
    AnnotationsResponse,
    SpeakerLegendItemOut,
)
from ..domain.enums import AnnotationStatus, ReadingMode
from ..storage.models import Annotation, Quote, Scene, SpeakerGroup


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

    quote_rows = {
        row.id: row
        for row in session.execute(
            select(Quote).where(
                Quote.book_version_id == book_version_id,
                Quote.start_cp < end_cp,
                Quote.end_cp > start_cp,
            )
        ).scalars()
    }
    annotations = list(
        session.execute(
            select(Annotation).where(Annotation.quote_id.in_(quote_rows.keys() or [""]))
        ).scalars()
    )
    scenes = {
        row.id: row
        for row in session.execute(
            select(Scene).where(Scene.book_version_id == book_version_id)
        ).scalars()
    }
    groups = list(
        session.execute(
            select(SpeakerGroup)
            .where(SpeakerGroup.scene_id.in_(scenes.keys() or [""]))
            .order_by(SpeakerGroup.first_quote_id)
        ).scalars()
    )

    # 场景内稳定色号：按分组的首次发言位置排序
    color_by_group: dict[str, int] = {}
    label_by_group: dict[str, str] = {}
    scene_of_group: dict[str, str] = {}
    for group in groups:
        scene_groups = [item for item in groups if item.scene_id == group.scene_id]
        index = scene_groups.index(group)
        color_by_group[group.id] = index
        label_by_group[group.id] = group.display_label
        scene_of_group[group.id] = group.scene_id

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
    quote_count_by_group: dict[str, int] = {}
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
            annotation.speaker_id
            if annotation.status is not AnnotationStatus.UNKNOWN
            else None
        )
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
            quote_count_by_group[group_id] = quote_count_by_group.get(group_id, 0) + 1

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
                color_index=color_index,
                visible_from_cp=annotation.visible_from_cp,
                stale=annotation.stale,
                user_locked=annotation.user_locked,
                withheld=withheld,
            )
        )

    legend = [
        SpeakerLegendItemOut(
            group_id=group.id,
            label=group.display_label,
            scene_id=group.scene_id,
            color_index=color_by_group[group.id],
            first_quote_id=group.first_quote_id,
            quote_count=quote_count_by_group.get(group.id, 0),
        )
        for group in groups
        if quote_count_by_group.get(group.id, 0) > 0  # 只列出本范围内真的出现过的分组
    ]
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
        start_cp=start_cp,
        end_cp=end_cp,
        items=items,
        legend=legend,
        counts=AnnotationCountsOut(**counts),
        scenes=scene_summaries,
    )
