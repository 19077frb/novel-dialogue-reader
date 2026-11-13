"""任务创建、估算与用量汇总。

- **幂等创建**：同一 `idempotency_key` + 相同请求摘要 → 返回既有任务；
  同一 key 但摘要不同 → 409 `IDEMPOTENCY_CONFLICT`。
- **估算只做本地计算**：用窗口规划给出 token 估算，不调用模型。
- **用量按每次尝试汇总**：`usage_json` 为 NULL 表示提供方没给 usage，单独计数，绝不按 0 计。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..api.errors import ApiError
from ..characters.input_view import IDENTITY_INPUT_VERSION
from ..characters.prompt_catalog import IDENTITY_PROMPT_VERSION
from ..config import Settings
from ..context.budget import DEFAULT_POLICY, BudgetPolicy, estimate_tokens
from ..context.service import plan_range
from ..context.window_builder import ProcessingWindow
from ..domain.enums import (
    CredentialMode,
    JobKind,
    JobPurpose,
    JobState,
    ReadingMode,
)
from ..domain.jobs import JobDetailOut, JobWindowOut
from ..ingest.query import load_canonical_text
from ..storage.cache import fingerprint
from ..storage.models import (
    Annotation,
    Book,
    BookVersion,
    Chapter,
    InferenceRun,
    Job,
    JobWindow,
    ModelProfile,
    Quote,
)
from .request_keys import bind_request_key, find_request_job


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
    windows: list[dict[str, Any]]

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
            "windows": self.windows,
        }


def digest_request(payload: dict[str, Any]) -> str:
    return fingerprint(payload)


def dialogue_strategy_range(strategy: str) -> dict[str, str]:
    if strategy == "legacy":
        return {}
    if strategy not in {
        "complete",
        "complete-review",
        "complete-blocks",
        "complete-blocks-review",
        "complete-blocks-isolated",
        "complete-blocks-isolated-review",
        "complete-blocks-isolated-feedback-review",
    }:
        raise ApiError.validation("不支持的对白处理策略")
    return {
        "context_policy": "context-chapter-3"
        if strategy.startswith("complete-blocks")
        else "context-chapter-1",
        "output_protocol": "expression-production-1",
        **(
            {"auxiliary_protocol": "expression-auxiliary-isolation-1"}
            if "-isolated" in strategy
            else {}
        ),
        **(
            {"review_protocol": "expression-evidence-review-1"}
            if strategy.endswith("-review")
            else {}
        ),
        **(
            {"identity_feedback_protocol": "identity-feedback-1"}
            if strategy == "complete-blocks-isolated-feedback-review"
            else {}
        ),
    }


def estimate_inference(
    session: Session,
    settings: Settings,
    version: BookVersion,
    *,
    start_cp: int = 0,
    end_cp: int | None = None,
    chapter_id: str | None = None,
    output_protocol: str | None = None,
    reading_mode: ReadingMode = ReadingMode.INITIAL,
    visible_horizon_cp: int | None = None,
    policy: BudgetPolicy | None = None,
    output_tokens_per_target: int = 20,
    max_recheck_rounds: int = 0,
    review_protocol: str | None = None,
    auxiliary_protocol: str | None = None,
    identity_feedback_protocol: str | None = None,
) -> JobEstimate:
    """纯本地估算：不调用模型、不写数据库。"""

    resolved_policy = policy or DEFAULT_POLICY
    from ..llm.expression_diagnostics import DIAGNOSTICS_VERSION

    if auxiliary_protocol not in (None, DIAGNOSTICS_VERSION):
        raise ApiError.validation("不支持的辅助诊断版本")
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
    window_inputs, roster_unknown = _estimate_request_inputs(
        session,
        version,
        plan.windows,
        {
            "chapter_id": chapter_id,
            "end_cp": end_cp,
            "reading_mode": reading_mode.value,
            "visible_horizon_cp": visible_horizon_cp,
            "output_protocol": output_protocol,
            "auxiliary_protocol": auxiliary_protocol,
            "identity_input_version": IDENTITY_INPUT_VERSION,
            "identity_prompt_version": IDENTITY_PROMPT_VERSION,
            "nonperson_policy_version": "nonperson-2",
        },
    )
    input_tokens = sum(window_inputs.values())
    output_tokens = targets * output_tokens_per_target
    from ..llm.expression_review import REVIEW_VERSION

    if review_protocol is not None and review_protocol != REVIEW_VERSION:
        raise ApiError.validation("不支持的证据复核版本")
    evidence_review = review_protocol == REVIEW_VERSION
    from ..llm.identity_feedback import FEEDBACK_VERSION

    if identity_feedback_protocol is not None and (
        identity_feedback_protocol != FEEDBACK_VERSION
        or not evidence_review
        or max_recheck_rounds < 1
    ):
        raise ApiError.validation("人物名单反馈须配合证据复核并设置至少一轮复核")
    multiplier = 1 + (3 if evidence_review else 1) * max_recheck_rounds + int(
        identity_feedback_protocol is not None
    )
    input_tokens *= multiplier
    output_tokens *= multiplier
    notes = [
        "估算来自本地启发式 token 口径，不是真实计费依据；实际用量以提供方 usage 为准。",
        "输入计入当前可见人物档案、已确认名册和实际请求提示，并与窗口规划预留取较大值。",
        "输出按每条目标对白 20 token 粗估，与任务派发时的安全输出预留口径不同。",
        "后续窗口的场景状态、复核证据、格式纠错、重试和模型推理可增加用量，估算不构成消耗上限。",
    ]
    if roster_unknown:
        notes.append(
            "本章尚无有效的已确认人物名册，当前只计入已知全书人物资料。"
            "人物分析与确认后，新增名册和人物档案会增加每个对白请求的输入，需重新估算。"
        )
    if evidence_review:
        notes.append(
            f"证据复核按首次处理加每轮最多三个阶段（独立复核、分歧裁决、挑战核验）估算：每窗最多{multiplier}个规划阶段。"
            "裁决与核验按需要执行，0轮关闭；格式纠错、限流重试、候选证据提示和模型推理用量未构成上限，实际消耗可高于估算。"
        )
        if identity_feedback_protocol:
            notes.append("已计入每窗最多一次有证据的人物名单反馈；反馈不直接修改人物目录。")
    elif max_recheck_rounds:
        notes.append(
            f"已计入最多 {max_recheck_rounds} 轮全窗口复核；"
            "上下文补全、拆窗及输出重试可增加消耗，实际以调用用量为准。"
        )
    if plan.stats.get("oversized_targets"):
        notes.append(f"其中 {plan.stats['oversized_targets']} 条目标超长，会单独成窗口或保留待定。")
    target_ids = [quote_id for window in plan.windows for quote_id in window.target_quote_ids]
    quote_positions = {
        row.id: (row.start_cp, row.end_cp)
        for row in session.execute(select(Quote).where(Quote.id.in_(target_ids or [""]))).scalars()
    }
    canonical = load_canonical_text(settings, version)
    # One bounded result per planned window; never fetch the entire task history.
    processed_ids = set(
        session.scalars(
            select(Annotation.quote_id).where(Annotation.quote_id.in_(target_ids or [""]))
        )
    )
    latest_windows = (
        select(
            JobWindow.window_id,
            JobWindow.state,
            Job.last_error,
            func.row_number()
            .over(
                partition_by=JobWindow.window_id,
                order_by=(Job.created_at.desc(), Job.id.desc()),
            )
            .label("rank"),
        )
        .join(Job, Job.id == JobWindow.job_id)
        .where(
            Job.book_version_id == version.id,
            JobWindow.window_id.in_([window.window_id for window in plan.windows] or [""]),
        )
        .subquery()
    )
    last_attempts = {
        row.window_id: row
        for row in session.execute(select(latest_windows).where(latest_windows.c.rank == 1))
    }
    windows: list[dict[str, Any]] = []
    for index, window in enumerate(plan.windows):
        positions = [
            quote_positions[item] for item in window.target_quote_ids if item in quote_positions
        ]
        window_start = min((item[0] for item in positions), default=start_cp)
        window_end = max((item[1] for item in positions), default=window_start)
        processed_count = sum(item in processed_ids for item in window.target_quote_ids)
        last_attempt = last_attempts.get(window.window_id)
        completed = processed_count == len(window.target_quote_ids)
        failed = last_attempt is not None and last_attempt.state == JobState.FAILED
        windows.append(
            {
                "window_id": window.window_id,
                "ordinal": index + 1,
                "start_cp": window_start,
                "end_cp": window_end,
                "target_count": len(window.target_quote_ids),
                "estimated_tokens": (
                    window_inputs[window.window_id]
                    + len(window.target_quote_ids) * output_tokens_per_target
                )
                * multiplier,
                "preview": canonical[window_start : min(window_end, window_start + 160)].strip(),
                "processing_status": (
                    "completed" if completed else "failed" if failed else "unprocessed"
                ),
                "processed_target_count": processed_count,
                "last_error": last_attempt.last_error if failed and not completed else None,
            }
        )
    return JobEstimate(
        window_count=len(plan.windows),
        target_count=targets,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        estimator=plan.windows[0].budget["estimator"] if plan.windows else {},
        policy={
            **resolved_policy.as_key(),
            **({"auxiliary_protocol": auxiliary_protocol} if auxiliary_protocol else {}),
            **({"review_protocol": review_protocol} if review_protocol is not None else {}),
            **(
                {"identity_feedback_protocol": identity_feedback_protocol}
                if identity_feedback_protocol is not None else {}
            ),
        },
        notes=notes,
        windows=windows,
    )


def _estimate_request_inputs(
    session: Session,
    version: BookVersion,
    windows: Sequence[ProcessingWindow],
    request_range: dict[str, Any],
) -> tuple[dict[str, int], bool]:
    """Read current identities and reuse dispatch assembly without creating a task."""
    from ..characters.service import confirmed_roster_context
    from ..domain.enums import CharacterRosterStatus
    from ..scenes.state import SceneState
    from .scheduler import _apply_confirmed_roster, _request_payload_for

    chapter_id = request_range.get("chapter_id")
    roster_unknown = False
    if chapter_id:
        chapter = session.get(Chapter, chapter_id)
        if chapter is None or chapter.book_version_id != version.id:
            raise ApiError.validation("章节不存在或不属于该书籍版本", chapter_id=chapter_id)
        roster, characters = confirmed_roster_context(session, version, chapter_id)
        roster_unknown = (
            roster is None
            or roster.status is not CharacterRosterStatus.CONFIRMED
            or not characters
            or roster.pov_character_id not in {person.id for person in characters}
        )
        if roster_unknown:
            request_range = {**request_range, "chapter_id": None}
    # This object is never added to the session. Assembly only reads range_json
    # and storage identities, then mutates the fresh in-memory SceneState.
    request = Job(range_json=json.dumps(request_range, ensure_ascii=False))
    inputs: dict[str, int] = {}
    for window in windows:
        state = SceneState()
        ready, error = _apply_confirmed_roster(session, request, version, state, window)
        if not ready:
            raise ApiError.validation(error or "人物资料无法安全读取")
        payload = _request_payload_for(window=window, state=state)
        prompt_tokens = sum(estimate_tokens(message["content"]) for message in payload["messages"])
        inputs[window.window_id] = max(int(window.budget["total_tokens"]), prompt_tokens)
    return inputs, roster_unknown


def profile_snapshot(
    profile: ModelProfile | None, inference_options: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """任务保存的配置快照：只有非敏感字段 + 提示/协议版本。**不含任何密钥**。"""

    if profile is None:
        return None
    params = json.loads(profile.params_json or "{}")
    options = inference_options or {}
    mode = options.get("thinking_mode", "default")
    if mode != "default":
        current = params.get("thinking")
        params["thinking"] = {**(current if isinstance(current, dict) else {}), "type": mode}
    effective_thinking = params.get("thinking")
    if isinstance(effective_thinking, dict) and effective_thinking.get("type") == "disabled":
        params.pop("reasoning_effort", None)
    elif options.get("reasoning_effort", "default") != "default":
        params["reasoning_effort"] = options["reasoning_effort"]
    return {
        "profile_id": profile.id,
        "name": profile.name,
        "protocol": profile.protocol,
        "base_url": profile.base_url,
        "model": profile.model,
        "params": params,
        "inference_options": options,
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
    inference_options: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> tuple[Job, bool]:
    """幂等创建任务；返回 ``(job, created)``。"""

    if budget.get("max_recheck_rounds") is None:
        budget = {key: value for key, value in budget.items() if key != "max_recheck_rounds"}
    from ..llm.expression_task import PRODUCTION_EXPRESSION_VERSION

    protocol = range_payload.get("output_protocol")
    if protocol is not None and protocol != PRODUCTION_EXPRESSION_VERSION:
        raise ApiError.validation("不支持的对白输出协议")
    from ..llm.expression_diagnostics import DIAGNOSTICS_VERSION

    auxiliary_protocol = range_payload.get("auxiliary_protocol")
    if auxiliary_protocol is not None and (
        auxiliary_protocol != DIAGNOSTICS_VERSION or protocol != PRODUCTION_EXPRESSION_VERSION
    ):
        raise ApiError.validation("辅助诊断隔离须选择受支持的版本与短表达协议")
    from ..context.budget import (
        CONTEXT_POLICY_BOUNDED_BLOCKS,
        CONTEXT_POLICY_CHAPTER,
        CONTEXT_POLICY_DIALOGUE_BLOCKS,
    )

    if (
        range_payload.get("context_policy")
        in {CONTEXT_POLICY_CHAPTER, CONTEXT_POLICY_DIALOGUE_BLOCKS, CONTEXT_POLICY_BOUNDED_BLOCKS}
        and protocol != PRODUCTION_EXPRESSION_VERSION
    ):
        raise ApiError.validation("完整章节上下文策略须配合短表达协议")
    from ..llm.expression_review import REVIEW_VERSION

    review_protocol = range_payload.get("review_protocol")
    if review_protocol is not None and (
        review_protocol != REVIEW_VERSION or protocol != PRODUCTION_EXPRESSION_VERSION
    ):
        raise ApiError.validation("证据裁决复核必须显式选择短表达协议及受支持的复核版本")
    from ..llm.identity_feedback import FEEDBACK_VERSION

    identity_feedback_protocol = range_payload.get("identity_feedback_protocol")
    if identity_feedback_protocol is not None and (
        identity_feedback_protocol != FEEDBACK_VERSION
        or review_protocol != REVIEW_VERSION
        or int(budget.get("max_recheck_rounds") or 0) < 1
    ):
        raise ApiError.validation("人物名单反馈须配合短表达证据复核并设置至少一轮复核")
    snapshot = profile_snapshot(profile, inference_options)
    request_payload = {
        "kind": kind.value,
        "book_id": book.id,
        "book_version_id": version.id,
        "profile_id": profile.id if profile else None,
        "range": range_payload,
        "budget": budget,
        "reading_mode": reading_mode.value,
        "visible_horizon_cp": visible_horizon_cp,
        "inference_options": inference_options or {},
    }
    digest = digest_request(request_payload)

    existing = find_request_job(session, idempotency_key, digest)
    if existing is not None:
        return existing, False

    # Different UI events may arrive before React has disabled the button, or
    # from two tabs. Reuse an equivalent active job even when each event has a
    # fresh idempotency key; otherwise both jobs race to overwrite the same
    # annotation projection.
    active_duplicate = (
        session.execute(
            select(Job)
            .where(
                Job.request_digest == digest,
                Job.state.in_((JobState.QUEUED, JobState.RUNNING, JobState.PAUSING)),
            )
            .order_by(Job.created_at.desc())
        )
        .scalars()
        .first()
    )
    if active_duplicate is not None:
        bind_request_key(session, active_duplicate, idempotency_key, digest)
        return active_duplicate, False

    from .occupancy import guard_active_target

    guard_active_target(session, version.id, kind, {
        **range_payload,
        "reading_mode": reading_mode.value,
        "visible_horizon_cp": visible_horizon_cp,
    }, settings=settings)

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
                "identity_input_version": IDENTITY_INPUT_VERSION,
                "identity_prompt_version": IDENTITY_PROMPT_VERSION,
                "nonperson_policy_version": "nonperson-2",
            },
            ensure_ascii=False,
        ),
        profile_snapshot_json=json.dumps(snapshot, ensure_ascii=False) if profile else None,
        budget_json=json.dumps(budget, ensure_ascii=False),
        progress_json=json.dumps({"stage": "queued", "windows_done": 0}, ensure_ascii=False),
        idempotency_key=idempotency_key,
        request_digest=digest,
    )
    session.add(job)
    session.flush()
    bind_request_key(session, job, idempotency_key, digest)
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
    runs = list(
        session.execute(
            select(
                InferenceRun.id,
                InferenceRun.created_at,
                InferenceRun.window_id,
                InferenceRun.usage_json,
                InferenceRun.state,
                InferenceRun.error_code,
            ).where(InferenceRun.job_id == job.id)
            .order_by(InferenceRun.created_at, InferenceRun.id)
        )
    )
    return _job_detail(job, rows, runs)


def job_details(session: Session, jobs: Sequence[Job]) -> list[JobDetailOut]:
    """Read complete details for a bounded task list with two batched queries."""

    if not jobs:
        return []
    job_ids = [job.id for job in jobs]
    windows_by_job: dict[str, list[JobWindow]] = {}
    for row in session.scalars(
        select(JobWindow)
        .where(JobWindow.job_id.in_(job_ids))
        .order_by(JobWindow.job_id, JobWindow.window_id)
    ):
        windows_by_job.setdefault(row.job_id, []).append(row)
    runs_by_job: dict[str, list[Any]] = {}
    for run in session.execute(
        select(
            InferenceRun.id,
            InferenceRun.created_at,
            InferenceRun.job_id,
            InferenceRun.window_id,
            InferenceRun.usage_json,
            InferenceRun.state,
            InferenceRun.error_code,
        ).where(InferenceRun.job_id.in_(job_ids))
        .order_by(InferenceRun.created_at, InferenceRun.id)
    ):
        runs_by_job.setdefault(run.job_id, []).append(run)
    return [
        _job_detail(job, windows_by_job.get(job.id, []), runs_by_job.get(job.id, []))
        for job in jobs
    ]


def _job_detail(job: Job, rows: list[JobWindow], runs: list[Any]) -> JobDetailOut:
    attempts_by_window: dict[str, int] = {}
    for run in runs:
        if run.window_id:
            attempts_by_window[run.window_id] = attempts_by_window.get(run.window_id, 0) + 1
    windows = [window_out(row, attempts_by_window.get(row.window_id, 0)) for row in rows]
    usage = _spent_tokens(runs)
    unknown_runs = sum(1 for run in runs if run.usage_json is None)
    progress = json.loads(job.progress_json) if job.progress_json else None
    from .local_finalization import settlement_progress
    progress = settlement_progress(job.id, progress)
    # Primary, format retries and review stages share their window_id. Only the
    # latest attempt for each window can leave an unresolved service failure.
    # A COMPLETED job can retain its valid primary result after review fails.
    latest_by_window = {run.window_id: run for run in runs}
    service_failure = next(
        (
            run for run in reversed(runs)
            if latest_by_window[run.window_id] is run
            and run.state.value == "FAILED"
            and run.error_code in {"RATE_LIMITED", "PROVIDER_QUOTA_EXHAUSTED"}
        ),
        None,
    )
    return JobDetailOut(
        range=json.loads(job.range_json or "{}"),
        id=job.id,
        kind=job.kind,
        purpose=job.purpose,
        state=job.state,
        book_id=job.book_id,
        book_version_id=job.book_version_id,
        progress=progress,
        checkpoint=json.loads(job.checkpoint_json) if job.checkpoint_json else None,
        last_error=job.last_error,
        service_error_code=service_failure.error_code if service_failure else None,
        service_error_run_id=service_failure.id if service_failure else None,
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
            select(
                InferenceRun.state,
                InferenceRun.usage_json,
                InferenceRun.profile_snapshot_json,
                Job.profile_snapshot_json.label("job_profile_snapshot_json"),
            )
            .join(Job, InferenceRun.job_id == Job.id, isouter=True)
            .where(Job.book_id == book_id)
        )
    )
    input_tokens = output_tokens = total_tokens = 0
    unknown_runs = 0
    by_state: dict[str, int] = {}
    by_model: dict[str, int] = {}
    for run in rows:
        by_state[run.state.value] = by_state.get(run.state.value, 0) + 1
        # 按**每次尝试自己的**配置快照归属模型（成本路由会为个别窗口换模型，
        # 只看任务级快照会把强模型用量算到基础模型头上）。
        snapshot_raw = run.profile_snapshot_json or (run.job_profile_snapshot_json)
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
            usage.get(key) is None for key in ("input_tokens", "output_tokens", "total_tokens")
        ):
            unknown_runs += 1
            continue
        input_tokens += int(usage.get("input_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or 0)
        total_tokens += int(
            usage.get("total_tokens")
            if usage.get("total_tokens") is not None
            else (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
        )
    return {
        "book_id": book_id,
        "runs": len(rows),
        "unknown_usage_runs": unknown_runs,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "by_state": by_state,
        "by_model": by_model,
    }


def spent_tokens(session: Session, job_id: str) -> dict[str, int]:
    """已结算的 token（未知用量按预留口径计入 ``unknown_runs``）。"""

    rows = list(
        session.execute(
            select(InferenceRun.usage_json, InferenceRun.state, InferenceRun.error_code).where(
                InferenceRun.job_id == job_id
            )
        )
    )
    return _spent_tokens(rows)


def _spent_tokens(rows) -> dict[str, int]:  # noqa: ANN001
    input_tokens = output_tokens = total_tokens = 0
    unknown_runs = 0
    for run in rows:
        if not run.usage_json:
            # INVALID_MODEL_OUTPUT 表示提供方通常已经生成过内容；缺 usage 不能按零费用处理。
            # 历史 UNAVAILABLE 也可能是已发送后的响应中断；只有独立的
            # PROVIDER_CONNECTION_FAILED 能证明尚未发送，且不伪造零 usage。
            if run.state.value in {"DISPATCHED", "UNKNOWN_OUTCOME", "SUCCEEDED"} or (
                run.state.value == "FAILED" and run.error_code in {
                    "INVALID_MODEL_OUTPUT", "LOCAL_FINALIZATION_FAILED", "PROVIDER_UNAVAILABLE",
                }
            ):
                unknown_runs += 1
            continue
        try:
            usage = json.loads(run.usage_json)
        except json.JSONDecodeError:
            unknown_runs += 1
            continue
        if usage.get("unknown") or all(
            usage.get(key) is None for key in ("input_tokens", "output_tokens", "total_tokens")
        ):
            unknown_runs += 1
            continue
        input_tokens += int(usage.get("input_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or 0)
        total_tokens += int(
            usage.get("total_tokens")
            if usage.get("total_tokens") is not None
            else (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
        )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
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
