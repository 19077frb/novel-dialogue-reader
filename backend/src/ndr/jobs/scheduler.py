"""单进程调度器（DEVELOPMENT.md 5.5，T10）。

- **每个窗口一次有效提交**：按文档顺序执行，完成即写检查点；再次运行跳过已完成窗口。
- **网络调用与数据库事务分离**：调用前用短事务写 PREPARED → DISPATCHED 并提交；
  适配器调用期间不持有事务；返回后用另一个事务应用结果并结算用量。
- **未知结果不自动重发**：DISPATCHED 却没有落库结果的尝试（进程中断、超时）标为
  `UNKNOWN_OUTCOME`，窗口与任务进入 `NEEDS_RECONCILIATION`，等用户显式决定（F15）。
- **缓存命中不调用模型**：同语义输入（含 horizon/阅读模式/提示版本/策略版本）直接复用（F16）。
- **预算**：调用前按估算预留、调用后按 usage 结算；未知用量按预留口径计入并单独标记（F20）。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..context.budget import DEFAULT_POLICY, BudgetPolicy
from ..context.service import load_window_inputs, plan_range
from ..domain.enums import (
    AnnotationSource,
    CredentialMode,
    InferenceRunState,
    JobState,
    ReadingMode,
)
from ..llm.adapter import ProviderAdapter
from ..llm.adapters import AdapterSpec, build_adapter
from ..llm.errors import ProviderError, ProviderErrorKind
from ..llm.prompts import LABELING_PROMPT_VERSION
from ..llm.validation import parse_and_validate
from ..scenes.engine import apply_window
from ..scenes.runner import _messages_for, _targets_for
from ..scenes.state import SceneState
from ..storage.cache import CacheKeyParts, ResultCacheStore, compute_cache_key, fingerprint
from ..storage.models import BookVersion, InferenceRun, Job, JobWindow
from .service import (
    credential_mode_of,
    credential_reference,
    profile_for_job,
    spent_tokens,
)

SCHEDULER_VERSION = "scheduler-1"
DEFAULT_LEASE_SECONDS = 900
LABELING_MAX_TOKENS = 800
OUTPUT_TOKENS_PER_TARGET = 20

TERMINAL_STATES = {
    JobState.COMPLETED,
    JobState.FAILED,
    JobState.BUDGET_EXHAUSTED,
    JobState.PAUSED,
    JobState.NEEDS_RECONCILIATION,
}
STOP_STATES = TERMINAL_STATES | {JobState.PARTIAL}


@dataclass
class JobRunOutcome:
    job_id: str
    state: JobState
    windows_total: int = 0
    windows_done: int = 0
    cached_windows: int = 0
    calls: int = 0
    unknown_runs: int = 0
    budget_exhausted: bool = False
    errors: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "state": self.state.value,
            "windows_total": self.windows_total,
            "windows_done": self.windows_done,
            "cached_windows": self.cached_windows,
            "calls": self.calls,
            "unknown_runs": self.unknown_runs,
            "budget_exhausted": self.budget_exhausted,
            "errors": list(self.errors),
            "usage": self.usage,
        }


def _json_of(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _budget_of(job: Job) -> dict[str, Any]:
    return _json_of(job.budget_json)


def _range_of(job: Job) -> dict[str, Any]:
    return _json_of(job.range_json)


def _reading_mode_of(job: Job) -> ReadingMode:
    try:
        return ReadingMode(_range_of(job).get("reading_mode", ReadingMode.INITIAL.value))
    except ValueError:
        return ReadingMode.INITIAL


def _load_state(job: Job) -> SceneState:
    return SceneState.from_snapshot(_json_of(job.checkpoint_json).get("scene_state"))


def _plan(
    session: Session,
    settings: Settings,
    job: Job,
    version: BookVersion,
    policy: BudgetPolicy,
):  # noqa: ANN202
    payload = _range_of(job)
    return plan_range(
        session,
        settings,
        version,
        start_cp=int(payload.get("start_cp", 0) or 0),
        end_cp=payload.get("end_cp"),
        reading_mode=_reading_mode_of(job),
        visible_horizon_cp=payload.get("visible_horizon_cp"),
        policy=policy,
    )


def ensure_windows(
    session: Session,
    settings: Settings,
    job: Job,
    version: BookVersion,
    *,
    policy: BudgetPolicy | None = None,
) -> list[JobWindow]:
    """按任务范围规划窗口并落库 `job_windows`（幂等）。"""

    plan = _plan(session, settings, job, version, policy or DEFAULT_POLICY)
    existing = {
        row.window_id: row
        for row in session.execute(select(JobWindow).where(JobWindow.job_id == job.id)).scalars()
    }
    rows: list[JobWindow] = []
    for window in plan.windows:
        row = existing.get(window.window_id)
        if row is None:
            row = JobWindow(
                job_id=job.id,
                window_id=window.window_id,
                target_ids_json=json.dumps(list(window.target_quote_ids), ensure_ascii=False),
                dependency_hash=window.dependency_hash,
                state=JobState.QUEUED,
            )
            session.add(row)
            session.flush()
        rows.append(row)
    return rows


def _cache_key_for(
    *, job: Job, version: BookVersion, window, snapshot: dict[str, Any] | None  # noqa: ANN001
) -> str:
    snapshot = snapshot or {}
    return compute_cache_key(
        CacheKeyParts(
            book_version_id=version.id,
            target_ids=list(window.target_quote_ids),
            input_fingerprint=fingerprint([fragment.text for fragment in window.fragments]),
            model=str(snapshot.get("model", "")),
            params=snapshot.get("params", {}) or {},
            protocol_version=str(snapshot.get("protocol", "")),
            prompt_version=LABELING_PROMPT_VERSION,
            schema_version="1.0",
            policy_version=window.policy_version,
            dependency_hash=window.dependency_hash,
            reading_mode=_reading_mode_of(job).value,
            visible_horizon_cp=window.visible_horizon_cp,
        )
    )


def _positions(inputs, window):  # noqa: ANN001, ANN202
    quote_positions = {quote.quote_id: (quote.start_cp, quote.end_cp) for quote in inputs.quotes}
    gap_positions = {gap.gap_id: gap.start_cp for gap in inputs.gaps}
    evidence_positions = dict(gap_positions)
    evidence_positions.update({quote.quote_id: quote.start_cp for quote in inputs.quotes})
    for fragment in window.fragments:
        evidence_positions.setdefault(fragment.fragment_id, fragment.end_cp)
    return quote_positions, gap_positions, evidence_positions


def _apply_payload(
    session: Session,
    *,
    window,  # noqa: ANN001
    inputs,  # noqa: ANN001
    state: SceneState,
    raw: Any,
    run_id: str | None,
    cache_key: str,
) -> tuple[bool, list[str]]:
    """校验并应用一次输出；成功时写缓存。返回 ``(ok, validation_codes)``。"""

    payload = (
        {key: value for key, value in raw.items() if not str(key).startswith("_")}
        if isinstance(raw, dict)
        else raw
    )
    report = parse_and_validate(payload, _targets_for(window, state))
    if not report.ok or report.output is None:
        return False, report.error_codes
    quote_positions, gap_positions, evidence_positions = _positions(inputs, window)
    result = apply_window(
        session,
        book_version_id=inputs.book_version_id,
        window=window,
        output=report.output,
        state=state,
        quote_positions=quote_positions,
        gap_positions=gap_positions,
        evidence_positions=evidence_positions,
        dependency_hash=window.dependency_hash,
        source=AnnotationSource.MODEL,
        run_id=run_id,
    )
    if not result.validation_ok:
        return False, result.validation_codes
    ResultCacheStore(session).put(
        cache_key=cache_key,
        result_json=json.dumps(report.output.model_dump(mode="json"), ensure_ascii=False),
        dependency_hash=window.dependency_hash,
        created_run_id=run_id,
    )
    return True, []


def _build_adapter(
    settings: Settings,
    credentials,  # noqa: ANN001
    *,
    snapshot: dict[str, Any] | None,
    credential_mode: CredentialMode,
    credential_ref: str | None,
) -> ProviderAdapter:
    snapshot = snapshot or {}
    return build_adapter(
        AdapterSpec(
            protocol=str(snapshot.get("protocol", "chat-completions-compatible")),
            base_url=str(snapshot.get("base_url", "")),
            model=str(snapshot.get("model", "")),
            params=snapshot.get("params", {}) or {},
            credential_mode=credential_mode,
            credential_ref=credential_ref,
            timeout_seconds=settings.llm_timeout_seconds,
        ),
        credentials,
        allow_fake_provider=settings.allow_fake_provider,
    )


def run_job(
    session_factory: sessionmaker[Session],
    settings: Settings,
    *,
    job_id: str,
    credentials=None,  # noqa: ANN001 - CredentialService
    adapter_factory: Callable[[Job, dict[str, Any] | None], ProviderAdapter] | None = None,
    max_windows: int | None = None,
) -> JobRunOutcome:
    """把任务跑到终态（或暂停 / 预算耗尽 / 需要人工对账）。"""

    outcome = JobRunOutcome(job_id=job_id, state=JobState.QUEUED)
    calls = cached = done = 0
    policy = DEFAULT_POLICY

    # ---------- 阶段 1：准备（短事务） ----------
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None:
            outcome.errors.append("job_not_found")
            outcome.state = JobState.FAILED
            return outcome
        if job.state in STOP_STATES:
            outcome.state = job.state
            return outcome
        if not job.book_version_id:
            job.state = JobState.FAILED
            job.last_error = "任务缺少书籍版本"
            session.commit()
            outcome.state = JobState.FAILED
            return outcome
        version = session.get(BookVersion, job.book_version_id)
        if version is None:
            job.state = JobState.FAILED
            job.last_error = "书籍版本不存在"
            session.commit()
            outcome.state = JobState.FAILED
            return outcome

        # 不要覆盖 PAUSING：让窗口之间的暂停检查有机会生效
        if job.state is not JobState.PAUSING:
            job.state = JobState.RUNNING
        ensure_windows(session, settings, job, version, policy=policy)
        plan = _plan(session, settings, job, version, policy)
        inputs = load_window_inputs(
            session,
            settings,
            version,
            reading_mode=_reading_mode_of(job),
            visible_horizon_cp=_range_of(job).get("visible_horizon_cp"),
            policy=policy,
        )
        state = _load_state(job)
        snapshot = _json_of(job.profile_snapshot_json) or None
        profile = profile_for_job(session, job)
        credential_mode = credential_mode_of(profile)
        credential_ref = credential_reference(profile)
        session.commit()

    outcome.windows_total = len(plan.windows)
    adapter: ProviderAdapter | None = None
    if adapter_factory is not None:
        adapter = adapter_factory(job, snapshot)
    elif credentials is not None:
        adapter = _build_adapter(
            settings,
            credentials,
            snapshot=snapshot,
            credential_mode=credential_mode,
            credential_ref=credential_ref,
        )

    # ---------- 阶段 2：逐窗口 ----------
    for window in plan.windows:
        if max_windows is not None and done + cached >= max_windows:
            break

        with session_factory() as session:
            job = session.get(Job, job_id)
            assert job is not None
            if job.state is JobState.PAUSING:
                job.state = JobState.PAUSED
                job.progress_json = json.dumps(
                    {"stage": "paused", "windows_done": done}, ensure_ascii=False
                )
                session.commit()
                outcome.state = JobState.PAUSED
                return outcome
            row = session.execute(
                select(JobWindow).where(
                    JobWindow.job_id == job_id, JobWindow.window_id == window.window_id
                )
            ).scalar_one_or_none()
            row_state = row.state if row is not None else JobState.QUEUED
            if row_state is JobState.COMPLETED:
                done += 1
                continue
            if row_state is JobState.NEEDS_RECONCILIATION:
                job.state = JobState.NEEDS_RECONCILIATION
                session.commit()
                outcome.state = JobState.NEEDS_RECONCILIATION
                return outcome
            spent = spent_tokens(session, job_id)
            job_snapshot = job
            state = _load_state(job)
            session.commit()

        # 预算预留（含输出预留；未知用量也按此保守口径）
        reserve = window.budget["total_tokens"] + OUTPUT_TOKENS_PER_TARGET * len(
            window.target_quote_ids
        )
        max_input = _budget_of(job_snapshot).get("max_input_tokens")
        if max_input is not None and spent["input_tokens"] + reserve > int(max_input):
            with session_factory() as session:
                job = session.get(Job, job_id)
                assert job is not None
                job.state = JobState.BUDGET_EXHAUSTED
                job.last_error = "达到输入预算上限，剩余窗口未处理"
                job.progress_json = json.dumps(
                    {"stage": "budget_exhausted", "windows_done": done}, ensure_ascii=False
                )
                session.commit()
            outcome.state = JobState.BUDGET_EXHAUSTED
            outcome.budget_exhausted = True
            break

        cache_key = _cache_key_for(
            job=job_snapshot, version=version, window=window, snapshot=snapshot
        )

        # 缓存命中：不调用模型，也不新增推理尝试
        with session_factory() as session:
            cached_result = ResultCacheStore(session).get(cache_key)
        if cached_result is not None:
            with session_factory() as session:
                job = session.get(Job, job_id)
                assert job is not None
                state = _load_state(job)
                ok, codes = _apply_payload(
                    session,
                    window=window,
                    inputs=inputs,
                    state=state,
                    raw=cached_result.payload(),
                    run_id=None,
                    cache_key=cache_key,
                )
                row = session.execute(
                    select(JobWindow).where(
                        JobWindow.job_id == job_id, JobWindow.window_id == window.window_id
                    )
                ).scalar_one_or_none()
                if ok:
                    if row is not None:
                        row.state = JobState.COMPLETED
                    done += 1
                    cached += 1
                    job.checkpoint_json = json.dumps(
                        {
                            "scheduler_version": SCHEDULER_VERSION,
                            "scene_state": state.snapshot(),
                            "last_window_id": window.window_id,
                            "windows_done": done,
                            "cached_windows": cached,
                        },
                        ensure_ascii=False,
                    )
                    job.progress_json = json.dumps(
                        {"stage": "running", "windows_done": done, "cached_windows": cached},
                        ensure_ascii=False,
                    )
                job.last_error = None if ok else f"缓存结果未通过校验：{codes}"
                session.commit()
            if ok:
                continue

        if adapter is None:
            outcome.errors.append("adapter_unavailable")
            with session_factory() as session:
                job = session.get(Job, job_id)
                assert job is not None
                job.state = JobState.FAILED
                job.last_error = "没有可用的模型适配器（缺少凭据或配置被删除）"
                session.commit()
            outcome.state = JobState.FAILED
            return outcome

        # 一次尝试：PREPARED → DISPATCHED（提交）→ 调用（无事务）→ 应用 + 结算（新事务）
        with session_factory() as session:
            run = InferenceRun(
                job_id=job_id,
                window_id=window.window_id,
                profile_snapshot_json=json.dumps(snapshot or {}, ensure_ascii=False),
                request_fingerprint=window.dependency_hash,
                state=InferenceRunState.PREPARED,
            )
            session.add(run)
            session.flush()
            run.state = InferenceRunState.DISPATCHED
            run_id = run.id
            session.commit()

        started = time.perf_counter()
        error: ProviderError | None = None
        raw: Any = None
        try:
            raw = _dispatch(adapter, window=window, state=state)
        except ProviderError as exc:
            error = exc
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        with session_factory() as session:
            job = session.get(Job, job_id)
            assert job is not None
            run = session.get(InferenceRun, run_id)
            assert run is not None
            run.elapsed_ms = elapsed_ms
            row = session.execute(
                select(JobWindow).where(
                    JobWindow.job_id == job_id, JobWindow.window_id == window.window_id
                )
            ).scalar_one_or_none()

            if error is not None:
                outcome.errors.append(f"{window.window_id}:{error.code.value}")
                if error.kind is ProviderErrorKind.TIMEOUT:
                    # 超时可能已经计费且结果未知：不自动重发，交人工对账（F15/F20）
                    run.state = InferenceRunState.UNKNOWN_OUTCOME
                    run.error_code = error.code.value
                    if row is not None:
                        row.state = JobState.NEEDS_RECONCILIATION
                    job.state = JobState.NEEDS_RECONCILIATION
                    job.last_error = f"{error.code.value}: {error.message}"
                    session.commit()
                    outcome.state = JobState.NEEDS_RECONCILIATION
                    outcome.unknown_runs += 1
                    return outcome
                run.state = InferenceRunState.FAILED
                run.error_code = error.code.value
                if row is not None:
                    row.state = JobState.FAILED
                job.state = JobState.PARTIAL if done else JobState.FAILED
                job.last_error = f"{error.code.value}: {error.message}"
                session.commit()
                outcome.state = job.state
                break

            calls += 1
            usage = raw.get("_usage") if isinstance(raw, dict) else None
            state = _load_state(job)
            ok, codes = _apply_payload(
                session,
                window=window,
                inputs=inputs,
                state=state,
                raw=raw,
                run_id=run_id,
                cache_key=cache_key,
            )
            if not ok:
                run.state = InferenceRunState.FAILED
                run.error_code = "INVALID_MODEL_OUTPUT"
                if row is not None:
                    row.state = JobState.FAILED
                job.state = JobState.PARTIAL if done else JobState.FAILED
                job.last_error = f"模型输出未通过校验：{codes}"
                session.commit()
                outcome.state = job.state
                break

            run.state = InferenceRunState.SUCCEEDED
            known_usage = bool(usage) and not usage.get("unknown")
            run.usage_json = json.dumps(usage, ensure_ascii=False) if known_usage else None
            if not known_usage:
                outcome.unknown_runs += 1
            if row is not None:
                row.state = JobState.COMPLETED
            done += 1
            job.checkpoint_json = json.dumps(
                {
                    "scheduler_version": SCHEDULER_VERSION,
                    "scene_state": state.snapshot(),
                    "last_window_id": window.window_id,
                    "windows_done": done,
                    "calls": calls,
                    "unknown_runs": outcome.unknown_runs,
                },
                ensure_ascii=False,
            )
            job.progress_json = json.dumps(
                {
                    "stage": "running",
                    "windows_done": done,
                    "calls": calls,
                    "cached_windows": cached,
                    "unknown_runs": outcome.unknown_runs,
                },
                ensure_ascii=False,
            )
            job.last_error = None
            session.commit()

    # ---------- 阶段 3：收尾 ----------
    with session_factory() as session:
        job = session.get(Job, job_id)
        assert job is not None
        outcome.windows_done = done
        outcome.cached_windows = cached
        outcome.calls = calls
        outcome.usage = spent_tokens(session, job_id)
        if job.state not in STOP_STATES:
            if outcome.windows_total == 0 or done >= outcome.windows_total:
                job.state = JobState.COMPLETED
                job.progress_json = json.dumps(
                    {
                        "stage": "completed",
                        "windows_done": done,
                        "cached_windows": cached,
                        "calls": calls,
                    },
                    ensure_ascii=False,
                )
            else:
                job.state = JobState.PARTIAL
                job.progress_json = json.dumps(
                    {"stage": "partial", "windows_done": done}, ensure_ascii=False
                )
            session.commit()
        outcome.state = job.state
    return outcome


def _dispatch(adapter: ProviderAdapter, *, window, state: SceneState) -> Any:  # noqa: ANN001
    payload = {
        "messages": _messages_for(window=window, state=state, locked_summary=None),
        "max_tokens": LABELING_MAX_TOKENS,
        "json_object": True,
        "target_quote_ids": list(window.target_quote_ids),
    }
    return asyncio.run(adapter.generate_labels(payload))


def reconcile_stale_runs(
    session_factory: sessionmaker[Session],
    *,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> list[str]:
    """把超过租约仍处于 DISPATCHED 的尝试标为未知结果（进程重启后的扫描，F15）。"""

    moment = now or datetime.now(tz=UTC)
    cutoff = moment - timedelta(seconds=lease_seconds)
    reconciled: list[str] = []
    with session_factory() as session:
        runs = list(
            session.execute(
                select(InferenceRun).where(InferenceRun.state == InferenceRunState.DISPATCHED)
            ).scalars()
        )
        for run in runs:
            updated = run.updated_at
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            if updated > cutoff:
                continue
            run.state = InferenceRunState.UNKNOWN_OUTCOME
            run.error_code = "UNKNOWN_OUTCOME"
            if run.job_id:
                job = session.get(Job, run.job_id)
                if job is not None and job.state not in {JobState.COMPLETED, JobState.FAILED}:
                    job.state = JobState.NEEDS_RECONCILIATION
                    job.last_error = "存在未知结果的远程调用，需人工确认后再决定是否重发"
                if run.window_id:
                    row = session.execute(
                        select(JobWindow).where(
                            JobWindow.job_id == run.job_id, JobWindow.window_id == run.window_id
                        )
                    ).scalar_one_or_none()
                    if row is not None:
                        row.state = JobState.NEEDS_RECONCILIATION
            reconciled.append(run.id)
        session.commit()
    return reconciled


def reconcile_job(session: Session, job: Job, *, action: str) -> dict[str, Any]:
    """处理 NEEDS_RECONCILIATION：``retry`` 显式重发；``keep_unknown`` 保留未知结果。"""

    rows = list(
        session.execute(select(JobWindow).where(JobWindow.job_id == job.id)).scalars()
    )
    pending = [row for row in rows if row.state is JobState.NEEDS_RECONCILIATION]
    if action == "retry":
        for row in pending:
            row.state = JobState.QUEUED
        job.state = JobState.QUEUED
        job.last_error = None
    else:
        for row in pending:
            row.state = JobState.PARTIAL
        job.state = JobState.PARTIAL
    session.flush()
    return {
        "job_id": job.id,
        "action": action,
        "affected_windows": [row.window_id for row in pending],
    }
