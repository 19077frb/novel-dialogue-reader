"""标注投影的 API schema。"""

from __future__ import annotations

from pydantic import Field

from .common import ApiModel
from .enums import (
    AnnotationStatus,
    Assignment,
    QuoteKind,
    ReadingMode,
    SceneStatus,
    SpeakerBasis,
)


class SpeakerLegendItemOut(ApiModel):
    group_id: str
    label: str = Field(description="已确认真实姓名优先，否则为场景内编号 S1/S2…")
    scene_id: str
    scene_ref: str | None = None
    color_index: int = Field(ge=0, description="按已确认人物身份统一的稳定色号（0..N-1）")
    first_quote_id: str | None = None
    description: str = ""
    quote_count: int = Field(ge=0, default=0)


class AnnotationItemOut(ApiModel):
    quote_id: str
    scene_id: str | None = None
    start_cp: int = Field(ge=0)
    end_cp: int = Field(ge=0)
    kind: QuoteKind
    assignment: Assignment | None = None
    basis: SpeakerBasis | None = None
    status: AnnotationStatus
    source: str
    speaker_group_id: str | None = None
    label: str | None = Field(
        default=None,
        description="已确认真实姓名优先，否则为人物称呼/说明；仅在可见时有值",
    )
    speaker_description: str = Field(
        default="", description="说话人的人物描述，用于正文悬停提示"
    )
    color_index: int | None = Field(default=None, ge=0)
    visible_from_cp: int | None = None
    stale: bool = False
    user_locked: bool = False
    withheld: bool = Field(
        default=False, description="初读 horizon 之下证据尚未出现：不显示颜色/编号"
    )


class AnnotationCountsOut(ApiModel):
    total: int = Field(ge=0)
    accepted: int = Field(ge=0)
    provisional: int = Field(ge=0)
    unknown: int = Field(ge=0)
    stale: int = Field(ge=0)
    withheld: int = Field(ge=0)
    unprocessed_quotes: int = Field(ge=0, description="候选里还没有标注的对白数")


class AnnotationsResponse(ApiModel):
    book_id: str
    book_version_id: str
    reading_mode: ReadingMode
    visible_horizon_cp: int | None = None
    start_cp: int = Field(ge=0)
    end_cp: int = Field(ge=0)
    items: list[AnnotationItemOut] = Field(default_factory=list)
    identity_reverts: int = Field(
        default=0,
        ge=0,
        description="初读 horizon 之下被还原（不提前合并/拆分）的身份修订条数",
    )
    legend: list[SpeakerLegendItemOut] = Field(default_factory=list)
    counts: AnnotationCountsOut
    scenes: list[dict[str, object]] = Field(default_factory=list)


class SceneSummaryOut(ApiModel):
    scene_id: str
    scene_ref: str | None = None
    status: SceneStatus
    start_cp: int = Field(ge=0)
    end_cp: int | None = None
    participants: list[SpeakerLegendItemOut] = Field(default_factory=list)
