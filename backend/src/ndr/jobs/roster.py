"""Chapter character-roster job runner."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..characters.facts import OriginalIdentitySnapshot
from ..characters.names import valid_display_name
from ..characters.service import (
    chapter_has_body_text,
    complete_textless_chapter,
    roster_messages,
    store_roster_candidates,
)
from ..config import Settings
from ..context.budget import estimate_tokens
from ..domain.enums import CredentialMode, InferenceRunState, JobKind, JobState
from ..ingest.query import load_canonical_text
from ..llm.adapters import AdapterSpec, build_adapter
from ..llm.errors import ProviderError, ProviderErrorKind
from ..llm.schemas import RosterOutput
from ..llm.sourced_roster import (
    SOURCED_ROSTER_VERSION,
    SOURCED_ROSTER_VERSIONS,
    compile_isolated_sourced_roster,
    compile_sourced_roster,
)
from ..storage.models import BookVersion, Chapter, InferenceRun, Job

ROSTER_MAX_TOKENS = 2000
_ACTIVE = set()
_GUARD = threading.Lock()
logger = logging.getLogger(__name__)


@dataclass
class RosterJobOutcome:
    job_id: str
    state: JobState
    calls: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "state": self.state.value,
            "calls": self.calls,
            "errors": list(self.errors),
        }


def _job_range(job: Job) -> dict[str, Any]:
    try:
        value = json.loads(job.range_json or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _attempt_usage_json(raw: Any, error: Exception | None = None) -> str | None:
    """Keep adapter-reported charges even when the proposal fails validation."""
    usage = raw.get("_usage") if isinstance(raw, dict) else None
    if usage is None and isinstance(error, ProviderError):
        usage = error.details.get("usage")
    if not isinstance(usage, dict) or usage.get("unknown"):
        return None
    counts = [usage.get(key) for key in ("input_tokens", "output_tokens", "total_tokens")]
    if not any(value is not None for value in counts) or any(
        value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0)
        for value in counts
    ):
        return None
    return json.dumps(usage, ensure_ascii=False)


def _build_adapter(settings: Settings, credentials, snapshot: dict[str, Any]):  # noqa: ANN001
    profile_id = str(snapshot.get("profile_id", ""))
    credential_mode = CredentialMode(
        str(snapshot.get("credential_mode", CredentialMode.NONE.value))
    )
    credential_ref = (
        f"model-profile/{profile_id}"
        if credential_mode is not CredentialMode.NONE
        else None
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


def _prepare_input(session, settings, job, version, chapter):
    source_text = load_canonical_text(settings, version)
    protocol = _job_range(job).get("roster_protocol")
    sourced = protocol in SOURCED_ROSTER_VERSIONS
    original = (OriginalIdentitySnapshot(version.id, version.canonical_sha256, source_text)
                if sourced else None)
    messages = roster_messages(session, settings, job, version, chapter,
                               canonical_text=source_text)
    request_data = json.loads(messages[1]["content"].split("任务参数（JSON）：\n", 1)[1]
                              .split("\n\n", 1)[0])
    allowed_ids = {item["character_id"] for item in request_data["existing_characters"]}
    return messages, original, protocol if sourced else "legacy-roster-1", allowed_ids


def run_character_roster_job(
    session_factory, settings, *, job_id, credentials=None, adapter_factory=None,
) -> RosterJobOutcome:
    """Always settle unexpected worker failures; serialize re-entry for one job."""
    with _GUARD:
        if job_id in _ACTIVE:
            with session_factory() as session:
                job = session.get(Job, job_id)
                return RosterJobOutcome(job_id, job.state if job else JobState.FAILED)
        _ACTIVE.add(job_id)
    try:
        with session_factory() as session:
            job = session.get(Job, job_id)
            if job is not None and job.state is JobState.PAUSING:
                job.state = JobState.PAUSED
                session.commit()
            if job is not None and job.state in {JobState.COMPLETED, JobState.PAUSED}:
                return RosterJobOutcome(job_id, job.state)
        return _run_character_roster_job(
            session_factory, settings, job_id=job_id, credentials=credentials,
            adapter_factory=adapter_factory,
        )
    except Exception as exc:  # noqa: BLE001 - background exceptions must not leave RUNNING
        from .roster_receipts import RosterReceiptError

        logger.exception("人物任务收尾异常 job_id=%s", job_id)
        state = (JobState.NEEDS_RECONCILIATION if isinstance(exc, RosterReceiptError)
                 else JobState.FAILED)
        with session_factory() as session:
            job = session.get(Job, job_id)
            if job is not None:
                if job.state == JobState.COMPLETED:
                    return RosterJobOutcome(job_id, job.state)
                if not isinstance(exc, RosterReceiptError):
                    from ..storage.run_archive import decode_archive

                    attempt = session.scalar(select(InferenceRun).where(
                        InferenceRun.job_id == job_id,
                    ).order_by(InferenceRun.created_at.desc(), InferenceRun.id.desc()).limit(1))
                    if attempt is not None and attempt.state is InferenceRunState.DISPATCHED:
                        try:
                            receipt = decode_archive(attempt.call_archive)
                            returned = (receipt.get("phase") == "returned"
                                        and isinstance(receipt.get("adapter_result"), dict))
                        except ValueError:
                            returned = False
                        if returned:
                            attempt.state = InferenceRunState.FAILED
                            attempt.error_code = "ROSTER_FINALIZATION_FAILED"
                            attempt.usage_json = _attempt_usage_json(receipt["adapter_result"])
                            attempt.elapsed_ms = receipt.get("elapsed_ms")
                        else:
                            state = JobState.NEEDS_RECONCILIATION
                            attempt.state = InferenceRunState.UNKNOWN_OUTCOME
                            attempt.error_code = "UNKNOWN_OUTCOME"
                job.state = state
                job.last_error = (str(exc) if isinstance(exc, RosterReceiptError) else
                                  f"人物任务收尾失败（{type(exc).__name__}）；重试将优先恢复已保存的返回")
                calls = len(list(session.scalars(select(InferenceRun.id).where(
                    InferenceRun.job_id == job_id,
                ))))
                job.progress_json = json.dumps({
                    "stage": state.value.lower(), "calls": calls,
                    "receipt_recovery_blocked": isinstance(exc, RosterReceiptError),
                })
                session.commit()
        return RosterJobOutcome(job_id, state, errors=[type(exc).__name__])
    finally:
        with _GUARD:
            _ACTIVE.discard(job_id)


def _run_character_roster_job(
    session_factory: sessionmaker[Session],
    settings: Settings,
    *,
    job_id: str,
    credentials=None,  # noqa: ANN001
    adapter_factory=None,  # noqa: ANN001
) -> RosterJobOutcome:
    outcome = RosterJobOutcome(job_id=job_id, state=JobState.QUEUED)

    with session_factory() as session:
        job = session.get(Job, job_id)
        use_repair = (
            job is not None
            and _job_range(job).get("roster_repair_protocol") == "roster-repair-1"
        )
    if use_repair:
        from .roster_pipeline import run_repaired_roster_job
        return run_repaired_roster_job(
            session_factory, settings, job_id=job_id, credentials=credentials,
            adapter_factory=adapter_factory,
        )

    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None or job.kind is not JobKind.CHARACTER_ROSTER:
            outcome.state = JobState.FAILED
            outcome.errors.append("job_not_found")
            return outcome
        version = session.get(BookVersion, job.book_version_id)
        chapter_id = str(_job_range(job).get("chapter_id", ""))
        chapter = session.get(Chapter, chapter_id)
        if version is None or chapter is None or chapter.book_version_id != version.id:
            job.state = JobState.FAILED
            job.last_error = "人物分析任务缺少有效章节"
            session.commit()
            outcome.state = JobState.FAILED
            outcome.errors.append("invalid_chapter")
            return outcome

        if not chapter_has_body_text(session, settings, version, chapter):
            roster = complete_textless_chapter(session, chapter)
            roster.analysis_job_id = job.id
            job.state = JobState.COMPLETED
            job.last_error = None
            job.progress_json = json.dumps({
                "stage": "completed", "calls": 0, "candidate_count": 0,
                "skipped_reason": "no_text", "message": "本章没有正文文字，已完成，无需人物识别",
            }, ensure_ascii=False)
            job.checkpoint_json = job.progress_json
            session.commit()
            outcome.state = JobState.COMPLETED
            return outcome

        chapter_start, chapter_end = chapter.start_cp, chapter.end_cp
        try:
            messages, original, protocol, allowed_character_ids = _prepare_input(
                session, settings, job, version, chapter,
            )
        except Exception as exc:  # noqa: BLE001 - no call or partial candidate writes
            job.state = JobState.FAILED
            job.last_error = f"人物分析输入准备失败：{exc}"
            job.progress_json = json.dumps({"stage": "failed", "calls": 0}, ensure_ascii=False)
            session.commit()
            outcome.state = JobState.FAILED
            outcome.errors.append("invalid_roster_input")
            return outcome
        from .roster_receipts import returned_attempt

        restored = returned_attempt(session, job, chapter.id, messages, protocol)
        snapshot = json.loads(job.profile_snapshot_json or "{}")
        if not restored:
            if adapter_factory is not None:
                adapter = adapter_factory(job, snapshot)
            else:
                try:
                    adapter = _build_adapter(settings, credentials, snapshot)
                except ProviderError as exc:
                    job.state = JobState.FAILED
                    job.last_error = f"{exc.code.value}: {exc.message}"
                    session.commit()
                    outcome.state = JobState.FAILED
                    outcome.errors.append(exc.code.value)
                    return outcome
        budget = json.loads(job.budget_json or "{}")
        max_input_tokens = budget.get("max_input_tokens")
        estimated_tokens = sum(estimate_tokens(message["content"]) for message in messages)
        if (not restored and max_input_tokens is not None
                and estimated_tokens + ROSTER_MAX_TOKENS > int(max_input_tokens)):
            job.state = JobState.BUDGET_EXHAUSTED
            job.last_error = "剩余 Token 额度不足以分析本章人物"
            job.progress_json = json.dumps({"stage": "budget_exhausted"}, ensure_ascii=False)
            session.commit()
            outcome.state = JobState.BUDGET_EXHAUSTED
            return outcome
        fingerprint = hashlib.sha256(
            json.dumps(messages, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        job.state = JobState.RUNNING
        job.progress_json = json.dumps({"stage": "running", "calls": 0}, ensure_ascii=False)
        run = restored[0] if restored else InferenceRun(
            job_id=job.id,
            window_id=f"roster:{chapter.id}",
            profile_snapshot_json=job.profile_snapshot_json or "{}",
            request_fingerprint=fingerprint,
            state=InferenceRunState.DISPATCHED,
        )
        session.add(run)
        session.commit()
        run_id = run.id
        chapter_id_value = chapter.id

    started = time.monotonic()
    request_payload = {
        "task": "roster", "roster_protocol": protocol, "messages": messages,
        "max_tokens": ROSTER_MAX_TOKENS, "json_object": True, "target_quote_ids": [],
    }
    from ..storage.run_archive import save_run_archive
    if restored:
        _, archive, allowed_character_ids = restored
        request_payload = archive["request"]
        elapsed_ms = archive.get("elapsed_ms")
    else:
        with session_factory() as session:
            save_run_archive(session.get(InferenceRun, run_id), request_payload)
            session.commit()
    raw = None
    outcome.calls = 0 if restored else 1
    try:
        raw = (archive["adapter_result"] if restored else
               asyncio.run(adapter.generate_labels(deepcopy(request_payload))))
        elapsed_ms = elapsed_ms if restored else int((time.monotonic() - started) * 1000)
        with session_factory() as session:
            run = session.get(InferenceRun, run_id)
            if not restored:
                save_run_archive(run, request_payload, raw=raw,
                                 elapsed_ms=elapsed_ms, phase="returned")
            run.elapsed_ms = elapsed_ms
            run.usage_json = _attempt_usage_json(raw)
            session.commit()
        payload = {
            key: value
            for key, value in dict(raw).items()
            if not str(key).startswith("_")
        }
        identity_facts = None
        diagnostics = {}
        if protocol == SOURCED_ROSTER_VERSION:
            output, identity_facts, diagnostics = compile_isolated_sourced_roster(
                payload, original=original, chapter_start=chapter_start, chapter_end=chapter_end,
                allowed_character_ids=allowed_character_ids, source_ref=run_id,
            )
        elif protocol in SOURCED_ROSTER_VERSIONS:
            output, identity_facts = compile_sourced_roster(
                payload, original=original, chapter_start=chapter_start, chapter_end=chapter_end,
                allowed_character_ids=allowed_character_ids, source_ref=run_id,
            )
        else:
            output = RosterOutput.model_validate(payload)
        if any(person.character_id and person.character_id not in allowed_character_ids
               for person in output.characters):
            raise ValueError("人物引用了未提供的全书人物 ID")
        if any(person.character_id and not person.evidence_refs for person in output.characters):
            raise ValueError("关联已有全书人物必须提供原文证据")
        if any(not valid_display_name(person.name) for person in output.characters):
            raise ValueError("每个新人物必须填写简短 name（姓名或称呼），不能用描述或编号替代")
        if any(person.real_name and (
            not valid_display_name(person.real_name) or not person.evidence_refs)
               for person in output.characters):
            raise ValueError("真实姓名必须有原文证据且为简短姓名")
    except Exception as exc:  # noqa: BLE001
        unknown = isinstance(exc, ProviderError) and exc.kind in {
            ProviderErrorKind.TIMEOUT, ProviderErrorKind.UNKNOWN_OUTCOME,
        }
        with session_factory() as session:
            job = session.get(Job, job_id)
            run = session.get(InferenceRun, run_id)
            if run is not None:
                if not restored:
                    save_run_archive(run, request_payload, raw=raw, error=exc,
                                     elapsed_ms=int((time.monotonic() - started) * 1000),
                                     phase="returned")
                run.state = (InferenceRunState.UNKNOWN_OUTCOME if unknown
                             else InferenceRunState.FAILED)
                run.error_code = (exc.code.value if isinstance(exc, ProviderError)
                                  else "INVALID_MODEL_OUTPUT")
                if not restored:
                    run.elapsed_ms = int((time.monotonic() - started) * 1000)
                run.usage_json = _attempt_usage_json(raw, exc)
            if job is not None:
                job.state = JobState.NEEDS_RECONCILIATION if unknown else JobState.FAILED
                job.last_error = f"人物分析失败：{exc}"
                job.progress_json = json.dumps({"stage": job.state.value.lower(), "calls": 1})
                session.commit()
            outcome.state = JobState.NEEDS_RECONCILIATION if unknown else JobState.FAILED
            outcome.errors.append("invalid_model_output")
            return outcome

    with session_factory() as session:
        job = session.get(Job, job_id)
        version = session.get(BookVersion, job.book_version_id) if job is not None else None
        chapter = session.get(Chapter, chapter_id_value) if version is not None else None
        if job is None or version is None or chapter is None:
            outcome.state = JobState.FAILED
            outcome.errors.append("job_not_found")
            return outcome
        if job.state in {JobState.PAUSING, JobState.PAUSED, JobState.PARTIAL}:
            job.state = JobState.PAUSED
            job.progress_json = json.dumps({"stage": "paused", "calls": 1})
            session.commit()
            outcome.state = JobState.PAUSED
            return outcome
        try:
            with session.begin_nested():
                roster = store_roster_candidates(
                    session, version=version, chapter=chapter, output=output, job_id=job.id,
                    allow_overwrite_manual=bool(
                        _job_range(job).get("allow_overwrite_manual", False)),
                    identity_facts=identity_facts, original=original,
                )
        except Exception as exc:  # noqa: BLE001 - persist charged storage failures
            run = session.get(InferenceRun, run_id)
            if run is not None:
                run.state = InferenceRunState.FAILED
                run.error_code = "ROSTER_STORAGE_FAILED"
                run.elapsed_ms = elapsed_ms
                run.usage_json = _attempt_usage_json(raw)
            job.state = JobState.FAILED
            job.last_error = f"人物保存失败：{exc}"
            job.progress_json = json.dumps({"stage": "failed", "calls": 1}, ensure_ascii=False)
            session.commit()
            outcome.state = JobState.FAILED
            outcome.errors.append("roster_storage_failed")
            return outcome
        run = session.get(InferenceRun, run_id)
        if run is not None:
            run.state = InferenceRunState.SUCCEEDED
            run.error_code = None
            run.elapsed_ms = elapsed_ms
            run.usage_json = _attempt_usage_json(raw)
        job.state = JobState.COMPLETED
        job.last_error = None
        job.checkpoint_json = json.dumps(
            {
                "roster_version": roster.version,
                "candidate_count": len(output.characters),
                "proposal_diagnostics": diagnostics,
                "calls": 1,
            },
            ensure_ascii=False,
        )
        job.progress_json = json.dumps(
            {
                "stage": "completed",
                "calls": 1,
                "candidate_count": len(output.characters),
                "proposal_diagnostics": diagnostics,
            },
            ensure_ascii=False,
        )
        session.commit()
        outcome.state = JobState.COMPLETED
    return outcome
