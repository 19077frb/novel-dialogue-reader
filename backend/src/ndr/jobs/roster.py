"""Chapter character-roster job runner."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ..characters.names import valid_display_name
from ..characters.service import (
    chapter_has_body_text,
    complete_textless_chapter,
    list_book_characters,
    roster_messages,
    store_roster_candidates,
)
from ..config import Settings
from ..context.budget import estimate_tokens
from ..domain.enums import CredentialMode, InferenceRunState, JobKind, JobState
from ..llm.adapters import AdapterSpec, build_adapter
from ..llm.errors import ProviderError
from ..llm.schemas import RosterOutput
from ..storage.models import BookVersion, Chapter, InferenceRun, Job

ROSTER_MAX_TOKENS = 2000


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


def run_character_roster_job(
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

        snapshot = json.loads(job.profile_snapshot_json or "{}")
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

        job.state = JobState.RUNNING
        job.progress_json = json.dumps({"stage": "running", "calls": 0}, ensure_ascii=False)
        session.commit()

        messages = roster_messages(session, settings, job, version, chapter)
        # IDs must have been present in this exact request, not merely exist
        # by the time a concurrent model call finishes.
        allowed_character_ids = {item.id for item in list_book_characters(session, version)}
        budget = json.loads(job.budget_json or "{}")
        max_input_tokens = budget.get("max_input_tokens")
        estimated_tokens = sum(estimate_tokens(message["content"]) for message in messages)
        if max_input_tokens is not None and estimated_tokens + ROSTER_MAX_TOKENS > int(
            max_input_tokens
        ):
            job.state = JobState.BUDGET_EXHAUSTED
            job.last_error = "剩余 Token 额度不足以分析本章人物"
            job.progress_json = json.dumps({"stage": "budget_exhausted"}, ensure_ascii=False)
            session.commit()
            outcome.state = JobState.BUDGET_EXHAUSTED
            return outcome
        fingerprint = hashlib.sha256(
            json.dumps(messages, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        run = InferenceRun(
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
    try:
        raw = asyncio.run(
            adapter.generate_labels(
                {
                    "task": "roster",
                    "messages": messages,
                    "max_tokens": ROSTER_MAX_TOKENS,
                    "json_object": True,
                    "target_quote_ids": [],
                }
            )
        )
        payload = {
            key: value
            for key, value in dict(raw).items()
            if not str(key).startswith("_")
        }
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
        with session_factory() as session:
            job = session.get(Job, job_id)
            run = session.get(InferenceRun, run_id)
            if run is not None:
                run.state = InferenceRunState.FAILED
                run.error_code = "INVALID_MODEL_OUTPUT"
                run.elapsed_ms = int((time.monotonic() - started) * 1000)
            if job is not None:
                job.state = JobState.FAILED
                job.last_error = f"人物分析失败：{exc}"
                job.progress_json = json.dumps({"stage": "failed"}, ensure_ascii=False)
                session.commit()
            outcome.state = JobState.FAILED
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
        roster = store_roster_candidates(
            session,
            version=version,
            chapter=chapter,
            output=output,
            job_id=job.id,
        )
        usage = raw.get("_usage") if isinstance(raw, dict) else None
        run = session.get(InferenceRun, run_id)
        if run is not None:
            run.state = InferenceRunState.SUCCEEDED
            run.elapsed_ms = int((time.monotonic() - started) * 1000)
            if isinstance(usage, dict) and not usage.get("unknown"):
                run.usage_json = json.dumps(usage, ensure_ascii=False)
        job.state = JobState.COMPLETED
        job.checkpoint_json = json.dumps(
            {
                "roster_version": roster.version,
                "candidate_count": len(output.characters),
                "calls": 1,
            },
            ensure_ascii=False,
        )
        job.progress_json = json.dumps(
            {
                "stage": "completed",
                "calls": 1,
                "candidate_count": len(output.characters),
            },
            ensure_ascii=False,
        )
        session.commit()
        outcome.state = JobState.COMPLETED
        outcome.calls = 1
    return outcome
