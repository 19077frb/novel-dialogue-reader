"""单进程调度器。

- **每个窗口一次有效提交**：按文档顺序执行，完成即写检查点；再次运行跳过已完成窗口。
- **网络调用与数据库事务分离**：调用前用短事务写 PREPARED → DISPATCHED 并提交；
  适配器调用期间不持有事务；返回后用另一个事务应用结果并结算用量。
- **未知结果不自动重发**：DISPATCHED 却没有落库结果的尝试（进程中断、超时）标为
  `UNKNOWN_OUTCOME`，窗口与任务进入 `NEEDS_RECONCILIATION`，等用户显式决定。
- **缓存命中不调用模型**：同语义输入（含 horizon/阅读模式/提示版本/策略版本）直接复用。
- **预算**：调用前按估算预留、调用后按 usage 结算；未知用量按预留口径计入并单独标记。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Bundle, Session, sessionmaker

from ..characters.input_view import IDENTITY_INPUT_VERSION, project_identity_state
from ..characters.service import confirmed_roster_context, list_book_characters
from ..config import Settings
from ..context.budget import DEFAULT_POLICY, BudgetPolicy, estimate_tokens, policy_for_version
from ..context.recheck import plan_recheck, route_window
from ..context.service import load_window_inputs, plan_range
from ..context.window_builder import plan_windows
from ..domain.enums import (
    AnnotationSource,
    AnnotationStatus,
    CredentialMode,
    InferenceRunState,
    JobKind,
    JobState,
    ReadingMode,
    SpeakerBasis,
)
from ..llm.adapter import FAKE_PROVIDER_PROTOCOL, PROTOCOL_CAPABILITIES, ProviderAdapter
from ..llm.adapters import AdapterSpec, build_adapter
from ..llm.errors import ProviderError, ProviderErrorKind
from ..llm.expression_task import (
    PRODUCTION_CACHE_VERSION,
    PRODUCTION_EXPRESSION_VERSION,
    build_production_expression_task,
)
from ..llm.prompts import LABELING_PROMPT_VERSION
from ..llm.prompts.labeling import DATA_DELIMITER, escape_data_markers
from ..llm.validation import parse_and_validate
from ..scenes.acceptance import ACCEPTANCE_POLICY_VERSION
from ..scenes.engine import apply_window
from ..scenes.runner import _messages_for, _restore_output_references, _targets_for
from ..scenes.state import ConfirmedCharacter, SceneState, SpeakerSlot
from ..storage.cache import (
    CACHE_SCHEMA_VERSION,
    CacheKeyParts,
    ResultCacheStore,
    compute_cache_key,
    fingerprint,
)
from ..storage.chapter_status import complete_chapter_automatically
from ..storage.models import (
    Annotation,
    BookVersion,
    Chapter,
    InferenceRun,
    Job,
    JobWindow,
    ModelProfile,
    Quote,
    Scene,
    SpeakerGroup,
)
from .expression_pipeline import (
    ReviewedExpression,
    cached_proposal,
    restore_primary,
    run_review_pipeline,
    selected,
)
from .roster import run_character_roster_job
from .service import (
    credential_mode_of,
    credential_reference,
    profile_for_job,
    profile_snapshot,
    spent_tokens,
)

SCHEDULER_VERSION = "scheduler-1"

# 只有「确定没有被处理」的错误才允许自动重试；超时属于结果未知，必须人工对账。
AUTO_RETRY_KINDS = {ProviderErrorKind.RATE_LIMITED, ProviderErrorKind.UNAVAILABLE}
DEFAULT_LEASE_SECONDS = 900
LABELING_MAX_TOKENS = 800
OUTPUT_BASE_TOKENS = 256
OUTPUT_TOKENS_PER_TARGET = 128


def _json_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _output_token_reserve(snapshot: dict[str, Any] | None, target_count: int) -> int:
    """按契约规模预留输出，并受配置的 max_tokens 上限约束。"""

    params = (snapshot or {}).get("params", {}) or {}
    try:
        requested_max = max(0, int(params.get("max_tokens", LABELING_MAX_TOKENS)))
    except (TypeError, ValueError):
        requested_max = LABELING_MAX_TOKENS
    structural_estimate = OUTPUT_BASE_TOKENS + OUTPUT_TOKENS_PER_TARGET * target_count
    return min(requested_max, structural_estimate)


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
    # 成本路由与局部复核的计数（默认都是 0；不是估计值）
    strong_windows: int = 0
    recheck_windows: int = 0
    recheck_targets: int = 0
    recheck_calls: int = 0
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
            "strong_windows": self.strong_windows,
            "recheck_windows": self.recheck_windows,
            "recheck_targets": self.recheck_targets,
            "recheck_calls": self.recheck_calls,
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


def _format_retry_limit(job: Job) -> int:
    return min(5, max(0, int(_budget_of(job).get("max_format_retries", 1))))


def _range_of(job: Job) -> dict[str, Any]:
    return _json_of(job.range_json)


def _reading_mode_of(job: Job) -> ReadingMode:
    try:
        return ReadingMode(_range_of(job).get("reading_mode", ReadingMode.INITIAL.value))
    except ValueError:
        return ReadingMode.INITIAL


def _load_state(job: Job) -> SceneState:
    return SceneState.from_snapshot(_json_of(job.checkpoint_json).get("scene_state"))


def _apply_confirmed_roster(
    session: Session,
    job: Job,
    version: BookVersion,
    state: SceneState,
    window=None,
) -> tuple[bool, str | None]:
    """把用户确认的人物与 POV 注入场景状态；返回是否可继续。"""

    chapter_id = _range_of(job).get("chapter_id")
    if not chapter_id:
        return _prepare_identity_view(session, job, version, state, window)
    roster, characters = confirmed_roster_context(session, version, str(chapter_id))
    if roster is None:
        return False, "请先分析并确认本章人物，再选择本章主人公"
    if roster.status.value != "CONFIRMED":
        return False, "请先完成本章人物确认并选择本章主人公"
    if not characters:
        return False, "本章已确认人物为空，请重新确认"
    state.confirmed_characters = [
        ConfirmedCharacter(
            character_id=character.id,
            canonical_name=character.canonical_name or "",
            aliases=tuple(_json_list(character.aliases_json)),
            description=character.description or "",
            source=character.source.value,
            user_confirmed=character.user_confirmed,
            confirmation_source=character.confirmation_source or "unknown",
        )
        for character in characters
    ]
    people = list_book_characters(session, version)
    state.book_characters = [
        ConfirmedCharacter(
            character_id=character.id,
            canonical_name=character.canonical_name or "",
            aliases=tuple(_json_list(character.aliases_json)),
            description=character.description or "",
            source=character.source.value,
            user_confirmed=character.user_confirmed,
            confirmation_source=character.confirmation_source or "unknown",
        )
        for character in people
    ]
    state.pov_character_id = roster.pov_character_id
    if _range_of(job).get("identity_input_version") is None:
        state.sync_confirmed_participants()
    if state.pov_character_id not in {item.character_id for item in state.confirmed_characters}:
        return False, "本章主人公不在已确认人物中，请重新确认"
    return _prepare_identity_view(session, job, version, state, window, people=people)


def _prepare_identity_view(session, job, version, state, window=None, *, people=None):
    requested = _range_of(job).get("identity_input_version")
    if requested is None:
        return (
            (False, "短协议任务缺少冻结的人物输入版本")
            if _range_of(job).get("output_protocol")
            else (True, None)
        )
    if requested != IDENTITY_INPUT_VERSION:
        return False, "人物输入版本不受支持，不能继续旧任务"
    mode = _reading_mode_of(job)
    horizon = version.canonical_length_cp
    if mode is ReadingMode.INITIAL:
        horizon = (
            max((f.end_cp for f in window.fragments), default=0)
            if window
            else int(_range_of(job).get("end_cp") or version.canonical_length_cp)
        )
        explicit = _range_of(job).get("visible_horizon_cp")
        if explicit is not None:
            horizon = min(horizon, int(explicit))
    try:
        project_identity_state(
            state,
            people if people is not None else list_book_characters(session, version),
            version,
            reading_mode=mode,
            horizon=horizon,
        )
        state.sync_confirmed_participants()
        output_protocol = _range_of(job).get("output_protocol")
        if output_protocol is not None and output_protocol != PRODUCTION_EXPRESSION_VERSION:
            raise ValueError("不支持的对白输出协议")
        if output_protocol and window is not None:
            state.production_expression_task = build_production_expression_task(window, state)
    except (ValueError, TypeError, KeyError) as exc:
        return False, f"人物资料无法安全读取：{exc}"
    return True, None


def policy_for_job(job: Job) -> BudgetPolicy:
    """任务范围的 ``context_policy`` 选择版本化的上下文策略；缺省一律保守的 ``context-1``。"""

    return policy_for_version(_range_of(job).get("context_policy"))


def _escalated_max_tokens(snapshot: dict[str, Any] | None, error: ProviderError) -> int | None:
    """输出被截断（finish_reason=length）时，重试把 max_tokens 翻倍（上限 128k）。"""

    details = getattr(error, "details", None)
    if not isinstance(details, dict) or details.get("finish_reason") != "length":
        return None
    params = (snapshot or {}).get("params") or {}
    try:
        current = int(params.get("max_tokens") or LABELING_MAX_TOKENS)
    except (TypeError, ValueError):
        current = LABELING_MAX_TOKENS
    return min(max(current * 2, 32000), 128000)


def _strong_profile(session: Session, job: Job) -> ModelProfile | None:
    """成本路由用的强模型配置（任务范围里的 ``strong_profile_id``）；没有就不路由。"""

    profile_id = _range_of(job).get("strong_profile_id")
    if not profile_id:
        return None
    return session.get(ModelProfile, str(profile_id))


def _unresolved_targets(session: Session, window) -> list[str]:  # noqa: ANN001
    """Return unresolved and low-confidence targets in window order."""

    quote_ids = list(window.target_quote_ids)
    if not quote_ids:
        return []
    rows = session.execute(
        select(Annotation).where(
            Annotation.quote_id.in_(quote_ids),
            Annotation.status.in_((AnnotationStatus.UNKNOWN, AnnotationStatus.PROVISIONAL)),
            Annotation.user_locked.is_(False),
        )
    ).scalars()
    unresolved = {row.quote_id for row in rows}
    return [quote_id for quote_id in quote_ids if quote_id in unresolved]


def _plan(
    session: Session,
    settings: Settings,
    job: Job,
    version: BookVersion,
    policy: BudgetPolicy,
):  # noqa: ANN202
    payload = _range_of(job)
    plan = plan_range(
        session,
        settings,
        version,
        start_cp=int(payload.get("start_cp", 0) or 0),
        end_cp=payload.get("end_cp"),
        reading_mode=_reading_mode_of(job),
        visible_horizon_cp=payload.get("visible_horizon_cp"),
        policy=policy,
    )
    selected = payload.get("selected_window_ids")
    if isinstance(selected, list):
        selected_ids = {str(item) for item in selected}
        plan = replace(
            plan,
            windows=tuple(window for window in plan.windows if window.window_id in selected_ids),
        )
    return plan


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
    *,
    job: Job,
    version: BookVersion,
    window,  # noqa: ANN001
    snapshot: dict[str, Any] | None,
    state: SceneState,
    prompt_hint: str | None = None,
    review_rounds: int | None = None,
) -> str:
    snapshot = snapshot or {}
    # 缓存必须覆盖实际发给模型的动态人物状态，而不只是预先规划的正文片段。
    messages = _request_payload_for(window=window, state=state, correction=prompt_hint)["messages"]
    expression = state.production_expression_task
    return compute_cache_key(
        CacheKeyParts(
            book_version_id=version.id,
            target_ids=list(window.target_quote_ids),
            input_fingerprint=fingerprint(
                {
                    "messages": messages,
                    "task": expression.fingerprint(),
                    **(
                        {
                            "review_protocol": _range_of(job).get("review_protocol"),
                            "review_rounds": review_rounds
                            if review_rounds is not None
                            else _budget_of(job).get("max_recheck_rounds"),
                        }
                        if selected(job)
                        else {}
                    ),
                }
            )
            if expression
            else fingerprint(messages),
            model=str(snapshot.get("model", "")),
            params=snapshot.get("params", {}) or {},
            protocol_version=(
                f"{snapshot.get('protocol', '')}:{snapshot.get('base_url', '')}"
                + (
                    ":" + str(_range_of(job)["identity_input_version"])
                    if "identity_input_version" in _range_of(job)
                    else ""
                )
            ),
            prompt_version=PRODUCTION_EXPRESSION_VERSION if expression else LABELING_PROMPT_VERSION,
            schema_version="1.1" if expression else "1.0",
            policy_version=window.policy_version,
            dependency_hash=window.dependency_hash,
            reading_mode=_reading_mode_of(job).value,
            visible_horizon_cp=window.visible_horizon_cp,
            acceptance_policy_version=ACCEPTANCE_POLICY_VERSION,
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
    preserve_existing_candidates: bool = False,
    attribution_only: bool = False,
) -> tuple[bool, list[str], list[str], list[str]]:
    """校验并应用一次输出；成功时写缓存。

    返回 ``(ok, validation_codes, validation_messages, repair_warnings)``；
    ``repair_warnings`` 记录「程序补齐的模型遗漏」（例如只写 NEW 却没声明 temp_ref）。
    """

    reference_repairs = (
        [str(item) for item in raw.get("_reference_repairs", [])]
        if isinstance(raw, dict) and isinstance(raw.get("_reference_repairs"), list)
        else []
    )
    payload = (
        {key: value for key, value in raw.items() if not str(key).startswith("_")}
        if isinstance(raw, dict)
        else raw
    )
    compilation = None
    cache_payload = payload
    expected_version = "1.0"
    if state.production_expression_task is not None:
        from pydantic import ValidationError

        from ..llm.errors import InvalidModelOutput
        from ..llm.expression_compiler import compile_expression_output
        from ..scenes.runner import _validate_expression_task

        try:
            _validate_expression_task(state.production_expression_task, window, state, None)
        except ValueError as exc:
            return False, ["invalid_expression_input"], [str(exc)], []
        try:
            if (
                isinstance(raw, ReviewedExpression)
                and raw.task_fingerprint != state.production_expression_task.fingerprint()
            ):
                raise ValueError("Resolved review does not match current task")
            compilation = compile_expression_output(
                payload,
                state.production_expression_task,
                owner_approvals=raw.owner_approvals
                if isinstance(raw, ReviewedExpression)
                else None,
            )
            if isinstance(raw, ReviewedExpression):
                cache_payload = raw.cache_payload()
        except (ValueError, InvalidModelOutput, ValidationError) as exc:
            return False, ["invalid_expression_output"], [str(exc)], []
        payload = compilation.output
        if (
            attribution_only
            and not payload.scene_updates
            and all(d.decision.value == "CONTINUE" for d in payload.gap_decisions)
        ):
            payload = payload.model_copy(update={"gap_decisions": []})
        expected_version = "1.1"
    report = parse_and_validate(
        payload, _targets_for(window, state), expected_schema_version=expected_version
    )
    if not report.ok or report.output is None:
        return False, report.error_codes, report.messages, list(report.warnings)
    if attribution_only and (
        report.output.scene_updates
        or report.output.gap_decisions
        or report.output.identity_proposals
    ):
        return (
            False,
            ["review_structure_changed"],
            ["复核仅修改对白归属与证据，不切分场景或合并拆分人物"],
            [],
        )
    quote_positions, gap_positions, evidence_positions = _positions(inputs, window)
    # 人工确认过的对白（user_locked）不能被模型结果覆盖，也不能被自动 merge/split。
    locked_quote_ids = {
        row.quote_id
        for row in session.execute(
            select(Annotation).where(
                Annotation.quote_id.in_(list(window.target_quote_ids) or [""]),
                Annotation.user_locked.is_(True),
            )
        ).scalars()
    }
    locked_group_ids: set[str] = set()
    if state.scene_id:
        locked_group_ids = {
            row.speaker_id
            for row in session.execute(
                select(Annotation).where(
                    Annotation.scene_id == state.scene_id,
                    Annotation.user_locked.is_(True),
                    Annotation.speaker_id.is_not(None),
                )
            ).scalars()
            if row.speaker_id
        }
    result = apply_window(
        session,
        book_version_id=inputs.book_version_id,
        window=window,
        output=report.output,
        state=state,
        quote_positions=quote_positions,
        gap_positions=gap_positions,
        evidence_positions=evidence_positions,
        locked_quote_ids=locked_quote_ids,
        locked_group_ids=locked_group_ids,
        dependency_hash=window.dependency_hash,
        source=AnnotationSource.MODEL,
        run_id=run_id,
        preserve_existing_candidates=preserve_existing_candidates,
        expected_schema_version=expected_version,
        acceptance_ceilings=compilation.acceptance_ceilings if compilation else None,
    )
    if not result.validation_ok:
        return False, result.validation_codes, result.warnings, list(report.warnings)
    ResultCacheStore(session).put(
        cache_key=cache_key,
        result_json=json.dumps(
            cache_payload if compilation else report.output.model_dump(mode="json"),
            ensure_ascii=False,
        ),
        schema_version=(
            "expr-review-1" if isinstance(raw, ReviewedExpression) else PRODUCTION_CACHE_VERSION
        )
        if compilation
        else CACHE_SCHEMA_VERSION,
        dependency_hash=window.dependency_hash,
        created_run_id=run_id,
    )
    return True, [], [], [*reference_repairs, *report.warnings]


def _review_state(session: Session, job: Job, window) -> SceneState:  # noqa: ANN001
    state = _load_state(job)
    annotation = session.scalar(
        select(Annotation).where(Annotation.quote_id == window.target_quote_ids[0])
    )
    scene = session.get(Scene, annotation.scene_id) if annotation and annotation.scene_id else None
    if scene is None:
        raise ValueError("复核对白缺少既有场景")
    state.scene_id, state.scene_ref, state.start_cp = scene.id, window.scene_ref, scene.start_cp
    state.last_quote_id = state.last_speaker_ref = None
    state.recent_turns = []
    state.participants = [
        SpeakerSlot(
            display_label=group.display_label,
            first_quote_id=group.first_quote_id or "",
            group_id=group.id,
            character_id=group.character_id,
            canonical_name=group.canonical_name or "",
            description=group.description or "",
        )
        for group in session.scalars(
            select(SpeakerGroup)
            .where(SpeakerGroup.scene_id == scene.id)
            .order_by(SpeakerGroup.display_label)
        )
    ]
    return state


def _run_recheck(
    session_factory: sessionmaker[Session],
    settings: Settings,
    **kwargs,
) -> dict[str, int]:
    policy = kwargs["policy"]
    if policy.recheck_max_rounds <= 0:
        return _run_recheck_pass(session_factory, settings, **kwargs)
    stats = {
        "windows": 0,
        "targets": 0,
        "calls": 0,
        "restored_evidence": 0,
        "unknown_outcome": 0,
        "aborted": 0,
    }
    window_id, job_id = kwargs["window"].window_id, kwargs["job_id"]
    with session_factory() as session:
        job = session.get(Job, job_id)
        checkpoint = _json_of(job.checkpoint_json)
        if window_id in checkpoint.get("review_stopped", {}):
            return stats
        rounds = checkpoint.get("review_rounds", {}).get(window_id, 0)
    for index in range(rounds, policy.recheck_max_rounds):
        with session_factory() as session:
            job = session.get(Job, job_id)
            if job.state in STOP_STATES or job.state is JobState.PAUSING:
                return stats
            job.progress_json = json.dumps(
                {
                    "stage": "rechecking",
                    "window_id": window_id,
                    "review_round": index + 1,
                    "review_rounds": policy.recheck_max_rounds,
                }
            )
            session.commit()
        current = _run_recheck_pass(
            session_factory, settings, **kwargs, full_window=True, round_index=index + 1
        )
        for key in stats:
            stats[key] += current[key]
        if current["unknown_outcome"]:
            return stats
        with session_factory() as session:
            job = session.get(Job, job_id)
            if job.state in STOP_STATES or (
                job.state is JobState.PAUSING and not current.get("round_completed")
            ):
                return stats
            checkpoint = _json_of(job.checkpoint_json)
            checkpoint.setdefault("review_rounds", {})[window_id] = (
                index + 1 if current.get("round_completed") else index
            )
            if current.get("aborted"):
                checkpoint.setdefault("review_stopped", {})[window_id] = current.get(
                    "stop_reason", "复核提前结束，已保留首次有效结果。"
                )
            job.checkpoint_json = json.dumps(checkpoint, ensure_ascii=False)
            session.commit()
        if current.get("aborted") or not current["windows"]:
            break
    return stats


def _run_recheck_pass(
    session_factory: sessionmaker[Session],
    settings: Settings,
    *,
    job_id: str,
    version: BookVersion,
    window,  # noqa: ANN001 - ProcessingWindow
    adapter: ProviderAdapter | None,
    snapshot: dict[str, Any] | None,
    policy: BudgetPolicy,
    reading_mode: ReadingMode,
    horizon_cp: int | None,
    full_window: bool = False,
    round_index: int = 0,
) -> dict[str, Any]:
    """有限局部复核：只复核未解决的目标，并用保守策略把压缩丢掉的行文补回。

    复核是**额外**一次尝试：单独写 `inference_runs` 并计入用量（不是只统计最后一次调用）。
    复核失败不改变首次结果；只有超时（结果未知、可能已计费）才升级为人工对账。
    """

    stats = {
        "windows": 0,
        "targets": 0,
        "calls": 0,
        "restored_evidence": 0,
        "unknown_outcome": 0,
        "aborted": 0,
        "round_completed": 0,
    }
    if adapter is None:
        return stats

    with session_factory() as session:
        query = select(
            Annotation,
            Quote,
            Bundle("identity", SpeakerGroup.canonical_name, SpeakerGroup.character_id),
        )
        query = (
            query.join(Quote, Quote.id == Annotation.quote_id)
            .outerjoin(SpeakerGroup, SpeakerGroup.id == Annotation.speaker_id)
            .where(Quote.id.in_(window.target_quote_ids))
        )
        if not full_window:
            query = query.where(
                Annotation.user_locked.is_(False),
                Annotation.status.in_((AnnotationStatus.UNKNOWN, AnnotationStatus.PROVISIONAL)),
            )
        candidates = session.execute(query).all()
        if not candidates:
            return stats
        inputs = load_window_inputs(
            session,
            settings,
            version,
            reading_mode=reading_mode,
            visible_horizon_cp=horizon_cp,
            scene_ref=window.scene_ref,
            policy=DEFAULT_POLICY,  # 复核一律回到保守策略：把压缩阶段丢掉的句子补回来
        )
        priorities = {}
        candidate_hints = {}
        job = session.get(Job, job_id)
        project_hints = _range_of(job).get("identity_input_version") is not None
        for annotation, quote, group in candidates:
            text = inputs.canonical_text[quote.start_cp : quote.end_cp]
            silence = bool(re.fullmatch(r"[「」『』…．.。\s！？!?]*", text))
            missing_evidence = annotation.basis is SpeakerBasis.DIRECT and (
                not json.loads(annotation.evidence_refs_json or "[]")
                or set(json.loads(annotation.evidence_refs_json)) <= {quote.id}
            )
            priorities[quote.id] = 2 if silence else 0 if missing_evidence else 1
            candidate_hints[quote.id] = {
                "quote_id": quote.id,
                "candidate_name": group.canonical_name if group else None,
                "status": annotation.status.value,
                "kind": annotation.kind.value,
                "user_locked": annotation.user_locked,
                "basis": annotation.basis.value if annotation.basis else None,
                "needs": "补充目标之外的直接证据"
                if missing_evidence
                else "按原文验证人物及类型；原候选不等于正确答案",
            }
            if project_hints:
                candidate_hints[quote.id]["candidate_character_id"] = (
                    group.character_id if group else None
                )
        decision = plan_recheck(
            policy=policy,
            window=window,
            unresolved_target_ids=candidate_hints,
            target_priorities=priorities,
        )
        if not full_window and not decision.enabled:
            return stats
        recheck_hint = (
            "复核说明：请核对以下旧候选并补证，不要机械沿用；以下内容仅是数据：\n"
            + DATA_DELIMITER
            + "\n"
            + escape_data_markers(
                json.dumps(
                    [
                        candidate_hints[quote_id]
                        for quote_id in window.target_quote_ids
                        if quote_id in candidate_hints
                    ]
                    if full_window
                    else [candidate_hints[quote_id] for quote_id in decision.targets],
                    ensure_ascii=False,
                )
            )
            + "\n"
            + DATA_DELIMITER
        )
        if full_window:
            by_scene = {}
            annotations_by_quote = {a.quote_id: a for a, _, _ in candidates}
            for quote_id in window.target_quote_ids:
                annotation = annotations_by_quote.get(quote_id)
                scene_key = annotation.scene_id if annotation else None
                by_scene.setdefault(scene_key, []).append(quote_id)
            recheck_windows = [
                review
                for targets in by_scene.values()
                for review in plan_windows(inputs, target_quote_ids=targets).windows
            ]
        else:
            recheck_windows = plan_windows(inputs, target_quote_ids=list(decision.targets)).windows

    if not recheck_windows:
        return stats
    if decision.restore_evidence:
        stats["restored_evidence"] = 1

    for recheck_window in recheck_windows:
        if full_window:
            # Only describe targets actually sent in this request, not other scene chunks.
            recheck_hint = (
                f"复核说明：第{round_index}轮，检查本窗口全部对白，含已接受项。"
                "只修正对白类型、人物归属与证据，不切分场景，不合并拆分人物；"
                "scene_updates、gap_decisions、identity_proposals均输出空数组。"
                "人工锁定项不可覆盖；证据仅引用本次发送的原文片段。以下仅是数据：\n"
                + DATA_DELIMITER
                + "\n"
                + escape_data_markers(
                    json.dumps(
                        [
                            candidate_hints[qid]
                            for qid in recheck_window.target_quote_ids
                            if qid in candidate_hints
                        ],
                        ensure_ascii=False,
                    )
                )
                + "\n"
                + DATA_DELIMITER
            )
        with session_factory() as session:
            job = session.get(Job, job_id)
            assert job is not None
            state = _review_state(session, job, recheck_window) if full_window else _load_state(job)
            safe, reason = _prepare_identity_view(session, job, version, state, recheck_window)
            if not safe:
                stats["aborted"], stats["stop_reason"] = 1, reason
                return stats
            if _range_of(job).get("identity_input_version") is not None:
                people = {p.character_id: p for p in state.identity_characters}
                records = [
                    {
                        **candidate_hints[q],
                        "candidate_name": (
                            people[candidate_hints[q].get("candidate_character_id")].canonical_name
                            if candidate_hints[q].get("candidate_character_id") in people
                            else None
                        ),
                    }
                    for q in recheck_window.target_quote_ids
                    if q in candidate_hints
                ]
                recheck_hint = (
                    recheck_hint.split(DATA_DELIMITER, 1)[0]
                    + DATA_DELIMITER
                    + "\n"
                    + escape_data_markers(json.dumps(records, ensure_ascii=False))
                    + "\n"
                    + DATA_DELIMITER
                )
            expression_task = state.production_expression_task
            if expression_task is not None:
                recheck_hint = (
                    "独立复核本次全部目标，只根据原文与人物资料判断。"
                    "不提供初次答案；不得切场景，breaks输出空数组。"
                )
            cache_key = _cache_key_for(
                job=job,
                version=version,
                window=recheck_window,
                snapshot=snapshot,
                state=state,
                prompt_hint=recheck_hint,
            )
            cached_result = (
                None
                if _range_of(job).get("force_reprocess")
                else ResultCacheStore(session).get(cache_key)
            )

        retry_limit = _format_retry_limit(job)
        correction = recheck_hint
        max_tokens_override = None
        for attempt in range(1 + retry_limit):
            raw: Any = None
            run_id: str | None = None
            elapsed_ms = 0
            with session_factory() as session:
                job = session.get(Job, job_id)
                assert job is not None
                if job.state in STOP_STATES or job.state is JobState.PAUSING:
                    return stats
                max_input = _budget_of(job).get("max_input_tokens")
                spent = spent_tokens(session, job_id)
                if expression_task is not None and cached_result is None and spent["unknown_runs"]:
                    stats["aborted"] = 1
                    stats["stop_reason"] = "已有调用用量未知，已保留结果并停止额外复核"
                    return stats
                review_request = _request_payload_for(
                    window=recheck_window,
                    state=state,
                    correction=correction,
                )
                reserve = sum(
                    estimate_tokens(message["content"]) for message in review_request["messages"]
                ) + max(
                    _output_token_reserve(snapshot, len(recheck_window.target_quote_ids)),
                    max_tokens_override or 0,
                )
                if (
                    cached_result is None
                    and max_input is not None
                    and (
                        spent["input_tokens"] + (spent["unknown_runs"] + 1) * reserve
                        > int(max_input)
                    )
                ):
                    # Optional recheck must not discard the already accepted first result.
                    stats["aborted"] = 1
                    stats["stop_reason"] = "剩余输入额度不足，已保留结果并停止额外复核。"
                    return stats
                max_output = _budget_of(job).get("max_output_tokens")
                if (
                    cached_result is None
                    and max_output is not None
                    and (
                        spent["output_tokens"]
                        + _output_token_reserve(snapshot, len(recheck_window.target_quote_ids))
                        > int(max_output)
                    )
                ):
                    stats["aborted"] = 1
                    stats["stop_reason"] = "剩余输出额度不足，已保留结果并停止额外复核。"
                    return stats
            if cached_result is None:
                raw, error, run_id, elapsed_ms = _dispatch_with_bounded_retry(
                    session_factory,
                    adapter,
                    job_id=job_id,
                    window=recheck_window,
                    state=state,
                    snapshot=snapshot,
                    settings=settings,
                    correction=correction,
                    max_tokens_override=max_tokens_override,
                )
                stats["calls"] += 1
                if error is not None:
                    timeout = error.kind is ProviderErrorKind.TIMEOUT
                    with session_factory() as session:
                        run = session.get(InferenceRun, run_id)
                        assert run is not None
                        run.elapsed_ms = elapsed_ms
                        run.error_code = error.code.value
                        run.state = (
                            InferenceRunState.UNKNOWN_OUTCOME
                            if timeout
                            else InferenceRunState.FAILED
                        )
                        run.usage_json = _usage_json_from_error(error)
                        if timeout:
                            failed_job = session.get(Job, job_id)
                            assert failed_job is not None
                            failed_job.state = JobState.NEEDS_RECONCILIATION
                            failed_job.last_error = f"复核超时，结果未知：{error.message}"
                        session.commit()
                    if timeout:
                        stats["unknown_outcome"] = 1
                        return stats
                    if (
                        error.kind is ProviderErrorKind.INVALID_OUTPUT
                        and attempt < retry_limit
                        and (
                            expression_task is None or _known_call_usage(error.details.get("usage"))
                        )
                    ):
                        correction = recheck_hint + "；本次重试需修正：" + error.message
                        max_tokens_override = (
                            _escalated_max_tokens(snapshot, error)
                            if expression_task is None
                            else None
                        )
                        continue
                    stats["aborted"] = 1
                    stats["stop_reason"] = f"复核调用失败，已保留结果：{error.message[:200]}"
                    break
            else:
                raw = cached_result.payload()

            with session_factory() as session:
                job = session.get(Job, job_id)
                assert job is not None
                state = (
                    _review_state(session, job, recheck_window) if full_window else _load_state(job)
                )
                safe, reason = _prepare_identity_view(session, job, version, state, recheck_window)
                if expression_task is not None:
                    state.production_expression_task = expression_task
                if safe:
                    ok, codes, messages, _repair_warnings = _apply_payload(
                        session,
                        window=recheck_window,
                        inputs=inputs,
                        state=state,
                        raw=raw,
                        run_id=run_id,
                        cache_key=cache_key,
                        preserve_existing_candidates=True,
                        attribution_only=full_window,
                    )
                else:
                    ok, codes, messages = False, ["invalid_identity_input"], [reason]
                if run_id:
                    run = session.get(InferenceRun, run_id)
                    assert run is not None
                    run.elapsed_ms = elapsed_ms
                    usage = raw.get("_usage") if isinstance(raw, dict) else None
                    known_usage = bool(usage) and not usage.get("unknown")
                    run.usage_json = json.dumps(usage, ensure_ascii=False) if known_usage else None
                    run.state = InferenceRunState.SUCCEEDED if ok else InferenceRunState.FAILED
                    if not ok:
                        run.error_code = "INVALID_MODEL_OUTPUT"
                if ok:
                    stats["windows"] += 1
                    stats["targets"] += len(recheck_window.target_quote_ids)
                session.commit()
            if ok:
                break
            if not safe:
                stats["aborted"], stats["stop_reason"] = 1, reason
                return stats
            if expression_task is not None and (
                "invalid_expression_input" in codes
                or not _known_call_usage(raw.get("_usage") if isinstance(raw, dict) else None)
            ):
                stats["aborted"], stats["stop_reason"] = (
                    1,
                    "复核输入已改变或用量未知，已保留有效结果",
                )
                return stats
            correction = (
                recheck_hint
                + "；本次重试需修正："
                + ("；".join(messages)[:800] or "；".join(codes))
            )
            cached_result = None
            if attempt == retry_limit:
                stats["aborted"] = 1
                stats["stop_reason"] = "复核输出未通过校验，已保留结果：" + (
                    "；".join(messages)[:200] or "；".join(codes)
                )
        if stats["aborted"]:
            return stats
    stats["round_completed"] = 1
    return stats


def _build_adapter(
    settings: Settings,
    credentials,  # noqa: ANN001
    *,
    snapshot: dict[str, Any] | None,
    credential_mode: CredentialMode,
    credential_ref: str | None,
) -> ProviderAdapter:
    snapshot = snapshot or {}
    protocol = str(snapshot.get("protocol", ""))
    _ = PROTOCOL_CAPABILITIES.get(protocol)  # 协议必须已登记（未登记会在 build_adapter 被拒绝）
    # 规则：**用户明确选择了密钥模式（session/system）却取不到密钥**才算缺凭据；
    # `credential_mode=none`（本地无鉴权网关）与 fake-provider（永远不需要密钥）不拦。
    if (
        protocol != FAKE_PROVIDER_PROTOCOL
        and credential_mode is not CredentialMode.NONE
        and credential_ref
        and not credentials.has(mode=credential_mode, ref=credential_ref)
    ):
        raise ProviderError(
            ProviderErrorKind.MISSING_CREDENTIAL,
            "缺少模型凭据：请在「模型配置」里补充密钥后再继续",
            details={
                "profile_id": snapshot.get("profile_id"),
                "credential_mode": credential_mode.value,
            },
            retryable=False,
        )
    return build_adapter(
        AdapterSpec(
            protocol=str(snapshot.get("protocol", "chat-completions-compatible")),
            base_url=str(snapshot.get("base_url", "")),
            model=str(snapshot.get("model", "")),
            params=snapshot.get("params", {}) or {},
            credential_mode=credential_mode,
            credential_ref=credential_ref,
            timeout_seconds=settings.llm_timeout_seconds,
            fake_labeling_mode=settings.fake_provider_labels,
            fake_script_mode=settings.fake_provider_script,
        ),
        credentials,
        allow_fake_provider=settings.allow_fake_provider,
    )


def backoff_seconds(attempt: int, settings: Settings) -> int:
    """限流退避：指数增长但**有上限**（上限来自配置，测试里可设为 0 不等待）。"""

    base = max(0, int(settings.rate_limit_backoff_base_seconds))
    cap = max(0, int(settings.rate_limit_backoff_max_seconds))
    return min(base * (2 ** max(0, attempt - 1)), cap)


def _usage_json_from_error(error: ProviderError) -> str | None:
    usage = error.details.get("usage") if isinstance(error.details, dict) else None
    if not isinstance(usage, dict) or usage.get("unknown"):
        return None
    return json.dumps(usage, ensure_ascii=False)


def _dispatch_with_bounded_retry(
    session_factory: sessionmaker[Session],
    adapter: ProviderAdapter,
    *,
    job_id: str,
    window,  # noqa: ANN001 - ProcessingWindow
    state: SceneState,
    snapshot: dict[str, Any] | None,
    settings: Settings,
    correction: str | None = None,
    max_tokens_override: int | None = None,
    request_payload_override: dict[str, Any] | None = None,
) -> tuple[Any, ProviderError | None, str, int]:
    """调用适配器；限流/暂时不可用按**有上限**的退避重试。

    返回最后一次尝试的 ``(raw, error, run_id, elapsed_ms)``。最后一条 `inference_runs`
    保持 DISPATCHED，由调用方按原有逻辑置为 SUCCEEDED/FAILED/UNKNOWN_OUTCOME；
    中间被退避重试的失败尝试在这里就写成 FAILED，保证每次尝试都可追溯。

    超时**不**自动重试：结果未知必须交人工对账。
    """

    allowed = max(1, int(settings.rate_limit_max_retries) + 1)
    raw: Any = None
    error: ProviderError | None = None
    run_id = ""
    elapsed_ms = 0
    request_payload = (
        deepcopy(request_payload_override)
        if request_payload_override is not None
        else _request_payload_for(
            window=window,
            state=state,
            correction=correction,
            max_tokens_override=max_tokens_override,
        )
    )
    request_fingerprint = _request_fingerprint(request_payload, snapshot)
    for index in range(allowed):
        with session_factory() as session:
            run = InferenceRun(
                job_id=job_id,
                window_id=window.window_id,
                profile_snapshot_json=json.dumps(snapshot or {}, ensure_ascii=False),
                request_fingerprint=request_fingerprint,
                state=InferenceRunState.PREPARED,
            )
            session.add(run)
            session.flush()
            from ..storage.run_archive import save_run_archive

            save_run_archive(run, request_payload)
            run.state = InferenceRunState.DISPATCHED
            run_id = run.id
            session.commit()

        started = time.perf_counter()
        raw = None
        error = None
        try:
            raw = _dispatch(
                adapter,
                window=window,
                state=state,
                correction=correction,
                max_tokens_override=max_tokens_override,
                request_payload=request_payload,
            )
        except ProviderError as exc:
            error = exc
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        # Commit transport evidence before semantic validation or application.
        # A received invalid proposal must not vanish when those steps fail.
        with session_factory() as session:
            returned_run = session.get(InferenceRun, run_id)
            if returned_run is not None:
                save_run_archive(
                    returned_run,
                    request_payload,
                    raw=raw,
                    error=error,
                    elapsed_ms=elapsed_ms,
                    phase="returned",
                )
                session.commit()

        if error is None or error.kind not in AUTO_RETRY_KINDS or index + 1 >= allowed:
            from ..llm.receipt import ProviderResult

            if isinstance(raw, dict):
                if not isinstance(raw, ProviderResult):
                    raw = ProviderResult(raw)
                raw.dispatch_attempts = index + 1
            if error is not None:
                error.dispatch_attempts = index + 1
            return raw, error, run_id, elapsed_ms

        backoff = backoff_seconds(index + 1, settings)
        with session_factory() as session:
            failed_run = session.get(InferenceRun, run_id)
            if failed_run is not None:
                failed_run.state = InferenceRunState.FAILED
                failed_run.error_code = error.code.value
                failed_run.elapsed_ms = elapsed_ms
                failed_run.usage_json = _usage_json_from_error(error)
            job = session.get(Job, job_id)
            if job is not None:
                job.progress_json = json.dumps(
                    {
                        "stage": "rate_limit_backoff",
                        "attempts": index + 1,
                        "retry_in_seconds": backoff,
                        "error_code": error.code.value,
                    },
                    ensure_ascii=False,
                )
            session.commit()
        if backoff > 0:
            time.sleep(backoff)
    return raw, error, run_id, elapsed_ms


def run_job(
    session_factory: sessionmaker[Session],
    settings: Settings,
    *,
    job_id: str,
    credentials=None,  # noqa: ANN001 - CredentialService
    adapter_factory: Callable[[Job, dict[str, Any] | None], ProviderAdapter] | None = None,
    max_windows: int | None = None,
    policy: BudgetPolicy | None = None,
) -> JobRunOutcome:
    """把任务跑到终态（或暂停 / 预算耗尽 / 需要人工对账）。

    上下文策略默认取任务范围里的 ``context_policy``（缺省是保守的 ``context-1``）；
    显式传入 ``policy`` 可以覆盖它（测试与 B3/B4 消融对比用）。
    """

    outcome = JobRunOutcome(job_id=job_id, state=JobState.QUEUED)
    calls = cached = done = 0
    strong_used = 0
    requested_policy = policy
    policy = requested_policy or DEFAULT_POLICY

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
        if job.kind is JobKind.CHARACTER_MERGE:
            # Do not retain the dispatcher read transaction during the model call.
            session.close()
            from ..characters.auto_merge import run_auto_merge_job

            state, merge_calls = run_auto_merge_job(
                session_factory,
                settings,
                job_id=job_id,
                credentials=credentials,
                adapter_factory=adapter_factory,
            )
            return JobRunOutcome(job_id=job_id, state=state, calls=merge_calls)
        if job.kind is JobKind.CHARACTER_ROSTER:
            roster_outcome = run_character_roster_job(
                session_factory,
                settings,
                job_id=job_id,
                credentials=credentials,
            )
            return JobRunOutcome(
                job_id=job_id,
                state=roster_outcome.state,
                calls=roster_outcome.calls,
                errors=list(roster_outcome.errors),
            )
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

        policy = requested_policy or policy_for_job(job)
        if requested_policy is None:
            budget = _budget_of(job)
            if budget.get("max_recheck_rounds") is not None:
                policy = replace(
                    policy,
                    recheck_max_rounds=int(budget["max_recheck_rounds"]),
                    recheck_max_targets=0,
                )
            elif "max_rechecks" in budget:
                policy = replace(
                    policy,
                    recheck_max_targets=int(budget["max_rechecks"] or 0),
                )
        if job.kind in {JobKind.INFERENCE, JobKind.RECHECK, JobKind.RECOMPUTE}:
            state = _load_state(job)
            roster_ok, roster_error = _apply_confirmed_roster(session, job, version, state)
            if not roster_ok:
                job.state = JobState.FAILED
                job.last_error = roster_error
                job.progress_json = json.dumps(
                    {"stage": "roster_not_confirmed"}, ensure_ascii=False
                )
                session.commit()
                outcome.state = JobState.FAILED
                outcome.errors.append("roster_not_confirmed")
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
        roster_ok, roster_error = _apply_confirmed_roster(session, job, version, state)
        if not roster_ok:
            job.state = JobState.FAILED
            job.last_error = roster_error
            job.progress_json = json.dumps({"stage": "roster_not_confirmed"}, ensure_ascii=False)
            session.commit()
            outcome.state = JobState.FAILED
            outcome.errors.append("roster_not_confirmed")
            return outcome
        snapshot = _json_of(job.profile_snapshot_json) or None
        profile = profile_for_job(session, job)
        credential_mode = credential_mode_of(profile)
        credential_ref = credential_reference(profile)
        strong_profile = _strong_profile(session, job)
        strong_snapshot = (
            profile_snapshot(strong_profile, (snapshot or {}).get("inference_options"))
            if strong_profile is not None
            else None
        )
        strong_mode = credential_mode_of(strong_profile)
        strong_ref = credential_reference(strong_profile)
        session.commit()

    outcome.windows_total = len(plan.windows)
    adapter: ProviderAdapter | None = None
    if adapter_factory is not None:
        adapter = adapter_factory(job, snapshot)
    elif credentials is not None:
        try:
            adapter = _build_adapter(
                settings,
                credentials,
                snapshot=snapshot,
                credential_mode=credential_mode,
                credential_ref=credential_ref,
            )
        except ProviderError as exc:
            # 缺 Key / 配置不可用：任务必须落到可解释的失败状态，并给出恢复动作
            with session_factory() as session:
                failed_job = session.get(Job, job_id)
                if failed_job is not None:
                    failed_job.state = JobState.FAILED
                    failed_job.last_error = f"{exc.code.value}: {exc.message}"
                    failed_job.progress_json = json.dumps(
                        {"stage": "adapter_unavailable", "error_code": exc.code.value},
                        ensure_ascii=False,
                    )
                    session.commit()
            outcome.state = JobState.FAILED
            outcome.errors.append(exc.code.value)
            return outcome

    # 成本路由：只有策略开启、且任务里指定了强模型配置时才可能升级
    strong_adapter: ProviderAdapter | None = None
    if policy.strong_model_share > 0 and strong_snapshot is not None:
        if adapter_factory is not None:
            strong_adapter = adapter_factory(job, strong_snapshot)
        elif credentials is not None:
            try:
                strong_adapter = _build_adapter(
                    settings,
                    credentials,
                    snapshot=strong_snapshot,
                    credential_mode=strong_mode,
                    credential_ref=strong_ref,
                )
            except ProviderError as exc:
                # 强模型不可用不是致命错误：如实退回基础模型，并把原因记进结果
                strong_adapter = None
                outcome.errors.append(f"strong_route_unavailable:{exc.code.value}")

    def _maybe_recheck(window_, window_adapter_, window_snapshot_) -> bool:  # noqa: ANN001, ANN202
        """首次结果落地后的有限复核（默认关闭）；返回 False 表示任务需要人工对账。"""

        if selected(job_snapshot):
            return True
        if (
            policy.recheck_max_targets <= 0
            and policy.recheck_max_rounds <= 0
            or window_adapter_ is None
        ):
            return True
        stats = _run_recheck(
            session_factory,
            settings,
            job_id=job_id,
            version=version,
            window=window_,
            adapter=window_adapter_,
            snapshot=window_snapshot_,
            policy=policy,
            reading_mode=_reading_mode_of(job_snapshot),
            horizon_cp=window_.visible_horizon_cp,
        )
        outcome.recheck_windows += int(stats["windows"])
        outcome.recheck_targets += int(stats["targets"])
        outcome.recheck_calls += int(stats["calls"])
        if stats.get("unknown_outcome"):
            outcome.unknown_runs += 1
            outcome.state = JobState.NEEDS_RECONCILIATION
            return False
        return True

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
                if policy.recheck_max_rounds > 0:
                    job_snapshot = job
                    session.commit()
                    if not _maybe_recheck(window, adapter, snapshot):
                        return outcome
                continue
            if row_state is JobState.NEEDS_RECONCILIATION:
                job.state = JobState.NEEDS_RECONCILIATION
                session.commit()
                outcome.state = JobState.NEEDS_RECONCILIATION
                return outcome
            spent = spent_tokens(session, job_id)
            job_snapshot = job
            state = _load_state(job)
            roster_ok, roster_error = _apply_confirmed_roster(session, job, version, state, window)
            if not roster_ok:
                job.state = JobState.FAILED
                job.last_error = roster_error
                job.progress_json = json.dumps(
                    {"stage": "roster_not_confirmed"}, ensure_ascii=False
                )
                session.commit()
                outcome.state = JobState.FAILED
                outcome.errors.append("roster_not_confirmed")
                return outcome
            session.commit()

        # 预算预留（含输出预留；未知用量也按此保守口径）
        target_count = len(window.target_quote_ids)
        output_reserve = max(
            _output_token_reserve(snapshot, target_count),
            _output_token_reserve(strong_snapshot, target_count)
            if strong_snapshot is not None
            else 0,
        )
        planned_reserve = window.budget["total_tokens"] + output_reserve
        actual_prompt_tokens = sum(
            estimate_tokens(message["content"])
            for message in _request_payload_for(window=window, state=state)["messages"]
        )
        reserve = max(planned_reserve, actual_prompt_tokens + output_reserve)
        max_input = _budget_of(job_snapshot).get("max_input_tokens")
        pending_review = selected(job_snapshot) and window.window_id in _json_of(
            job_snapshot.checkpoint_json
        ).get("expression_reviews", {})
        if (
            max_input is not None
            and spent["input_tokens"] + reserve > int(max_input)
            and not pending_review
        ):
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

        # 成本路由：默认关闭；开启时也只升级困难窗口，且不超过 share 上限
        route = route_window(
            policy=policy,
            window=window,
            total_windows=len(plan.windows),
            strong_available=strong_adapter is not None,
            strong_used=strong_used,
        )
        window_adapter = strong_adapter if route.strong else adapter
        window_snapshot = strong_snapshot if route.strong else snapshot

        cache_key = _cache_key_for(
            job=job_snapshot,
            version=version,
            window=window,
            snapshot=window_snapshot,
            state=state,
            review_rounds=policy.recheck_max_rounds,
        )
        expression_task = state.production_expression_task

        # 缓存命中：不调用模型，也不新增推理尝试
        with session_factory() as session:
            cached_result = (
                None
                if _range_of(job_snapshot).get("force_reprocess")
                else ResultCacheStore(session).get(cache_key)
            )
        if cached_result is not None:
            try:
                cached_raw = (
                    cached_proposal(cached_result, expression_task)
                    if expression_task is not None
                    else cached_result.payload()
                )
            except (ValueError, ProviderError, KeyError, TypeError) as exc:
                with session_factory() as session:
                    failed_job = session.get(Job, job_id)
                    failed_job.state = JobState.FAILED
                    failed_job.last_error = "缓存复核提案无法安全恢复：" + str(exc)[:400]
                    session.commit()
                outcome.state = JobState.FAILED
                outcome.errors.append("invalid_review_cache")
                return outcome
            with session_factory() as session:
                job = session.get(Job, job_id)
                assert job is not None
                state = _load_state(job)
                safe, reason = _apply_confirmed_roster(session, job, version, state, window)
                if not safe:
                    job.state, job.last_error = JobState.FAILED, reason
                    failed_window = session.scalar(
                        select(JobWindow).where(
                            JobWindow.job_id == job_id, JobWindow.window_id == window.window_id
                        )
                    )
                    if failed_window:
                        failed_window.state = JobState.FAILED
                    session.commit()
                    outcome.state = JobState.FAILED
                    outcome.errors.append("invalid_identity_input")
                    return outcome
                if expression_task is not None:
                    state.production_expression_task = expression_task
                ok, codes, messages, repair_warnings = _apply_payload(
                    session,
                    window=window,
                    inputs=inputs,
                    state=state,
                    raw=cached_raw,
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
                            **_json_of(job.checkpoint_json),
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
                if ok:
                    job.last_error = None
                else:
                    problems = "；".join(messages[:3])
                    job.last_error = f"缓存结果未通过校验：{codes}；问题：{problems}"
                session.commit()
            if "invalid_expression_input" in codes:
                with session_factory() as session:
                    failed_job = session.get(Job, job_id)
                    failed_job.state = JobState.PARTIAL if done else JobState.FAILED
                    failed_row = session.scalar(
                        select(JobWindow).where(
                            JobWindow.job_id == job_id, JobWindow.window_id == window.window_id
                        )
                    )
                    if failed_row:
                        failed_row.state = JobState.FAILED
                    session.commit()
                    outcome.state = failed_job.state
                outcome.errors.append("invalid_expression_input")
                return outcome
            if ok:
                # 首次结果落地后按策略做有限局部复核（默认关闭）
                if not _maybe_recheck(window, window_adapter, window_snapshot):
                    return outcome
                continue

        if window_adapter is None:
            outcome.errors.append("adapter_unavailable")
            with session_factory() as session:
                job = session.get(Job, job_id)
                assert job is not None
                job.state = JobState.FAILED
                job.last_error = "没有可用的模型适配器（缺少凭据或配置被删除）"
                session.commit()
            outcome.state = JobState.FAILED
            return outcome

        # 一次（或有限次退避重试的）尝试：
        # PREPARED → DISPATCHED（提交）→ 调用（无事务）→ 应用 + 结算（新事务）
        if route.strong:
            strong_used += 1
            outcome.strong_windows += 1

        # 每个窗口首次调用 + 用户设置的有限纠错重发。
        # 每次调用都单独写 inference_runs 并各自结算用量，不是只记最后一次。
        correction: str | None = None
        retry_correction: str | None = None
        retry_max_tokens: int | None = None
        attempt_max_tokens: int | None = None
        window_failed = False
        format_retry_limit = _format_retry_limit(job_snapshot)
        for attempt in range(1 + format_retry_limit):
            if attempt:
                with session_factory() as session:
                    job = session.get(Job, job_id)
                    assert job is not None
                    if job.state in STOP_STATES:
                        outcome.state = job.state
                        return outcome
                    spent = spent_tokens(session, job_id)
                    max_input = _budget_of(job).get("max_input_tokens")
                    retry_reserve = sum(
                        estimate_tokens(message["content"])
                        for message in _request_payload_for(
                            window=window, state=state, correction=correction
                        )["messages"]
                    ) + max(output_reserve, attempt_max_tokens or 0)
                    if max_input is not None and (
                        spent["input_tokens"] + spent["unknown_runs"] * reserve + retry_reserve
                        > int(max_input)
                    ):
                        job.state = JobState.BUDGET_EXHAUSTED
                        job.last_error = "剩余额度不足以纠错重试，已停止调用"
                        session.commit()
                        outcome.state = JobState.BUDGET_EXHAUSTED
                        outcome.budget_exhausted = True
                        return outcome
                    job.progress_json = json.dumps(
                        {
                            "stage": "validation_retry",
                            "retry_attempt": attempt,
                            "retry_limit": format_retry_limit,
                            "windows_done": done,
                        },
                        ensure_ascii=False,
                    )
                    session.commit()
            restored = None
            if expression_task is not None and selected(job_snapshot):
                with session_factory() as session:
                    active_job = session.get(Job, job_id)
                    try:
                        restored = restore_primary(
                            session, active_job, window, expression_task, window_snapshot
                        )
                    except ValueError as exc:
                        active_job.state, active_job.last_error = JobState.FAILED, str(exc)
                        session.commit()
                        outcome.state = JobState.FAILED
                        outcome.errors.append("invalid_review_restore")
                        return outcome
            raw, error, run_id, elapsed_ms = restored or _dispatch_with_bounded_retry(
                session_factory,
                window_adapter,
                job_id=job_id,
                window=window,
                state=state,
                snapshot=window_snapshot,
                settings=settings,
                correction=correction,
                max_tokens_override=attempt_max_tokens,
            )

            if (
                error is None
                and expression_task is not None
                and selected(job_snapshot)
                and policy.recheck_max_rounds > 0
            ):
                from ..llm.expression_compiler import compile_expression_output

                try:
                    if not isinstance(raw, dict):
                        raise ValueError("模型提案必须是对象")
                    compile_expression_output(
                        {k: v for k, v in raw.items() if not str(k).startswith("_")},
                        expression_task,
                    )
                except (ValueError, ProviderError):
                    pass  # Original format-retry path owns invalid first proposals.
                else:
                    review = run_review_pipeline(
                        session_factory,
                        settings,
                        job_id=job_id,
                        window=window,
                        state=state,
                        adapter=window_adapter,
                        model_snapshot=window_snapshot,
                        primary_raw=raw,
                        primary_run_id=run_id,
                        primary_elapsed_ms=elapsed_ms,
                        rounds=policy.recheck_max_rounds,
                    )
                    outcome.recheck_calls += review.calls
                    if review.stopped:
                        with session_factory() as session:
                            active_job = session.get(Job, job_id)
                            if active_job.state is JobState.PAUSING:
                                active_job.state = JobState.PAUSED
                                session.commit()
                            outcome.state = active_job.state
                        outcome.unknown_runs += int(review.unknown)
                        return outcome
                    review.raw["_usage"] = raw.get("_usage")
                    raw = review.raw
                    outcome.recheck_windows += 1
                    outcome.recheck_targets += len(window.target_quote_ids)

            retry_correction = None
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
                    run.usage_json = _usage_json_from_error(error)
                    if error.kind is ProviderErrorKind.TIMEOUT:
                        # 超时可能已经计费且结果未知：不自动重发，交人工对账
                        outcome.errors.append(f"{window.window_id}:{error.code.value}")
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

                    # 格式/契约类错误按用户配置有限纠错重发。
                    if (
                        error.kind is ProviderErrorKind.INVALID_OUTPUT
                        and attempt < format_retry_limit
                        and (
                            expression_task is None or _known_call_usage(error.details.get("usage"))
                        )
                    ):
                        run.state = InferenceRunState.FAILED
                        run.error_code = error.code.value
                        session.commit()
                        retry_correction = error.message
                        retry_max_tokens = (
                            _escalated_max_tokens(window_snapshot, error)
                            if expression_task is None
                            else None
                        )
                    else:
                        outcome.errors.append(f"{window.window_id}:{error.code.value}")
                        run.state = InferenceRunState.FAILED
                        run.error_code = error.code.value
                        if row is not None:
                            row.state = JobState.FAILED
                        job.state = JobState.PARTIAL if done else JobState.FAILED
                        # 真实提供方返回坏结构时带上脱敏片段，避免只剩一个错误码无从定位
                        snippet = ""
                        if isinstance(getattr(error, "details", None), dict):
                            raw_body = error.details.get("body") or error.details.get("snippet")
                            if isinstance(raw_body, str) and raw_body.strip():
                                snippet = f"；原始输出片段：{raw_body[:160]}"
                        job.last_error = f"{error.code.value}: {error.message}{snippet}"
                        session.commit()
                        outcome.state = job.state
                        window_failed = True
                        break
                else:
                    calls += int(not getattr(raw, "restored_attempt", False))
                    usage = raw.get("_usage") if isinstance(raw, dict) else None
                    known_usage = bool(usage) and not usage.get("unknown")
                    run.usage_json = json.dumps(usage, ensure_ascii=False) if known_usage else None
                    state = _load_state(job)
                    safe, reason = _apply_confirmed_roster(session, job, version, state, window)
                    if expression_task is not None:
                        state.production_expression_task = expression_task
                    if not safe:
                        run.state = InferenceRunState.FAILED
                        run.error_code = "INVALID_IDENTITY_INPUT"
                        job.state = JobState.PARTIAL if done else JobState.FAILED
                        job.last_error = reason
                        if row is not None:
                            row.state = JobState.FAILED
                        session.commit()
                        outcome.state, outcome.calls = job.state, calls
                        outcome.errors.append("invalid_identity_input")
                        return outcome
                    ok, codes, messages, repair_warnings = _apply_payload(
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
                        if (
                            attempt < format_retry_limit
                            and "invalid_expression_input" not in codes
                            and (expression_task is None or _known_call_usage(usage))
                        ):
                            # 记下失败尝试，带着具体问题重发一次（不把窗口标记为终态失败）
                            session.commit()
                            hint = "；".join(messages)[:800] or "；".join(codes)
                            if "undeclared_new_speaker" in codes:
                                hint += (
                                    "；请在同一次输出的 new_speakers 中声明这些 temp_ref"
                                    "（first_quote_id 用该对白自己的 id），不要只写 NEW"
                                )
                            retry_correction = hint
                        else:
                            if row is not None:
                                row.state = JobState.FAILED
                            job.state = JobState.PARTIAL if done else JobState.FAILED
                            problems = "；".join(messages[:3])
                            job.last_error = f"模型输出未通过校验：{codes}；问题：{problems}"
                            session.commit()
                            outcome.state = job.state
                            window_failed = True
                            break
                    else:
                        run.state = InferenceRunState.SUCCEEDED
                        known_usage = bool(usage) and not usage.get("unknown")
                        run.usage_json = (
                            json.dumps(usage, ensure_ascii=False) if known_usage else None
                        )
                        if not known_usage:
                            outcome.unknown_runs += 1
                        if row is not None:
                            row.state = JobState.COMPLETED
                        done += 1
                        job.checkpoint_json = json.dumps(
                            {
                                **_json_of(job.checkpoint_json),
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
                                "strong_windows": outcome.strong_windows,
                                "route": route.as_dict(),
                                "repairs": list(repair_warnings[:5]),
                            },
                            ensure_ascii=False,
                        )
                        job.last_error = None
                        session.commit()
                        break

            if retry_correction is not None:
                # 带着具体问题重发一次：校验问题（如未声明的临时人物）或提供方错误原文都作为提示；
                # 上次若被输出上限截断，同时提高 max_tokens，避免用同样预算再截断一次。
                correction = retry_correction
                attempt_max_tokens = retry_max_tokens
                continue

        if window_failed:
            break

        # 有限局部复核（默认关闭；只有策略显式开启才会多花一次调用）
        if not _maybe_recheck(window, window_adapter, window_snapshot):
            return outcome

    # ---------- 阶段 3：收尾 ----------
    with session_factory() as session:
        job = session.get(Job, job_id)
        assert job is not None
        outcome.windows_done = done
        outcome.cached_windows = cached
        outcome.calls = calls
        outcome.usage = spent_tokens(session, job_id)
        if job.state is JobState.PAUSING:
            job.state = JobState.PAUSED
            job.progress_json = json.dumps({"stage": "paused", "windows_done": done})
        elif job.state not in STOP_STATES:
            if outcome.windows_total == 0 or done >= outcome.windows_total:
                job.state = JobState.COMPLETED
                job_range = _range_of(job)
                chapter_id = job_range.get("chapter_id")
                if (
                    job.kind is JobKind.INFERENCE
                    and chapter_id
                    and not job_range.get("selected_window_ids")
                ):
                    chapter = session.get(Chapter, str(chapter_id))
                    if chapter is not None and chapter.book_version_id == job.book_version_id:
                        complete_chapter_automatically(session, chapter)
                job.progress_json = json.dumps(
                    {
                        "stage": "completed",
                        "windows_done": done,
                        "cached_windows": cached,
                        "calls": calls,
                        "strong_windows": outcome.strong_windows,
                        "recheck_windows": outcome.recheck_windows,
                        "recheck_targets": outcome.recheck_targets,
                        "recheck_calls": outcome.recheck_calls,
                        "review_stopped": _json_of(job.checkpoint_json).get("review_stopped", {}),
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


def _request_payload_for(
    *,
    window,  # noqa: ANN001
    state: SceneState,
    correction: str | None = None,
    max_tokens_override: int | None = None,
) -> dict[str, Any]:
    """The business request shared by dispatch, budgeting and message cache keys."""
    task = state.production_expression_task
    if task is None:
        messages = _messages_for(
            window=window, state=state, locked_summary=None, correction=correction
        )
    else:
        from ..evaluation.owner_constraints import ConstrainedOwnerProtocol
        from ..scenes.runner import _validate_expression_task

        _validate_expression_task(task, window, state, None)
        messages = ConstrainedOwnerProtocol(task).messages()
        if correction:
            messages.append(
                {
                    "role": "user",
                    "content": correction
                    if correction.startswith("独立复核")
                    else "上一次输出无效：" + correction,
                }
            )
    payload = {
        "messages": messages,
        "max_tokens": LABELING_MAX_TOKENS,
        "json_object": True,
        "target_quote_ids": list(window.target_quote_ids),
    }
    if isinstance(max_tokens_override, int) and max_tokens_override > 0:
        payload["max_tokens_override"] = int(max_tokens_override)
    if task is not None:
        payload["compiler_task_fingerprint"] = task.fingerprint()
        payload["output_protocol"] = PRODUCTION_EXPRESSION_VERSION
    return payload


def _request_fingerprint(payload: dict[str, Any], snapshot: dict[str, Any] | None) -> str:
    snapshot = snapshot or {}
    return fingerprint(
        {
            "version": "production-request-1",
            "request": payload,
            "model_configuration": {
                key: snapshot.get(key)
                for key in ("protocol", "base_url", "model", "params", "inference_options")
            },
        }
    )


def _known_call_usage(usage) -> bool:
    return (
        isinstance(usage, dict)
        and not usage.get("unknown")
        and type(usage.get("total_tokens")) is int
        and usage["total_tokens"] >= 0
    )


def _dispatch(
    adapter: ProviderAdapter,
    *,
    window,  # noqa: ANN001
    state: SceneState,
    correction: str | None = None,
    max_tokens_override: int | None = None,
    request_payload: dict[str, Any] | None = None,
) -> Any:
    payload = (
        request_payload
        if request_payload is not None
        else _request_payload_for(
            window=window,
            state=state,
            correction=correction,
            max_tokens_override=max_tokens_override,
        )
    )
    # An adapter cannot mutate the frozen request used by another retry or its ledger hash.
    raw = asyncio.run(adapter.generate_labels(deepcopy(payload)))
    if state.production_expression_task is not None:
        return raw
    from ..llm.receipt import ProviderResult

    restored = _restore_output_references(raw, window)
    return ProviderResult(
        restored,
        receipt=getattr(raw, "receipt", None),
        original_result=getattr(raw, "original_result", raw),
    )


def reconcile_stale_runs(
    session_factory: sessionmaker[Session],
    *,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> list[str]:
    """把超过租约仍处于 DISPATCHED 的尝试标为未知结果。"""

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

    rows = list(session.execute(select(JobWindow).where(JobWindow.job_id == job.id)).scalars())
    pending = [row for row in rows if row.state is JobState.NEEDS_RECONCILIATION]
    if action == "retry":
        checkpoint = _json_of(job.checkpoint_json)
        for row in pending:
            row.state = JobState.QUEUED
            entry = checkpoint.get("expression_reviews", {}).get(row.window_id)
            if entry and entry.get("failed_stage"):
                stage = entry["failed_stage"]
                generations = entry.setdefault("retry_stages", {})
                generations[stage] = generations.get(stage, 0) + 1
        job.checkpoint_json = json.dumps(checkpoint, ensure_ascii=False)
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
