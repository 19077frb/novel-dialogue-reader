"""任务恢复状态的 API schema。

每个非完成状态都必须给用户**可理解的恢复动作**，
并且明确哪些动作会产生费用（`paid`）。
"""

from __future__ import annotations

from pydantic import Field

from .common import ApiModel
from .enums import JobState


class RecoveryActionOut(ApiModel):
    action: str = Field(
        description="pause/resume/run/reconcile_retry/reconcile_keep/open_settings/wait"
    )
    label: str
    detail: str
    paid: bool = Field(default=False, description="是否可能产生模型调用费用")
    endpoint: str | None = Field(default=None, description="前端可直接调用的端点模板")


class JobRecoveryOut(ApiModel):
    job_id: str
    state: JobState
    summary: str
    actions: list[RecoveryActionOut] = Field(default_factory=list)
    windows_total: int = Field(ge=0, default=0)
    windows_done: int = Field(ge=0, default=0)
    remaining_windows: int = Field(ge=0, default=0)
    unknown_runs: int = Field(ge=0, default=0)
    unknown_usage_runs: int = Field(ge=0, default=0)
    retry_in_seconds: int | None = Field(
        default=None, ge=0, description="限流退避的建议等待时间（0 表示可立即继续）"
    )
    requires_credential: bool = Field(
        default=False, description="是否因为缺少模型凭据而失败（需要先去补充密钥）"
    )
    last_error: str | None = None
    updated_at: str
