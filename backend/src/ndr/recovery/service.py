"""任务恢复：把「非完成状态」翻译成用户能执行的动作，并在进程重启时修复可见状态。

三条硬规则：

1. **任务失败不让原文不可读**：本模块只动任务/尝试/窗口状态，从不触碰原文与标注。
2. **未知付费结果不自动重发**：超时/中断的尝试标为 `UNKNOWN_OUTCOME`，只有用户显式选择
   （`reconcile` 的 `retry`）才会重新排队；`keep_unknown` 也会保留记录。
3. **每种非完成状态都有可理解的恢复动作**：`job_recovery()` 给出动作、说明与是否付费。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..domain.enums import CredentialMode, InferenceRunState, JobKind, JobState
from ..domain.recovery import JobRecoveryOut, RecoveryActionOut
from ..jobs.scheduler import reconcile_stale_runs
from ..jobs.service import job_windows
from ..storage.models import InferenceRun, Job

RECOVERY_VERSION = "recovery-1"


@dataclass
class StartupRecovery:
    stale_runs: list[str] = field(default_factory=list)
    paused_jobs: list[str] = field(default_factory=list)
    interrupted_jobs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, list[str]]:
        return {
            "stale_runs": list(self.stale_runs),
            "paused_jobs": list(self.paused_jobs),
            "interrupted_jobs": list(self.interrupted_jobs),
        }


def profile_snapshot_of(job: Job) -> dict[str, object]:
    if not job.profile_snapshot_json:
        return {}
    try:
        value = json.loads(job.profile_snapshot_json)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def progress_of(job: Job) -> dict[str, object]:
    if not job.progress_json:
        return {}
    try:
        value = json.loads(job.progress_json)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}

def recovery_actions(
    state: JobState,
    *,
    remaining_windows: int,
    unknown_runs: int,
    requires_credential: bool,
) -> list[RecoveryActionOut]:
    """按任务状态给出可执行动作；`paid=True` 的动作可能产生模型费用。"""

    resume = RecoveryActionOut(
        action="resume",
        label="继续处理剩余窗口",
        detail=(
            "从检查点继续：已完成窗口直接复用，"
            f"剩余 {remaining_windows} 个窗口会重新调用模型（可能计费）。"
        ),
        paid=remaining_windows > 0,
        endpoint="POST /api/jobs/{id}/resume",
    )
    pause = RecoveryActionOut(
        action="pause",
        label="暂停",
        detail="当前窗口结束后暂停；不承诺已经发出的远程请求已停止计费。",
        paid=False,
        endpoint="POST /api/jobs/{id}/pause",
    )
    run_now = RecoveryActionOut(
        action="run",
        label="立即执行",
        detail="手动触发本地调度器执行未完成窗口（可能计费）。",
        paid=True,
        endpoint="POST /api/jobs/{id}/run",
    )
    open_settings = RecoveryActionOut(
        action="open_settings",
        label="去补充模型凭据",
        detail="该配置需要密钥但当前没有：请在「模型配置」里补充后再继续。",
        paid=False,
        endpoint="/settings/models",
    )

    if state is JobState.COMPLETED:
        return []
    if state is JobState.QUEUED:
        return [pause, run_now]
    if state is JobState.RUNNING:
        return [pause]
    if state is JobState.PAUSING:
        return [
            RecoveryActionOut(
                action="wait",
                label="等待当前窗口结束",
                detail="暂停已请求；当前窗口返回后才会进入已暂停。",
                paid=False,
            )
        ]
    if state is JobState.PAUSED:
        return [resume]
    if state is JobState.PARTIAL:
        return [resume]
    if state is JobState.BUDGET_EXHAUSTED:
        return [
            RecoveryActionOut(
                action="new_job",
                label="提高预算后重新处理",
                detail=(
                    "预算属于创建任务时的快照，不能在原任务上提高：请用更大的预算重新创建一个任务"
                    "（已完成窗口命中缓存，不会重复计费）。"
                ),
                paid=True,
                endpoint="POST /api/jobs",
            )
        ]
    if state is JobState.NEEDS_RECONCILIATION:
        actions = [
            RecoveryActionOut(
                action="reconcile_keep",
                label="保留未知结果",
                detail=(
                    f"有 {unknown_runs} 次远程调用结果未知：保留记录并停止重发，之后可人工补确认。"
                ),
                paid=False,
                endpoint="POST /api/jobs/{id}/reconcile",
            ),
            RecoveryActionOut(
                action="reconcile_retry",
                label="确认重发这些窗口",
                detail="只有在你确认“重复计费可以接受”时才选择：窗口会回到队列并重新调用模型。",
                paid=True,
                endpoint="POST /api/jobs/{id}/reconcile",
            ),
        ]
        return actions
    # FAILED
    actions = [run_now]
    if requires_credential:
        actions = [open_settings, run_now]
    return actions


def recovery_summary(state: JobState, *, remaining_windows: int, unknown_runs: int) -> str:
    if state is JobState.COMPLETED:
        return "任务已完成；原文与标注不受本模块影响。"
    if state is JobState.QUEUED:
        return "任务已排队，等待本地调度器执行。"
    if state is JobState.RUNNING:
        return "任务正在执行；暂停会在当前窗口结束后生效。"
    if state is JobState.PAUSING:
        return "正在暂停：当前窗口返回后进入已暂停。"
    if state is JobState.PAUSED:
        return f"已暂停，还有 {remaining_windows} 个窗口未处理；继续时会复用已完成的窗口。"
    if state is JobState.PARTIAL:
        return f"部分完成：还有 {remaining_windows} 个窗口未处理，已完成的结果保持有效。"
    if state is JobState.BUDGET_EXHAUSTED:
        return "达到预算上限后停止：没有继续产生调用，剩余窗口保持未处理。"
    if state is JobState.NEEDS_RECONCILIATION:
        return (
            f"有 {unknown_runs} 次调用结果未知：系统**不会**自动重发，请选择保留未知或确认重发。"
        )
    return "任务失败：已完成窗口与标注保持有效，原文始终可读。"

def job_recovery(
    session: Session, job: Job, *, has_credential: bool | None = None
) -> JobRecoveryOut:
    """任务恢复状态：动作 + 说明 + 剩余窗口 + 未知用量/未知结果计数。"""

    rows = job_windows(session, job.id)
    done = sum(1 for row in rows if row.state is JobState.COMPLETED)
    remaining = sum(
        1 for row in rows if row.state in {JobState.QUEUED, JobState.NEEDS_RECONCILIATION}
    )
    runs = list(
        session.execute(select(InferenceRun).where(InferenceRun.job_id == job.id)).scalars()
    )
    unknown_runs = sum(
        1
        for run in runs
        if run.state is InferenceRunState.UNKNOWN_OUTCOME or run.usage_json is None
    )
    snapshot = profile_snapshot_of(job)
    mode = str(snapshot.get("credential_mode", CredentialMode.NONE.value))
    requires_credential = mode != CredentialMode.NONE.value and has_credential is False
    progress = progress_of(job)
    retry_in = progress.get("retry_in_seconds")
    result = JobRecoveryOut(
        job_id=job.id,
        state=job.state,
        summary=recovery_summary(
            job.state, remaining_windows=remaining, unknown_runs=unknown_runs
        ),
        actions=recovery_actions(
            job.state,
            remaining_windows=remaining,
            unknown_runs=unknown_runs,
            requires_credential=requires_credential,
        ),
        windows_total=len(rows),
        windows_done=done,
        remaining_windows=remaining,
        unknown_runs=unknown_runs,
        unknown_usage_runs=sum(1 for run in runs if run.usage_json is None),
        retry_in_seconds=int(retry_in) if isinstance(retry_in, int) else None,
        requires_credential=requires_credential,
        last_error=job.last_error,
        updated_at=job.updated_at.isoformat(),
    )
    if job.kind is JobKind.CHARACTER_MERGE and job.state not in {
        JobState.QUEUED,
        JobState.RUNNING,
        JobState.PAUSING,
    }:
        result.actions = []
        result.summary = "请在全书人物页查看合并结果；再次分析需新建一次自动合并，可能再次计费。"
    if job.kind is JobKind.CHARACTER_ROSTER and job.state in {
        JobState.FAILED, JobState.NEEDS_RECONCILIATION, JobState.PAUSED, JobState.PARTIAL,
    } and not json.loads(job.range_json or "{}").get("roster_repair_protocol"):
        from ..jobs.roster_receipts import has_returned_result

        if not progress.get("receipt_recovery_blocked") and has_returned_result(session, job):
            result.actions = [RecoveryActionOut(
                action="resume", label="恢复已返回的人物结果",
                detail="校验并保存已有返回，不重新调用模型；凭据不完整时会提示先核对。",
                paid=False, endpoint="POST /api/jobs/{id}/resume",
            )]
            result.summary = "模型已返回，人物结果尚未保存完成，可以直接恢复。"
    return result


def recover_on_startup(
    session_factory: sessionmaker[Session],
    *,
    lease_seconds: int = 900,
    now: datetime | None = None,
) -> StartupRecovery:
    """进程重启后的恢复扫描。

    - 超过租约仍是 DISPATCHED 的尝试 → `UNKNOWN_OUTCOME` + 任务/窗口
      `NEEDS_RECONCILIATION`（不自动重发）。
    - `PAUSING` → `PAUSED`（worker 已随进程退出，暂停确定生效）。
    - `RUNNING` → `PARTIAL`（上次执行被中断；已完成窗口保留，未完成窗口需要用户显式继续）。
    """

    summary = StartupRecovery()
    summary.stale_runs = reconcile_stale_runs(
        session_factory, lease_seconds=lease_seconds, now=now or datetime.now(tz=UTC)
    )
    with session_factory() as session:
        pausing = list(
            session.execute(select(Job).where(Job.state == JobState.PAUSING)).scalars()
        )
        for job in pausing:
            job.state = JobState.PAUSED
            job.last_error = "进程重启：暂停已生效（已完成窗口保留，可从检查点继续）"
            summary.paused_jobs.append(job.id)
        running = list(
            session.execute(select(Job).where(Job.state == JobState.RUNNING)).scalars()
        )
        for job in running:
            job.state = JobState.PARTIAL
            job.last_error = "进程重启：上一次执行被中断；已完成窗口保留，未完成窗口需显式继续"
            summary.interrupted_jobs.append(job.id)
        if pausing or running:
            session.commit()
    return summary
