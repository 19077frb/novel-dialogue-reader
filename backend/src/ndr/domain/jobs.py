"""任务、估算与用量的 API schema。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .common import ApiModel
from .enums import JobKind, JobPurpose, JobState, ReadingMode
from .inference_options import InferenceOptions


class BudgetIn(ApiModel):
    max_input_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    max_rechecks: int = Field(default=0, ge=0, deprecated=True,
        description="旧版每窗口待定对白复核条数，仅用于兼容历史任务")
    max_recheck_rounds: int | None = Field(default=None, ge=0,
        description="每个窗口最多复核轮次；每轮覆盖全部对白，0关闭，优先于旧条数配置")
    max_format_retries: int = Field(
        default=1, ge=0, le=5,
        description="对白输出校验失败后的纠错重试次数，不含首次调用；0 关闭，最多 5 次",
    )


class JobCreate(ApiModel):
    book_id: str
    kind: JobKind = JobKind.INFERENCE
    mode: JobPurpose = Field(default=JobPurpose.PROCESS, description="preview / process")
    book_version_id: str | None = None
    range: dict[str, Any] = Field(
        default_factory=dict, description="{start_cp, end_cp, chapter_id?}"
    )
    selected_window_ids: list[str] | None = Field(
        default=None,
        min_length=1,
        description="只处理预估阶段选中的窗口；null 表示全部窗口",
    )
    profile_id: str | None = None
    inference_options: InferenceOptions | None = None
    force_reprocess: bool = Field(
        default=False,
        description="强制重新调用模型，跳过结果缓存；仍保留人工锁定标注",
    )
    reading_mode: ReadingMode = ReadingMode.INITIAL
    visible_horizon_cp: int | None = Field(default=None, ge=0)
    budget: BudgetIn = Field(default_factory=BudgetIn)
    idempotency_key: str = Field(min_length=1, max_length=128)
    run_now: bool = Field(default=True, description="是否立即在后台执行（测试/E2E 可显式触发）")


class JobWindowOut(ApiModel):
    window_id: str
    state: JobState
    target_count: int = Field(ge=0)
    dependency_hash: str | None = None
    attempts: int = Field(default=0, ge=0)


class JobDetailOut(ApiModel):
    range: dict[str, Any] = Field(default_factory=dict)
    id: str
    kind: JobKind
    purpose: JobPurpose | None = None
    state: JobState
    book_id: str | None = None
    book_version_id: str | None = None
    progress: dict[str, Any] | None = None
    checkpoint: dict[str, Any] | None = None
    last_error: str | None = None
    windows: list[JobWindowOut] = Field(default_factory=list)
    remaining_windows: int = Field(default=0, ge=0)
    windows_total: int = Field(default=0, ge=0)
    calls: int = Field(default=0, ge=0)
    cached_windows: int = Field(default=0, ge=0)
    unknown_usage_runs: int = Field(default=0, ge=0)
    usage: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class TaskQueueItemOut(ApiModel):
    id: str
    kind: JobKind
    state: JobState
    book_id: str | None = None
    book_title: str = ""
    chapter_id: str | None = None
    chapter_title: str = ""
    selected_window_ids: list[str] | None = None
    start_cp: int | None = None
    end_cp: int | None = None
    progress: dict[str, Any] = Field(default_factory=dict)
    windows_total: int = 0
    windows_done: int = 0
    last_error: str | None = None
    created_at: str


class EstimateIn(ApiModel):
    book_version_id: str | None = None
    range: dict[str, Any] = Field(default_factory=dict)
    reading_mode: ReadingMode = ReadingMode.INITIAL
    visible_horizon_cp: int | None = Field(default=None, ge=0)
    budget: BudgetIn = Field(default_factory=BudgetIn)


class EstimateOut(ApiModel):
    book_id: str
    book_version_id: str
    window_count: int = Field(ge=0)
    target_count: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimator: dict[str, Any] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    windows: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Local window plans include processing_status (completed/failed/unprocessed), "
        "processed_target_count and last_error; completion is derived from saved annotations.",
    )


class UsageOut(ApiModel):
    book_id: str
    runs: int = Field(ge=0)
    unknown_usage_runs: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    by_state: dict[str, int] = Field(default_factory=dict)
    by_model: dict[str, int] = Field(default_factory=dict)


class ReconcileIn(ApiModel):
    action: Literal["retry", "keep_unknown"] = "keep_unknown"


class JobRunOut(ApiModel):
    job_id: str
    state: JobState
    windows_total: int = Field(default=0, ge=0)
    windows_done: int = Field(default=0, ge=0)
    cached_windows: int = Field(default=0, ge=0)
    calls: int = Field(default=0, ge=0)
    unknown_runs: int = Field(default=0, ge=0)
    budget_exhausted: bool = False
    errors: list[str] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
