"""Explicit bounded roster repair, with request-bound receipts and recovery."""

import asyncio
import json
import threading
import time
from contextlib import suppress
from copy import deepcopy

from sqlalchemy import select

from ..characters.service import complete_textless_chapter, store_roster_candidates
from ..context.budget import estimate_tokens
from ..domain.enums import InferenceRunState, JobKind, JobState
from ..llm.errors import ProviderError, ProviderErrorKind
from ..llm.isolated_roster_repair import (
    ISOLATED_REPAIR_POLICY,
    compile_isolated_roster_repair,
    prepare_isolated_roster_repair,
)
from ..llm.prompts.roster_repair import build_roster_repair_messages
from ..llm.roster_repair import REPAIR_VERSION, compile_roster_repair, prepare_roster_repair
from ..llm.sourced_roster import (
    SOURCED_ROSTER_VERSION,
    IsolatedRosterFailure,
    SourcedRosterOutput,
    compile_isolated_sourced_roster,
)
from ..storage.models import BookVersion, Chapter, InferenceRun, Job
from ..storage.run_archive import decode_archive, save_run_archive

_ACTIVE = set()
_GUARD = threading.Lock()


class _Halt(Exception):
    pass


class _Invalid(Exception):
    pass


def _clean(raw):
    if not isinstance(raw, dict):
        raise ValueError("人物提案必须为对象")
    return {k: deepcopy(v) for k, v in raw.items() if not str(k).startswith("_")}


def _limit(budget, key, default):
    value = budget.get(key, default)
    if type(value) is not int or not 0 <= value <= 5:
        raise ValueError(f"{key}必须为0至5的整数")
    return value


class _Pipeline:
    def __init__(self, session_factory, settings, outcome):
        self.sessions, self.settings, self.outcome = session_factory, settings, outcome
        self.job_id = outcome.job_id

    def _progress(self, session, job, stage, **extra):
        calls = len(
            list(
                session.scalars(
                    select(InferenceRun.id).where(
                        InferenceRun.job_id == self.job_id,
                    )
                )
            )
        )
        job.progress_json = json.dumps(
            {"stage": stage, "calls": calls, **extra}, ensure_ascii=False
        )

    def halt(self, state, message, stage=None):
        with self.sessions() as session:
            job = session.get(Job, self.job_id)
            checkpoint = json.loads(job.checkpoint_json or "{}")
            if stage:
                checkpoint.setdefault("roster_pipeline", {})["failed_stage"] = stage
            job.checkpoint_json = json.dumps(checkpoint, ensure_ascii=False)
            job.state, job.last_error = state, message
            self._progress(session, job, state.value.lower())
            session.commit()
        self.outcome.state = state
        if message:
            self.outcome.errors.append(message)
        raise _Halt

    def check_stop(self):
        with self.sessions() as session:
            job = session.get(Job, self.job_id)
            if job is None:
                self.outcome.state = JobState.FAILED
                self.outcome.errors.append("job_not_found")
                raise _Halt
            state = job.state
        if state is JobState.PAUSING:
            self.halt(JobState.PAUSED, None)
        if state not in {JobState.QUEUED, JobState.RUNNING}:
            self.outcome.state = state
            raise _Halt

    def _settle(self, run_id, request, raw, error, elapsed):
        from .roster import _attempt_usage_json

        with self.sessions() as session:
            run = session.get(InferenceRun, run_id)
            save_run_archive(
                run, request, raw=raw, error=error, elapsed_ms=elapsed, phase="returned"
            )
            run.usage_json = _attempt_usage_json(raw, error)
            run.elapsed_ms = elapsed
            run.state = InferenceRunState.SUCCEEDED if error is None else InferenceRunState.FAILED
            run.error_code = error.code.value if isinstance(error, ProviderError) else None
            if error is not None and (
                not isinstance(error, ProviderError)
                or error.kind in {ProviderErrorKind.TIMEOUT, ProviderErrorKind.UNKNOWN_OUTCOME}
            ):
                run.state = InferenceRunState.UNKNOWN_OUTCOME
                run.error_code = "UNKNOWN_OUTCOME"
            session.commit()

    def attempt(self, stage, request, validator):
        """Reuse one exact stage receipt; never resend an unaccounted attempt."""
        from .scheduler import _request_fingerprint

        self.check_stop()
        with self.sessions() as session:
            job = session.get(Job, self.job_id)
            checkpoint = json.loads(job.checkpoint_json or "{}")
            entry = checkpoint["roster_pipeline"]
            generation = entry.get("retry_stages", {}).get(stage, 0)
            request = {
                **deepcopy(request),
                "roster_stage": stage,
                "roster_generation": generation,
                "roster_binding": self.binding,
            }
            fp = _request_fingerprint(request, self.snapshot)
            runs = list(session.scalars(select(InferenceRun).where(InferenceRun.job_id == job.id)))
            matching = [run for run in runs if run.request_fingerprint == fp]
            if len(matching) > 1:
                session.close()
                self.halt(JobState.FAILED, "同一人物阶段存在重复调用记录，已停止恢复", stage)
            if matching:
                run = matching[0]
                try:
                    record = decode_archive(run.call_archive)
                    if record.get("phase") != "returned" or record.get("request") != request:
                        raise ValueError("人物调用尚无完整返回凭据")
                except ValueError as exc:
                    session.close()
                    self.halt(JobState.NEEDS_RECONCILIATION, str(exc), stage)
                raw, run_id = record.get("adapter_result"), run.id
                error_data = record.get("error")
                error = None
                if error_data:
                    try:
                        error = ProviderError(
                            ProviderErrorKind(error_data["kind"]),
                            error_data["message"],
                            details=error_data.get("details"),
                        )
                    except (KeyError, ValueError):
                        session.close()
                        self.halt(
                            JobState.NEEDS_RECONCILIATION,
                            "人物失败调用缺少完整凭据，不能自动重发",
                            stage,
                        )
                restored = True
            else:
                unknown, spent = False, 0
                for run in runs:
                    usage = json.loads(run.usage_json or "{}")
                    if type(usage.get("total_tokens")) is int and not usage.get("unknown"):
                        spent += usage["total_tokens"]
                    else:
                        unknown = True
                        try:
                            spent += decode_archive(run.call_archive)["request"]["reserve_tokens"]
                        except (ValueError, KeyError, TypeError):
                            session.close()
                            self.halt(
                                JobState.NEEDS_RECONCILIATION,
                                "历史人物调用消耗与预留不完整，请先核对",
                                stage,
                            )
                if unknown and not generation:
                    session.close()
                    self.halt(
                        JobState.NEEDS_RECONCILIATION,
                        "人物调用消耗未知，不能自动追加修复调用",
                        stage,
                    )
                limit = json.loads(job.budget_json or "{}").get("max_input_tokens")
                if limit is not None and spent + request["reserve_tokens"] > limit:
                    session.close()
                    self.halt(JobState.BUDGET_EXHAUSTED, "剩余Token额度不足以执行人物阶段", stage)
                entry["failed_stage"] = stage
                job.checkpoint_json = json.dumps(checkpoint, ensure_ascii=False)
                job.state = JobState.RUNNING
                run = InferenceRun(
                    job_id=job.id,
                    window_id=f"roster:{self.chapter_id}",
                    profile_snapshot_json=job.profile_snapshot_json,
                    request_fingerprint=fp,
                    state=InferenceRunState.DISPATCHED,
                )
                session.add(run)
                session.flush()
                save_run_archive(run, request)
                run_id = run.id
                self._progress(session, job, stage)
                session.commit()
                restored = False
        if not restored:
            started = time.monotonic()
            raw, error = None, None
            self.outcome.calls += 1
            try:
                raw = asyncio.run(self.adapter.generate_labels(deepcopy(request)))
            except Exception as exc:  # noqa: BLE001 - preserve dispatched unknown calls
                error = exc
            self._settle(run_id, request, raw, error, int((time.monotonic() - started) * 1000))
        self.check_stop()
        if error is not None:
            if not isinstance(error, ProviderError) or error.kind in {
                ProviderErrorKind.TIMEOUT,
                ProviderErrorKind.UNKNOWN_OUTCOME,
            }:
                self.halt(JobState.NEEDS_RECONCILIATION, str(error), stage)
            if error.kind is not ProviderErrorKind.INVALID_OUTPUT:
                self.halt(JobState.FAILED, error.message, stage)
            failure = error.message
        else:
            try:
                value = validator(_clean(raw), run_id)
            except ValueError as exc:
                failure = str(exc)
            else:
                with self.sessions() as session:
                    run = session.get(InferenceRun, run_id)
                    run.state, run.error_code = InferenceRunState.SUCCEEDED, None
                    session.commit()
                return value, run_id
        with self.sessions() as session:
            run = session.get(InferenceRun, run_id)
            run.state, run.error_code = InferenceRunState.FAILED, "INVALID_MODEL_OUTPUT"
            usage = json.loads(run.usage_json or "{}")
            known = type(usage.get("total_tokens")) is int and not usage.get("unknown")
            session.commit()
        if not known:
            self.halt(
                JobState.NEEDS_RECONCILIATION, "人物输出校验失败且消耗未知，不能自动重试", stage
            )
        raise _Invalid(failure)

    def request(self, messages, **extra):
        output_limit = int((self.snapshot.get("params") or {}).get("max_tokens", 2000))
        if output_limit <= 0:
            raise ValueError("人物调用输出上限必须大于0")
        return {
            "task": "roster",
            "roster_protocol": SOURCED_ROSTER_VERSION,
            "messages": deepcopy(messages),
            "max_tokens": 2000,
            "json_object": True,
            "target_quote_ids": [],
            "reserve_tokens": sum(estimate_tokens(m["content"]) for m in messages) + output_limit,
            **({"roster_repair_policy": self.repair_policy}
               if self.repair_policy is not None else {}),
            **extra,
        }

    def execute(self, credentials, adapter_factory):
        from .roster import _build_adapter, _job_range, _prepare_input, chapter_has_body_text
        from .scheduler import _request_fingerprint

        self.check_stop()
        with self.sessions() as session:
            job = session.get(Job, self.job_id)
            version = session.get(BookVersion, job.book_version_id)
            scope = _job_range(job)
            self.repair_policy = scope.get("roster_repair_policy")
            if self.repair_policy not in (None, ISOLATED_REPAIR_POLICY):
                raise ValueError("人物修复策略不受支持")
            chapter = session.get(Chapter, str(scope.get("chapter_id", "")))
            if (
                job.kind is not JobKind.CHARACTER_ROSTER
                or version is None
                or chapter is None
                or chapter.book_version_id != version.id
            ):
                session.close()
                self.halt(JobState.FAILED, "人物分析任务缺少有效章节")
            if scope.get("roster_protocol") != SOURCED_ROSTER_VERSION:
                session.close()
                self.halt(JobState.FAILED, "定向人物修复必须使用sourced-roster-2")
            if not chapter_has_body_text(session, self.settings, version, chapter):
                roster = complete_textless_chapter(session, chapter)
                roster.analysis_job_id = job.id
                job.state = JobState.COMPLETED
                self._progress(
                    session, job, "completed", candidate_count=0, skipped_reason="no_text"
                )
                session.commit()
                self.outcome.state = JobState.COMPLETED
                return
            self.snapshot = json.loads(job.profile_snapshot_json or "{}")
            self.chapter_id = chapter.id
            budget = json.loads(job.budget_json or "{}")
            total_limit = budget.get("max_input_tokens")
            if total_limit is not None and (type(total_limit) is not int or total_limit < 0):
                raise ValueError("人物Token额度必须为空或非负整数")
            repair_limit = _limit(budget, "max_roster_repairs", 1)
            format_limit = _limit(budget, "max_format_retries", 1)
            messages, original, _, allowed = _prepare_input(
                session, self.settings, job, version, chapter
            )
            initial_request = self.request(messages)
            self.binding = _request_fingerprint(
                {
                    "request": initial_request,
                    "original_sha256": original.canonical_sha256,
                    "chapter_range": [chapter.start_cp, chapter.end_cp],
                    "repair_limit": repair_limit,
                    "format_limit": format_limit,
                },
                self.snapshot,
            )
            checkpoint = json.loads(job.checkpoint_json or "{}")
            entry = checkpoint.setdefault("roster_pipeline", {"binding": self.binding})
            if entry.get("binding") != self.binding:
                session.close()
                self.halt(JobState.FAILED, "人物、原文或模型输入已改变，不能恢复旧提案")
            job.checkpoint_json = json.dumps(checkpoint, ensure_ascii=False)
            session.commit()
            self.adapter = (
                adapter_factory(job, self.snapshot)
                if adapter_factory
                else _build_adapter(self.settings, credentials, self.snapshot)
            )
            compile_args = {
                "original": original,
                "chapter_start": chapter.start_cp,
                "chapter_end": chapter.end_cp,
                "allowed_character_ids": allowed,
            }

        def initial(payload, run_id):
            try:
                output, facts, diagnostic = compile_isolated_sourced_roster(
                    payload, source_ref=run_id, **compile_args
                )
            except IsolatedRosterFailure as exc:
                output, facts, diagnostic = SourcedRosterOutput(characters=[]), {}, exc.diagnostics
            prepare = (prepare_isolated_roster_repair
                       if self.repair_policy else prepare_roster_repair)
            plan = prepare(payload, source_ref=run_id, **compile_args)
            return output, facts, diagnostic, plan

        feedback = None
        for index in range(format_limit + 1):
            current_messages = (
                messages
                if feedback is None
                else [
                    *messages,
                    {
                        "role": "user",
                        "content": "上次人物提案未通过协议校验，请更正：" + feedback[:1000],
                    },
                ]
            )
            try:
                (output, facts, diagnostic, plan), primary_id = self.attempt(
                    f"primary:{index}", self.request(current_messages), initial
                )
                break
            except _Invalid as exc:
                feedback = str(exc)
        else:
            self.halt(JobState.FAILED, "人物主协议格式重试已耗尽：" + feedback[:400])

        repair_error = None
        repaired = False
        repair_steps = []
        if plan.groups:
            for index in range(repair_limit):
                task = plan.task_payload()
                if repair_error:
                    task["previous_error"] = repair_error[:1000]
                repair_messages = build_roster_repair_messages(messages, task)
                try:
                    value, _ = self.attempt(
                        f"repair:{index}",
                        self.request(
                            repair_messages,
                            roster_repair_protocol=REPAIR_VERSION,
                            roster_primary_run_id=primary_id,
                            **({"roster_repair_binding": plan.fingerprint()}
                               if self.repair_policy else {}),
                        ),
                        lambda raw, run_id, plan=plan: (
                            compile_isolated_roster_repair if self.repair_policy
                            else compile_roster_repair
                        )(
                            plan, raw, original=original, source_ref=run_id
                        ),
                    )
                except _Invalid as exc:
                    repair_error = str(exc)
                else:
                    if self.repair_policy:
                        output, facts, plan, partial = value
                        repair_steps.append(partial)
                        repaired = not plan.groups
                        repair_error = None if repaired else "部分身份仍未通过校验"
                    else:
                        output, facts = value
                        repaired = True
                        repair_error = None
                    if repaired:
                        break
        diagnostic = {
            **diagnostic,
            "repair_succeeded": repaired,
            "unresolved_groups": [] if repaired else [list(group) for group in plan.groups],
            "repair_error": repair_error,
            **({"repair_policy": self.repair_policy, "repair_steps": repair_steps}
               if self.repair_policy else {}),
        }
        if not output.characters and plan.groups:
            self.halt(
                JobState.FAILED, "人物修复后仍无有效身份：" + (repair_error or "修复次数已耗尽")
            )
        self.check_stop()
        with self.sessions() as session:
            job = session.get(Job, self.job_id)
            if job.state not in {JobState.QUEUED, JobState.RUNNING}:
                session.close()
                self.check_stop()
            version = session.get(BookVersion, job.book_version_id)
            chapter = session.get(Chapter, self.chapter_id)
            with session.begin_nested():
                roster = store_roster_candidates(
                    session,
                    version=version,
                    chapter=chapter,
                    output=output,
                    job_id=job.id,
                    allow_overwrite_manual=bool(
                        _job_range(job).get("allow_overwrite_manual", False)
                    ),
                    identity_facts=facts,
                    original=original,
                )
            checkpoint = json.loads(job.checkpoint_json or "{}")
            checkpoint.update(
                roster_version=roster.version,
                candidate_count=len(output.characters),
                proposal_diagnostics=diagnostic,
            )
            checkpoint["roster_pipeline"].pop("failed_stage", None)
            job.checkpoint_json = json.dumps(checkpoint, ensure_ascii=False)
            job.state, job.last_error = JobState.COMPLETED, None
            self._progress(
                session,
                job,
                "completed",
                candidate_count=len(output.characters),
                proposal_diagnostics=diagnostic,
            )
            session.commit()
        self.outcome.state = JobState.COMPLETED


def run_repaired_roster_job(
    session_factory, settings, *, job_id, credentials=None, adapter_factory=None
):
    from .roster import RosterJobOutcome

    outcome = RosterJobOutcome(job_id=job_id, state=JobState.RUNNING)
    with _GUARD:
        if job_id in _ACTIVE:
            return outcome
        _ACTIVE.add(job_id)
    pipeline = _Pipeline(session_factory, settings, outcome)
    try:
        pipeline.execute(credentials, adapter_factory)
    except _Halt:
        pass
    except Exception as exc:  # noqa: BLE001 - preserve receipts and rollback candidate writes
        with suppress(_Halt):
            pipeline.halt(JobState.FAILED, f"人物修复任务失败：{str(exc)[:400]}")
    finally:
        with _GUARD:
            _ACTIVE.discard(job_id)
    return outcome
