"""人工更正、待确认队列与身份修订的 API schema。

设计要点：

- **人工操作不调用模型**：这些请求只写 `corrections` / `annotations` / `annotation_history` /
  `review_items` / `identity_revisions`，永远不创建 `inference_runs`。
- 普通人工确定结果为 `USER_CONFIRMED` 且 `user_locked=true`；模型结果不得覆盖它。
- `mark_unknown` 锁定的是「未知」本身：`status=UNKNOWN` 且 `speaker_id=null`，
  不会因此变成已识别人物。
- 撤销通过 `POST /api/corrections/{id}/undo` 完成，历史只追加、不硬删除。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from .common import ApiModel
from .enums import (
    AnnotationSource,
    AnnotationStatus,
    Assignment,
    CorrectionAction,
    CorrectionTargetType,
    GapDecision,
    IdentityOperation,
    QuoteKind,
    ReadingMode,
    ReviewQueueStatus,
    ReviewReason,
    ReviewTargetType,
    SpeakerBasis,
)
from .inference_options import InferenceOptions
from .jobs import BudgetIn

# 说话人级别的四种更正；其余动作各有专用端点（Gap / merge / split / undo）。
QuoteCorrectionAction = Literal[
    "assign_existing", "create_speaker", "set_kind", "mark_unknown"
]


class AnnotationStateOut(ApiModel):
    """当前有效标注（前端展示与版本校验都需要它）。"""

    quote_id: str
    scene_id: str | None = None
    kind: QuoteKind
    assignment: Assignment | None = None
    basis: SpeakerBasis | None = None
    speaker_group_id: str | None = None
    label: str | None = Field(default=None, description="场景内展示编号，如 S1")
    status: AnnotationStatus
    source: AnnotationSource
    stale: bool = False
    user_locked: bool = False
    visible_from_cp: int | None = None
    version: int = Field(ge=1)
    updated_at: str | None = None


class SceneGroupRefOut(ApiModel):
    """场景内可指定的已有分组（人工更正时按编号或 ID 指定）。"""

    group_id: str
    label: str
    canonical_name: str | None = None
    description: str = ""


class SceneRefOut(ApiModel):
    scene_id: str
    status: str
    start_cp: int = Field(ge=0)
    end_cp: int | None = None
    version: int = Field(ge=1)


class QuoteCorrectionIn(ApiModel):
    """`POST /api/quotes/{id}/corrections` 的请求体。"""

    action: QuoteCorrectionAction
    speaker_ref: str | None = Field(
        default=None,
        description="已有分组：可以是 group_id，也可以是场景内编号（S1、S2…）",
    )
    kind: QuoteKind | None = Field(default=None, description="set_kind 必填")
    description: str = Field(default="", max_length=512, description="create_speaker 的说明，可空")
    quote_ids: list[str] | None = Field(
        default=None,
        description="显式多目标范围；省略时只改当前 utterance。跨场景会被拒绝。",
    )
    expected_version: int | None = Field(
        default=None, ge=1, description="目标标注的期望版本；并发旧版本返回 409"
    )
    expected_scene_version: int | None = Field(default=None, ge=1)
    note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def _check_action_fields(self) -> QuoteCorrectionIn:
        if self.action == "assign_existing" and not self.speaker_ref:
            raise ValueError("assign_existing 必须提供 speaker_ref")
        if self.action == "set_kind" and self.kind is None:
            raise ValueError("set_kind 必须提供 kind")
        if self.quote_ids is not None and len(set(self.quote_ids)) != len(self.quote_ids):
            raise ValueError("quote_ids 不能重复")
        return self


class CorrectionOut(ApiModel):
    """人工更正的结果；前端不得自己推算权威计数。"""

    correction_id: str = Field(description="主目标的更正记录 ID")
    correction_ids: list[str] = Field(default_factory=list, description="本次写入的全部更正记录")
    action: CorrectionAction
    target_type: CorrectionTargetType
    target_id: str
    affected_quote_ids: list[str] = Field(default_factory=list)
    stale_quote_ids: list[str] = Field(default_factory=list)
    stale_window_ids: list[str] = Field(default_factory=list)
    created_group_ids: list[str] = Field(default_factory=list)
    resolved_review_item_ids: list[str] = Field(default_factory=list)
    annotation_versions: dict[str, int] = Field(default_factory=dict)
    scene_id: str | None = None
    scene_version: int | None = None
    updated_review_counts: dict[str, int] = Field(default_factory=dict)
    undone_by: str | None = None


class GapCorrectionIn(ApiModel):
    decision: GapDecision
    expected_scene_version: int | None = Field(default=None, ge=1)
    note: str = Field(default="", max_length=1000)


class GapCorrectionOut(ApiModel):
    correction_id: str
    gap_id: str
    decision: GapDecision
    previous_decision: GapDecision
    affected_quote_ids: list[str] = Field(default_factory=list)
    stale_quote_ids: list[str] = Field(default_factory=list)
    closed_scene_ids: list[str] = Field(default_factory=list)
    opened_scene_id: str | None = None
    resolved_review_item_ids: list[str] = Field(default_factory=list)
    created_review_item_ids: list[str] = Field(default_factory=list)
    updated_review_counts: dict[str, int] = Field(default_factory=dict)


class SpeakerRevisionIn(ApiModel):
    """`POST /api/scenes/{id}/speaker-revisions`：merge / split。"""

    operation: IdentityOperation
    visible_from_cp: int | None = Field(default=None, ge=0)
    source_group_ids: list[str] = Field(default_factory=list, description="merge：至少两个分组")
    buckets: list[list[str]] = Field(
        default_factory=list, description="split：至少两个桶，每桶是若干 quote_id"
    )
    expected_scene_version: int | None = Field(default=None, ge=1)
    note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def _check_arity(self) -> SpeakerRevisionIn:
        if self.operation is IdentityOperation.MERGE:
            if len(set(self.source_group_ids)) < 2:
                raise ValueError("merge 至少需要两个不同的 source_group_ids")
        else:
            if len(self.buckets) < 2:
                raise ValueError("split 至少需要两个 bucket")
            flat = [quote_id for bucket in self.buckets for quote_id in bucket]
            if len(flat) != len(set(flat)):
                raise ValueError("split 的 quote_id 不能重复")
            if any(len(bucket) == 0 for bucket in self.buckets):
                raise ValueError("split 的 bucket 不能为空")
        return self


class SpeakerRevisionOut(ApiModel):
    revision_id: str
    correction_id: str
    operation: IdentityOperation
    scene_id: str
    scene_version: int
    group_ids: list[str] = Field(default_factory=list)
    created_group_ids: list[str] = Field(default_factory=list)
    empty_group_ids: list[str] = Field(
        default_factory=list, description="合并/拆分后不再被引用的分组"
    )
    affected_quote_ids: list[str] = Field(default_factory=list)
    stale_quote_ids: list[str] = Field(default_factory=list)
    updated_review_counts: dict[str, int] = Field(default_factory=dict)


class RecheckIn(ApiModel):
    """`POST /api/quotes/{id}/recheck`：有上限的局部复核（**会创建真实付费任务**）。"""

    profile_id: str = Field(description="模型配置 ID；复核必须显式指定")
    dialogue_strategy: Literal["legacy", "complete", "complete-review"] = Field(
        default="legacy", description="对白策略；完整策略仍限定服务器选定的当前场景/章节范围",
    )
    inference_options: InferenceOptions | None = None
    budget: BudgetIn = Field(default_factory=BudgetIn)
    reading_mode: ReadingMode = ReadingMode.INITIAL
    visible_horizon_cp: int | None = Field(default=None, ge=0)
    idempotency_key: str = Field(min_length=1, max_length=128)
    run_now: bool = Field(default=True, description="是否立即在后台执行（测试可显式触发）")
    note: str = Field(default="", max_length=1000)


class ReviewFlagIn(ApiModel):
    """`POST /api/quotes/{id}/review-items`：用户主动标记问题（幂等）。"""

    reason: ReviewReason = ReviewReason.USER_FLAGGED
    note: str = Field(default="", max_length=1000)


class ReviewDeferIn(ApiModel):
    note: str = Field(default="", max_length=1000)


class ReviewItemOut(ApiModel):
    id: str
    target_type: ReviewTargetType
    quote_id: str | None = None
    gap_id: str | None = None
    target_text: str = Field(default="", description="待确认目标对应的原文片段。")
    reason: ReviewReason
    queue_status: ReviewQueueStatus
    candidates: dict[str, Any] = Field(default_factory=dict)
    annotation_version: int | None = None
    resolved_by_correction_id: str | None = None
    version: int = Field(ge=1)
    created_at: str
    updated_at: str


class ReviewItemCountsOut(ApiModel):
    total: int = Field(ge=0)
    by_status: dict[str, int] = Field(default_factory=dict)
    by_reason: dict[str, int] = Field(default_factory=dict)
    targets_total: int = Field(default=0, ge=0)
    targets_by_status: dict[str, int] = Field(default_factory=dict)


class ReviewItemDetailOut(ApiModel):
    item: ReviewItemOut
    annotation: AnnotationStateOut | None = None
    scene: SceneRefOut | None = None
    context_before: str = ""
    context_after: str = ""
    allowed_actions: list[str] = Field(default_factory=list)


class ReviewQueueResponse(ApiModel):
    items: list[ReviewItemOut] = Field(default_factory=list)
    next_cursor: str | None = None
    counts: ReviewItemCountsOut


class StaleReviewCleanupOut(ApiModel):
    resolved_records: int = Field(default=0, ge=0)
    restored_quotes: int = Field(default=0, ge=0)
    preserved_records: int = Field(default=0, ge=0)


class UndoOut(ApiModel):
    correction_id: str = Field(description="被撤销的更正记录 ID")
    undo_correction_id: str
    target_type: CorrectionTargetType
    target_id: str
    restored: dict[str, Any] = Field(default_factory=dict)
    affected_quote_ids: list[str] = Field(default_factory=list)
    stale_quote_ids: list[str] = Field(default_factory=list)
    updated_review_counts: dict[str, int] = Field(default_factory=dict)
