"""任务创建、估算与用量汇总（DEVELOPMENT.md 5.2 / 5.5，T10）。

- **幂等创建**：同一 `idempotency_key` + 相同请求摘要 → 返回既有任务；
  同一 key 但摘要不同 → 409 `IDEMPOTENCY_CONFLICT`。
- **估算只做本地计算**：用 T08 的窗口规划给出 token 估算，不调用模型；
  缺价格资料时只给 token，不伪造金额。
- **用量按每次尝试汇总**：`usage_json` 为 NULL 表示提供方没给 usage，单独计数，绝不按 0 计。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..api.errors import ApiError
from ..config import Settings
from ..context.budget import DEFAULT_POLICY, BudgetPolicy
from ..context.service import plan_range
from ..domain.enums import (
    CredentialMode,
    ErrorCode,
    JobKind,
    JobPurpose,
    JobState,
    ReadingMode,
)
from ..domain.jobs import JobDetailOut, JobWindowOut
from ..storage.cache import fingerprint
from ..storage.models import Book, BookVersion, InferenceRun, Job, JobWindow, ModelProfile


@dataclass(frozen=True)
class JobEstimate:
    window_count: int
    target_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimator: dict[str, Any]
    policy: dict[str, Any]
    notes: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "window_count": self.window_count,
            "target_count": self.target_count,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "estimator": self.estimator,
            "policy": self.policy,
            "notes": list(self.notes),
        }


def digest_request(payload: dict[str, Any]) -> str:
    return fingerprint(payload)


def estimate_inference(
    session: Session,
    settings: Settings,
    version: BookVersion,
    *,
    start_cp: int = 0,
    end_cp: int | None = None,
    reading_mode: ReadingMode = ReadingMode.INITIAL,
    visible_horizon_cp: int | None = None,
    policy: BudgetPolicy | None = None,
    output_tokens_per_target: int = 20,
) -> JobEstimate:
    """纯本地估算：不调用模型、不写数据库。"""

    resolved_policy = policy or DEFAULT_POLICY
    plan = plan_range(
        session,
        settings,
        version,
        start_cp=start_cp,
        end_cp=end_cp,
        reading_mode=reading_mode,
        visible_horizon_cp=visible_horizon_cp,
        policy=resolved_policy,
    )
    targets = sum(len(window.target_quote_ids) for window in plan.windows)
    input_tokens = sum(window.budget["total_tokens"] for window in plan.windows)
    output_tokens = targets * output_tokens_per_target
    notes = [
        "估算来自本地启发式 token 口径，不是真实计费依据；实际用量以提供方 usage 为准。",
        "输出预留按每条目标对白 20 token 粗估（可在预算里调整）。",
    ]
    if plan.stats.get("oversized_targets"):
        notes.append(f"其中 {plan.stats['oversized_targets']} 条目标超长，会单独成窗口或保留待定。")
    return JobEstimate(
        window_count=len(plan.windows),
        target_count=targets,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        estimator=plan.windows[0].budget["estimator"] if plan.windows else {},
        policy=resolved_policy.as_key(),
        notes=notes,
    )


def profile_snapshot(profile: ModelProfile | None) -> dict[str, Any] | None:
    """任务保存的配置快照：只有非敏感字段 + 提示/协议版本。**不含任何密钥**。"""

    if profile is None:
        return None
    return {
        "profile_id": profile.id,
        "name": profile.name,
        "protocol": profile.protocol,
        "base_url": profile.base_url,
        "model": profile.model,
        "params": json.loads(profile.params_json or "{}"),
        "credential_mode": profile.credential_mode.value,
    }


def create_inference_job(
    session: Session,
    *,
    book: Book,
    version: BookVersion,
    profile: ModelProfile | None,
    purpose: JobPurpose,
    range_payload: dict[str, Any],
    budget: dict[str, Any],
    idempotency_key: str,
    reading_mode: ReadingMode,
    visible_horizon_cp: int | None,
    kind: JobKind = JobKind.INFERENCE,
) -> tuple[Job, bool]:
    """幂等创建任务；返回 ``(job, created)``。"""

    request_payload = {
        "kind": kind.value,
        "book_id": book.id,
        "book_version_id": version.id,
        "profile_id": profile.id if profile else None,
        "range": range_payload,
        "budget": budget,
        "reading_mode": reading_mode.value,
        "visible_horizon_cp": visible_horizon_cp,
    }
    digest = digest_request(request_payload)

    existing = session.execute(
        select(Job).where(Job.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_digest == digest:
            return existing, False
        raise ApiError(
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "同一幂等键已被不同的请求使用",
            details={
                "idempotency_key": idempotency_key,
                "job_id": existing.id,
                "existing_digest": existing.request_digest,
                "request_digest": digest,
            },
            status_code=409,
        )

    job = Job(
        kind=kind,
        purpose=purpose,
        book_id=book.id,
        book_version_id=version.id,
        state=JobState.QUEUED,
        range_json=json.dumps(
            {
                **range_payload,
                "reading_mode": reading_mode.value,
                "visible_horizon_cp": visible_horizon_cp,
            },
            ensure_ascii=False,
        ),
        profile_snapshot_json=json.dumps(profile_snapshot(profile), ensure_ascii=False)
        if profile
        else None,
        budget_json=json.dumps(budget, ensure_ascii=False),
        progress_json=json.dumps({"stage": "queued", "windows_done": 0}, ensure_ascii=False),
        idempotency_key=idempotency_key,
        request_digest=digest,
    )
    session.add(job)
    session.flush()
    return job, True


def window_out(row: JobWindow, attempts: int = 0) -> JobWindowOut:
    try:
        targets = json.loads(row.target_ids_json or "[]")
    except json.JSONDecodeError:
        targets = []
    return JobWindowOut(
        window_id=row.window_id,
        state=row.state,
        target_count=len(targets) if isinstance(targets, list) else 0,
        dependency_hash=row.dependency_hash,
        attempts=attempts,
    )


def job_detail(session: Session, job: Job) -> JobDetailOut:
    """任务状态快照（窗口 + 用量 + 剩余）：`POST /api/jobs` 与局部复核共用。"""

    rows = job_windows(session, job.id)
    attempts_by_window: dict[str, int] = {}
    for run in session.execute(
        select(InferenceRun).where(InferenceRun.job_id == job.id)
    ).scalars():
        if run.window_id:
            attempts_by_window[run.window_id] = attempts_by_window.get(run.window_id, 0) + 1
    windows = [window_out(row, attempts_by_window.get(row.window_id, 0)) for row in rows]
    usage = spent_tokens(session, job.id)
    runs = list(
        session.execute(select(InferenceRun).where(InferenceRun.job_id == job.id)).scalars()
    )
    unknown_runs = sum(1 for run in runs if run.usage_json is None)
    progress = json.loads(job.progress_json) if job.progress_json else None
    return JobDetailOut(
        id=job.id,
        kind=job.kind,
        purpose=job.purpose,
        state=job.state,
        book_id=job.book_id,
        book_version_id=job.book_version_id,
        progress=progress,
        checkpoint=json.loads(job.checkpoint_json) if job.checkpoint_json else None,
        last_error=job.last_error,
        windows=windows,
        remaining_windows=sum(
            1 for row in rows if row.state in {JobState.QUEUED, JobState.NEEDS_RECONCILIATION}
        ),
        windows_total=len(rows),
        calls=len(runs),
        cached_windows=int(
            (progress or {}).get("cached_windows", 0) if isinstance(progress, dict) else 0
        )
        or 0,
        unknown_usage_runs=unknown_runs,
        usage=usage,
        created_at=job.created_at.isoformat(),
        updated_at=job.updated_at.isoformat(),
    )

def job_windows(session: Session, job_id: str) -> list[JobWindow]:
    return list(
        session.execute(
            select(JobWindow).where(JobWindow.job_id == job_id).order_by(JobWindow.window_id)
        ).scalars()
    )


def usage_summary(session: Session, book_id: str) -> dict[str, Any]:
    """按任务/状态/模型汇总用量；未知用量单独计数（不按 0 计）。"""

    rows = list(
        session.execute(
            select(InferenceRun, Job)
            .join(Job, InferenceRun.job_id == Job.id, isouter=True)
            .where(Job.book_id == book_id)
        )
    )
    input_tokens = output_tokens = total_tokens = 0
    unknown_runs = 0
    by_state: dict[str, int] = {}
    by_model: dict[str, int] = {}
    for run, job in rows:
        by_state[run.state.value] = by_state.get(run.state.value, 0) + 1
        # T17：按**每次尝试自己的**配置快照归属模型（成本路由会为个别窗口换模型，
        # 只看任务级快照会把强模型用量算到基础模型头上）。
        snapshot_raw = run.profile_snapshot_json or (
            job.profile_snapshot_json if job is not None else None
        )
        model = ""
        if snapshot_raw:
            try:
                model = json.loads(snapshot_raw).get("model", "") or ""
            except json.JSONDecodeError:
                model = ""
        if model:
            by_model[model] = by_model.get(model, 0) + 1
        if not run.usage_json:
            unknown_runs += 1
            continue
        try:
            usage = json.loads(run.usage_json)
        except json.JSONDecodeError:
            unknown_runs += 1
            continue
        if usage.get("unknown") or all(
            usage.get(key) is None
            for key in ("input_tokens", "output_tokens", "total_tokens")
        ):
            unknown_runs += 1
            continue
        input_tokens += int(usage.get("input_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or 0)
        total_tokens += int(usage.get("total_tokens") or 0)
    return {
        "book_id": book_id,
        "runs": len(rows),
        "unknown_usage_runs": unknown_runs,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "by_state": by_state,
        "by_model": by_model,
        "currency": None,  # 缺价格资料时不给金额
        "cost": None,
    }


def spent_tokens(session: Session, job_id: str) -> dict[str, int]:
    """已结算的 token（未知用量按预留口径计入 ``unknown_runs``）。"""

    rows = list(
        session.execute(select(InferenceRun).where(InferenceRun.job_id == job_id)).scalars()
    )
    input_tokens = output_tokens = 0
    unknown_runs = 0
    for run in rows:
        if not run.usage_json:
            # INVALID_MODEL_OUTPUT 表示提供方通常已经生成过内容；缺 usage 不能按零费用处理。
            if run.state.value in {"DISPATCHED", "UNKNOWN_OUTCOME", "SUCCEEDED"} or (
                run.state.value == "FAILED" and run.error_code == "INVALID_MODEL_OUTPUT"
            ):
                unknown_runs += 1
            continue
        try:
            usage = json.loads(run.usage_json)
        except json.JSONDecodeError:
            unknown_runs += 1
            continue
        if usage.get("unknown"):
            unknown_runs += 1
            continue
        input_tokens += int(usage.get("input_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or 0)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "unknown_runs": unknown_runs,
    }


def count_runs(session: Session, job_id: str, states: tuple[str, ...]) -> int:
    return int(
        session.execute(
            select(func.count(InferenceRun.id)).where(
                InferenceRun.job_id == job_id, InferenceRun.state.in_(states)
            )
        ).scalar_one()
    )


def profile_for_job(session: Session, job: Job) -> ModelProfile | None:
    if not job.profile_snapshot_json:
        return None
    try:
        snapshot = json.loads(job.profile_snapshot_json)
    except json.JSONDecodeError:
        return None
    profile_id = snapshot.get("profile_id")
    if not profile_id:
        return None
    return session.get(ModelProfile, profile_id)


def credential_reference(profile: ModelProfile | None) -> str | None:
    if profile is None:
        return None
    return profile.credential_ref or f"model-profile/{profile.id}"


def credential_mode_of(profile: ModelProfile | None) -> CredentialMode:
    return profile.credential_mode if profile is not None else CredentialMode.NONE
