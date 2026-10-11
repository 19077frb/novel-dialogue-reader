"""Opt-in staged expression proposals; network calls never hold a transaction."""

import json
from copy import deepcopy
from dataclasses import dataclass, replace

from sqlalchemy import select

from ..context.budget import estimate_tokens
from ..domain.enums import InferenceRunState, JobState
from ..llm.errors import ProviderError, ProviderErrorKind
from ..llm.expression_compiler import compile_expression_output
from ..llm.expression_review import (
    REVIEW_VERSION,
    agreed_challenges,
    build_challenge_messages,
    decision_payload,
    reconcile,
    verify_payload,
)
from ..llm.expression_review import (
    snapshot as proposal_snapshot,
)
from ..llm.receipt import ProviderResult
from ..storage.cache import fingerprint
from ..storage.models import Annotation, InferenceRun, Job, JobWindow
from ..storage.run_archive import decode_archive

REVIEW_CACHE_VERSION = "expr-review-1"


class ReviewedExpression(ProviderResult):
    def __init__(self, payload, *, approvals, task_fingerprint, auxiliary_warnings=()):
        super().__init__(payload)
        self.owner_approvals = deepcopy(approvals)
        self.task_fingerprint = task_fingerprint
        self.auxiliary_warnings = [w[:600] for w in auxiliary_warnings if isinstance(w, str)][:5]

    def cache_payload(self):
        return {
            "review_version": REVIEW_VERSION,
            "payload": _clean(self),
            "owner_approvals": self.owner_approvals,
            "task_fingerprint": self.task_fingerprint,
            **({"auxiliary_warnings": self.auxiliary_warnings} if self.auxiliary_warnings else {}),
        }


def cached_proposal(cached, task):
    if cached.schema_version != REVIEW_CACHE_VERSION:
        return cached.payload()
    record = cached.payload()
    if (
        record.get("review_version") != REVIEW_VERSION
        or record.get("task_fingerprint") != task.fingerprint()
    ):
        raise ValueError("Review cache does not match the frozen task")
    result = ReviewedExpression(
        record["payload"],
        approvals=record["owner_approvals"],
        task_fingerprint=record["task_fingerprint"],
        auxiliary_warnings=record.get("auxiliary_warnings", ()),
    )
    compile_expression_output(result, task, owner_approvals=result.owner_approvals)
    return result


def selected(job):
    return json.loads(job.range_json or "{}").get("review_protocol") == REVIEW_VERSION


def _clean(raw):
    if not isinstance(raw, dict):
        raise ValueError("模型提案必须是对象")
    return {k: deepcopy(v) for k, v in raw.items() if not str(k).startswith("_")}


def restore_primary(session, job, window, task, model_snapshot):
    entry = (
        json.loads(job.checkpoint_json or "{}").get("expression_reviews", {}).get(window.window_id)
    )
    if not entry:
        return None
    if entry["task_fingerprint"] != task.fingerprint() or entry["model_fingerprint"] != fingerprint(
        model_snapshot
    ):
        raise ValueError("人物资料或模型输入已改变，不能恢复旧复核提案")
    run = session.get(InferenceRun, entry["primary_run_id"])
    if run is None or run.job_id != job.id or run.window_id != window.window_id:
        raise ValueError("复核首次调用记录缺失")
    record = decode_archive(run.call_archive)
    if (
        record.get("phase") != "returned"
        or record.get("error")
        or not isinstance(record.get("adapter_result"), dict)
    ):
        raise ValueError("首次有效提案归档不完整")
    if record["request"].get("compiler_task_fingerprint") != task.fingerprint():
        raise ValueError("首次提案原文映射已改变")
    raw = ProviderResult(record["adapter_result"])
    raw.restored_attempt = True
    from .scheduler import _retain_format_rows_in_session

    raw = _retain_format_rows_in_session(session, job.id, window.window_id, run.id, raw, task)
    compile_expression_output(_clean(raw), task)
    return raw, None, run.id, run.elapsed_ms or 0


@dataclass
class PipelineResult:
    raw: dict
    calls: int = 0
    stopped: bool = False
    unknown: bool = False


def run_review_pipeline(
    session_factory,
    settings,
    *,
    job_id,
    window,
    state,
    adapter,
    model_snapshot,
    primary_raw,
    primary_run_id,
    primary_elapsed_ms,
    rounds,
):
    from ..evaluation.owner_constraints import ConstrainedOwnerProtocol
    from .scheduler import (
        STOP_STATES,
        _budget_of,
        _dispatch_with_bounded_retry,
        _format_retry_limit,
        _known_call_usage,
        _output_token_reserve,
        _request_fingerprint,
        spent_tokens,
    )

    task = deepcopy(state.production_expression_task)
    base = _clean(primary_raw)
    first = proposal_snapshot(base, task, call_ref=primary_run_id)
    diagnostic_warnings = list(first.get("auxiliary_warnings", []))
    base = first.get("primary_payload", base)
    result = PipelineResult(primary_raw)
    original_breaks = tuple(base.get("breaks", ()))
    with session_factory() as session:
        job = session.get(Job, job_id)
        feedback_version = json.loads(job.range_json or "{}").get("identity_feedback_protocol")
        locked_targets = (
            set(
                session.scalars(
                    select(Annotation.quote_id).where(
                        Annotation.quote_id.in_(list(first["decisions"])),
                        Annotation.user_locked.is_(True),
                    )
                )
            )
            if feedback_version
            else set()
        )
        checkpoint = json.loads(job.checkpoint_json or "{}")
        checkpoint.setdefault("expression_reviews", {}).setdefault(window.window_id, {}).update(
            {
                "primary_run_id": primary_run_id,
                "task_fingerprint": task.fingerprint(),
                "model_fingerprint": fingerprint(model_snapshot),
            }
        )
        job.checkpoint_json = json.dumps(checkpoint, ensure_ascii=False)
        primary = session.get(InferenceRun, primary_run_id)
        primary.state = InferenceRunState.SUCCEEDED
        primary.elapsed_ms = primary_elapsed_ms
        usage = primary_raw.get("_usage")
        primary.usage_json = json.dumps(usage) if _known_call_usage(usage) else None
        session.commit()

    def stopped(reason, *, unknown=False, pause=False, failed_stage=None):
        with session_factory() as session:
            job = session.get(Job, job_id)
            checkpoint = json.loads(job.checkpoint_json or "{}")
            checkpoint.setdefault("review_stopped", {})[window.window_id] = reason[:400]
            job.checkpoint_json = json.dumps(checkpoint, ensure_ascii=False)
            if unknown:
                job.state = JobState.NEEDS_RECONCILIATION
                job.last_error = reason
                checkpoint["expression_reviews"][window.window_id]["failed_stage"] = failed_stage
                job.checkpoint_json = json.dumps(checkpoint, ensure_ascii=False)
                row = session.scalar(
                    select(JobWindow).where(
                        JobWindow.job_id == job_id, JobWindow.window_id == window.window_id
                    )
                )
                if row is not None:
                    row.state = JobState.NEEDS_RECONCILIATION
            session.commit()
        result.unknown, result.stopped = unknown, pause or unknown

    def stage(stage_name, messages, validate, *, binding=None):
        correction = None
        with session_factory() as session:
            retry_limit = _format_retry_limit(session.get(Job, job_id))
            entry = json.loads(session.get(Job, job_id).checkpoint_json)["expression_reviews"][
                window.window_id
            ]
            generation = entry.get("retry_stages", {}).get(stage_name, 0)
        for attempt in range(1 + retry_limit):
            request = {
                "messages": deepcopy(messages),
                "json_object": True,
                "target_quote_ids": list(window.target_quote_ids),
                "output_protocol": "expression-production-1",
                "max_tokens": 4096,
                "compiler_task_fingerprint": task.fingerprint(),
                "review_stage": stage_name,
                "review_version": REVIEW_VERSION,
                "review_generation": generation,
                "review_binding": fingerprint([primary_run_id, binding]),
            }
            if task.auxiliary_protocol is not None:
                request["auxiliary_protocol"] = task.auxiliary_protocol
            if correction:
                request["messages"].append(
                    {"role": "user", "content": "上一次输出未通过校验：" + correction}
                )
            proof = _request_fingerprint(request, model_snapshot)
            reused = False
            with session_factory() as session:
                job = session.get(Job, job_id)
                if job.state in STOP_STATES or job.state is JobState.PAUSING:
                    result.stopped = True
                    return None
                previous = session.scalar(
                    select(InferenceRun)
                    .where(
                        InferenceRun.job_id == job_id,
                        InferenceRun.window_id == window.window_id,
                        InferenceRun.request_fingerprint == proof,
                    )
                    .order_by(InferenceRun.created_at.desc(), InferenceRun.id.desc())
                )
                raw, error, run_id, elapsed = None, None, None, 0
                if previous is not None:
                    try:
                        record = decode_archive(previous.call_archive)
                    except ValueError:
                        stopped(
                            "复核阶段归档缺失或损坏，不能自动重发",
                            unknown=True,
                            failed_stage=stage_name,
                        )
                        return None
                    if record.get("phase") != "returned":
                        stopped(
                            "复核阶段远程结果未知，不能自动重发",
                            unknown=True,
                            failed_stage=stage_name,
                        )
                        return None
                    raw, run_id, elapsed = (
                        record.get("adapter_result"),
                        previous.id,
                        record.get("elapsed_ms") or 0,
                    )
                    failed = record.get("error")
                    if failed:
                        if not isinstance(failed.get("message"), str):
                            stopped("旧失败归档缺少原始错误，不能自动重发修复请求")
                            return None
                        error = ProviderError(
                            ProviderErrorKind(failed["kind"]),
                            failed["message"],
                            details=failed["details"],
                        )
                    reused = True
                spent = spent_tokens(session, job_id)
                budget = _budget_of(job)
                reserve = sum(estimate_tokens(m["content"]) for m in request["messages"])
                output_reserve = _output_token_reserve(model_snapshot, len(window.target_quote_ids))
                if not reused and (
                    spent["unknown_runs"]
                    and not generation
                    or budget.get("max_input_tokens") is not None
                    and spent["input_tokens"]
                    + reserve * (spent["unknown_runs"] + 1)
                    + output_reserve
                    > int(budget["max_input_tokens"])
                    or budget.get("max_output_tokens") is not None
                    and spent["output_tokens"] + output_reserve * (spent["unknown_runs"] + 1)
                    > int(budget["max_output_tokens"])
                ):
                    stopped("用量未知或剩余额度不足，保留已有有效提案")
                    return None
                job.progress_json = json.dumps(
                    {
                        "stage": "rechecking",
                        "window_id": window.window_id,
                        "review_stage": stage_name,
                    }
                )
                session.commit()
            if not reused:
                raw, error, run_id, elapsed = _dispatch_with_bounded_retry(
                    session_factory,
                    adapter,
                    job_id=job_id,
                    window=window,
                    state=state,
                    snapshot=model_snapshot,
                    settings=settings,
                    request_payload_override=request,
                )
                result.calls += getattr(error if error else raw, "dispatch_attempts", 1)
            usage = (
                error.details.get("usage")
                if error
                else raw.get("_usage")
                if isinstance(raw, dict)
                else None
            )
            valid = None
            problem = error.message[:800] if error else ""
            if error is None:
                try:
                    valid = validate(_clean(raw), run_id)
                except (ValueError, ProviderError, TypeError, KeyError) as exc:
                    problem = str(exc)[:800]
            with session_factory() as session:
                run = session.get(InferenceRun, run_id)
                run.state = (
                    InferenceRunState.SUCCEEDED if valid is not None else InferenceRunState.FAILED
                )
                run.elapsed_ms = elapsed
                run.usage_json = json.dumps(usage) if _known_call_usage(usage) else None
                run.error_code = (
                    None
                    if valid is not None
                    else error.code.value
                    if error
                    else "INVALID_MODEL_OUTPUT"
                )
                if error and error.kind is ProviderErrorKind.TIMEOUT:
                    run.state = InferenceRunState.UNKNOWN_OUTCOME
                active = session.get(Job, job_id)
                halt = active.state in STOP_STATES or active.state is JobState.PAUSING
                session.commit()
            if error and error.kind is ProviderErrorKind.TIMEOUT:
                stopped("复核超时，结果未知，需人工核对", unknown=True, failed_stage=stage_name)
                return None
            if halt:
                result.stopped = True
                return None
            if valid is not None:
                return valid
            if (
                not _known_call_usage(usage)
                or attempt == retry_limit
                or error
                and error.kind is not ProviderErrorKind.INVALID_OUTPUT
            ):
                stopped("复核失败，保留已有有效提案：" + problem)
                return None
            correction = problem
        return None

    feedback = None
    feedback_targets = set()
    if feedback_version and rounds:
        from ..llm.identity_feedback import (
            FEEDBACK_VERSION,
            build_identity_feedback_messages,
            compile_identity_feedback,
            prepare_identity_feedback,
        )

        if feedback_version != FEEDBACK_VERSION:
            raise ValueError("Unsupported identity feedback version")
        plan = prepare_identity_feedback(
            task, base, primary_source=primary_run_id, locked_targets=locked_targets
        )
        feedback = stage(
            "identity_feedback:1",
            build_identity_feedback_messages(plan, task),
            lambda payload, run_id: compile_identity_feedback(
                plan, payload, task, source_ref=run_id
            ),
            binding=plan.fingerprint(),
        )
        if feedback is not None:
            issues = json.loads(feedback.issues_json)
            feedback_targets = {
                task.references[q]
                for issue in issues
                if issue["kind"] != "incorrect_pov"
                for q in issue["targets"]
            }
            with session_factory() as session:
                job = session.get(Job, job_id)
                checkpoint = json.loads(job.checkpoint_json or "{}")
                checkpoint["expression_reviews"][window.window_id]["identity_feedback"] = {
                    "plan_fingerprint": feedback.plan_fingerprint,
                    "source_ref": feedback.source_ref,
                    "issues": issues,
                    "pov_candidate": feedback.pov_candidate,
                }
                job.checkpoint_json = json.dumps(checkpoint, ensure_ascii=False)
                session.commit()
            if feedback_targets:
                pending = {
                    q: replace(d, admissible=False) if q in feedback_targets else d
                    for q, d in first["decisions"].items()
                }
                pending_payload, approvals = decision_payload(
                    task, pending, breaks=original_breaks, anonymous=first["anonymous"]
                )
                result.raw = ReviewedExpression(
                    pending_payload, approvals=approvals, task_fingerprint=task.fingerprint()
                )
                result.raw.restored_attempt = getattr(primary_raw, "restored_attempt", False)

    for round_index in range(rounds):
        if result.stopped:
            break

        def check_proposal(payload, run_id):
            if payload.get("breaks"):
                raise ValueError("复核不允许改变场景边界")
            proposed = proposal_snapshot(payload, task, call_ref=run_id)
            diagnostic_warnings.extend(proposed.get("auxiliary_warnings", []))
            del diagnostic_warnings[:-5]
            return proposed.get("primary_payload", payload), proposed

        messages = ConstrainedOwnerProtocol(task).messages()
        messages.append(
            {
                "role": "user",
                "content": "独立复核全部目标，只看原文，不提供首次答案。breaks必须为空。",
            }
        )
        second = stage(f"review:{round_index + 1}", messages, check_proposal)
        if second is None:
            break
        reviewed = second[1]
        differences = [
            q
            for q in first["decisions"]
            if first["decisions"][q].identity() != reviewed["decisions"][q].identity()
        ]
        third = None
        anonymous = {**first["anonymous"], **reviewed["anonymous"]}
        feedback_challenge = (
            round_index == 0 and feedback is not None and bool(json.loads(feedback.issues_json))
        )
        if differences or feedback_challenge:
            adjudication = ConstrainedOwnerProtocol(task).messages()
            adjudication.append(
                {
                    "role": "user",
                    "content": (
                        "双方答案仅是候选，请根据完整原文和双方证据裁决。"
                        "允许有证据的第三种人物答案；无法区分则未知。"
                        "不得改变场景，breaks为空。\n"
                    )
                    + json.dumps(
                        {
                            "first": base,
                            "review": second[0],
                            **(
                                {
                                    "identity_feedback": {
                                        "issues": json.loads(feedback.issues_json),
                                        "proposal": json.loads(feedback.proposal_json),
                                        "pov_candidate": feedback.pov_candidate,
                                        "instruction": "反馈只是候选，不强制接受身份或POV。",
                                    }
                                }
                                if feedback_challenge
                                else {}
                            ),
                        },
                        ensure_ascii=False,
                    ),
                }
            )
            binding = [
                base,
                second[0],
                [d.source_ref for d in first["decisions"].values()],
                [d.source_ref for d in reviewed["decisions"].values()],
                *([feedback.fingerprint()] if feedback_challenge else []),
            ]
            third = stage(
                f"adjudication:{round_index + 1}", adjudication, check_proposal, binding=binding
            )
            if third is None:
                pending = {
                    q: replace(d, admissible=False)
                    if q in set(differences) | feedback_targets
                    else d
                    for q, d in first["decisions"].items()
                }
                payload, approvals = decision_payload(
                    task, pending, breaks=original_breaks, anonymous=first["anonymous"]
                )
                compile_expression_output(payload, task, owner_approvals=approvals)
                result.raw = ReviewedExpression(
                    payload, approvals=approvals, task_fingerprint=task.fingerprint()
                )
                result.raw.restored_attempt = getattr(primary_raw, "restored_attempt", False)
                break
            anonymous.update(third[1]["anonymous"])
        third_decisions = third[1]["decisions"] if third else {}
        requested = agreed_challenges(first["decisions"], reviewed["decisions"], third_decisions)
        verified = {}
        if requested:
            verification = build_challenge_messages(
                task,
                requested=requested,
                original=base,
                reviewed=second[0],
                challenger=third[0],
            )

            def check_verification(
                payload,
                run_id,
                first=first,
                reviewed=reviewed,
                third_decisions=third_decisions,
                requested=requested,
                anonymous=anonymous,
            ):
                return verify_payload(
                    payload,
                    task,
                    first["decisions"],
                    reviewed["decisions"],
                    third_decisions,
                    requested=requested,
                    verifier_ref=run_id,
                    anonymous=anonymous,
                    breaks=original_breaks,
                )

            verified = (
                stage(
                    f"verification:{round_index + 1}",
                    verification,
                    check_verification,
                    binding=[binding, third[0], [d.source_ref for d in third_decisions.values()]],
                )
                or {}
            )
        resolved, reasons = reconcile(
            first["decisions"],
            reviewed["decisions"],
            adjudicated=third_decisions,
            verified=verified,
            task_fingerprint=task.fingerprint(),
        )
        payload, approvals = decision_payload(
            task, resolved, breaks=original_breaks, anonymous=anonymous
        )
        compile_expression_output(payload, task, owner_approvals=approvals)
        result.raw = ReviewedExpression(
            payload, approvals=approvals, task_fingerprint=task.fingerprint()
        )
        result.raw.restored_attempt = getattr(primary_raw, "restored_attempt", False)
        first = proposal_snapshot(
            payload,
            task,
            call_ref=f"resolved:{primary_run_id}:{round_index}",
            owner_approvals=approvals,
        )
        base = payload
        with session_factory() as session:
            job = session.get(Job, job_id)
            checkpoint = json.loads(job.checkpoint_json or "{}")
            checkpoint.setdefault("expression_reviews", {})[window.window_id][
                "rounds_completed"
            ] = round_index + 1
            checkpoint.setdefault("expression_review_reasons", {})[window.window_id] = reasons
            job.checkpoint_json = json.dumps(checkpoint, ensure_ascii=False)
            session.commit()
    if isinstance(result.raw, ReviewedExpression):
        result.raw.auxiliary_warnings = diagnostic_warnings[-5:]
    return result
