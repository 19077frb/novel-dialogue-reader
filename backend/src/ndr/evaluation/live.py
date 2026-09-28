"""真实运行：把清单里的正文导入本地库、用指定模型配置跑任务、再转成预测。

只有显式 `--allow-live` 才会走到这里：真实提供方需要凭据与预算，失败时如实返回错误码，
绝不回退到 FakeProvider，也不伪造成功结果。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from ..config import Settings
from ..context.budget import policy_for_version
from ..domain.enums import JobKind, JobPurpose, JobState, ReadingMode
from ..ingest.service import import_epub, import_txt
from ..jobs.scheduler import run_job
from ..jobs.service import create_inference_job, profile_for_job
from ..llm.credentials import CredentialService, SystemCredentialStore
from ..scenes.projection import build_projection
from ..storage.engine import create_db_engine, create_session_factory
from ..storage.migrate import run_migrations
from ..storage.models import BookVersion
from ..storage.transactions import transaction

LIVE_BLOCKED_NO_ALLOW = "真实运行需要显式 --allow-live（并确保已配置凭据与预算）"
LIVE_BLOCKED_NO_PROFILE = "真实运行缺少 --profile-id：需要一个已保存的模型配置"
LIVE_UNSUPPORTED = "该配置没有可用的真实运行路径"


def run_live_predictions(
    *,
    settings: Settings,
    book_path: Path,
    profile_id: str,
    config_id: str,
    reading_mode: ReadingMode = ReadingMode.REREAD,
    context_policy: str | None = None,
    recheck_max_targets: int = 0,
    allow_live: bool = False,
) -> tuple[dict[str, Any] | None, str, str]:
    """返回 ``(prediction, state, reason)``；state 为 COMPLETED / NOT_RUN / LIVE_FAILED。

    ``context_policy`` 与 ``recheck_max_targets`` 来自评测配置（B3 = 只有压缩；
    B4 = 压缩 + 有限复核）。同一次消融里除这几项之外必须完全一致，否则数字不可比。
    """

    policy = replace(
        policy_for_version(context_policy),
        recheck_max_targets=max(0, int(recheck_max_targets)),
    )

    if not allow_live:
        return None, "NOT_RUN", LIVE_BLOCKED_NO_ALLOW
    if not profile_id:
        return None, "NOT_RUN", LIVE_BLOCKED_NO_PROFILE

    run_migrations(settings)
    engine = create_db_engine(settings)
    factory = create_session_factory(engine)
    credentials = CredentialService(
        system=SystemCredentialStore(enabled=settings.credential_backend.lower() != "session")
    )
    try:
        with transaction(factory) as session:
            raw = Path(book_path).read_bytes()
            if Path(book_path).suffix.lower() == ".epub":
                outcome = import_epub(session, settings, filename=Path(book_path).name, raw=raw)
            else:
                outcome = import_txt(session, settings, filename=Path(book_path).name, raw=raw)
            book_id = outcome.book.id
            version_id = outcome.version.id
            version = session.get(BookVersion, version_id)
            assert version is not None
            from ..storage.models import ModelProfile

            profile = session.get(ModelProfile, profile_id)
            if profile is None:
                return None, "NOT_RUN", f"模型配置不存在：{profile_id}"
            job, _created = create_inference_job(
                session,
                book=outcome.book,
                version=version,
                profile=profile,
                purpose=JobPurpose.PROCESS,
                range_payload={
                    "start_cp": 0,
                    "end_cp": version.canonical_length_cp,
                    "context_policy": context_policy,
                    "recheck_max_targets": policy.recheck_max_targets,
                },
                budget={"max_input_tokens": None, "max_output_tokens": None, "max_rechecks": 0},
                idempotency_key=f"evaluation:{config_id}:{book_id}",
                reading_mode=reading_mode,
                visible_horizon_cp=None,
                kind=JobKind.INFERENCE,
            )
            job_id = job.id

        job_outcome = run_job(
            factory,
            settings,
            job_id=job_id,
            credentials=credentials,
            policy=policy,
        )
        if job_outcome.state is not JobState.COMPLETED:
            return None, "LIVE_FAILED", f"任务结束于 {job_outcome.state.value}"

        with transaction(factory) as session:
            version = session.get(BookVersion, version_id)
            assert version is not None
            projection = build_projection(
                session,
                book_id=book_id,
                book_version_id=version_id,
                start_cp=0,
                end_cp=version.canonical_length_cp,
                reading_mode=reading_mode,
                visible_horizon_cp=None,
            )
            job = session.get(__import__("ndr.storage.models", fromlist=["Job"]).Job, job_id)
            profile = profile_for_job(session, job) if job is not None else None
            provider = profile.protocol if profile is not None else "unknown"

        quotes = [
            {
                "quote_id": item.quote_id,
                "start_cp": item.start_cp,
                "end_cp": item.end_cp,
                "kind": item.kind.value,
                "scene_key": item.scene_id,
                "group_key": item.label,
                "status": item.status.value,
            }
            for item in projection.items
        ]
        usage = dict(job_outcome.usage or {})
        usage["provider"] = provider
        return (
            {
                "book_id": book_id,
                "provider": provider,
                "quality_evidence": provider != "fake-provider",
                "quotes": quotes,
                "usage": usage,
            },
            "COMPLETED",
            "",
        )
    finally:
        engine.dispose()
