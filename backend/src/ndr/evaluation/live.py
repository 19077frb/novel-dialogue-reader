"""显式授权的章节评测：人物识别 → 自动确认 → 正常对白任务。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from ..characters.service import confirm_roster, create_roster_job, get_roster
from ..config import Settings
from ..domain.characters import RosterConfirmCandidateIn, RosterConfirmIn
from ..domain.enums import CharacterRosterStatus, JobPurpose, JobState, ReadingMode
from ..ingest.service import import_epub, import_txt
from ..jobs.scheduler import run_job
from ..jobs.service import create_inference_job, profile_snapshot, spent_tokens
from ..llm.credentials import CredentialService, SystemCredentialStore
from ..scenes.projection import build_projection
from ..storage.engine import create_db_engine, create_session_factory
from ..storage.migrate import run_migrations
from ..storage.models import Book, BookVersion, Chapter, InferenceRun, ModelProfile
from ..storage.transactions import transaction

LIVE_BLOCKED_NO_ALLOW = "真实运行需要显式 --allow-live（并确保已配置凭据与预算）"
LIVE_BLOCKED_NO_PROFILE = "真实运行缺少 --profile-id：需要一个已保存的模型配置"


def run_live_predictions(
    *,
    settings: Settings,
    book_path: Path,
    profile_id: str,
    config_id: str,
    reading_mode: ReadingMode = ReadingMode.REREAD,
    context_policy: str | None = None,
    budget: dict | None = None,
    inference_options: dict | None = None,
    allow_live: bool = False,
) -> tuple[dict[str, Any] | None, str, str]:
    """预算按每章任务生效；失败保留全部用量，但不提交质量评分。"""
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
    job_ids: list[str] = []
    run_key = uuid4().hex
    budget = {"max_recheck_rounds": 0, **(budget or {})}
    provider = "unknown"
    model_config: dict = {}

    def result(quotes: list[dict]) -> dict:
        usage = dict.fromkeys(
            ("calls", "input_tokens", "output_tokens", "total_tokens", "unknown_runs"), 0
        )
        with factory() as session:
            for job_id in job_ids:
                spent = spent_tokens(session, job_id)
                for key in spent:
                    usage[key] += spent[key]
            if job_ids:
                usage["calls"] = (
                    session.scalar(
                        select(func.count())
                        .select_from(InferenceRun)
                        .where(InferenceRun.job_id.in_(job_ids))
                    )
                    or 0
                )
        return {
            "book_id": book_id,
            "provider": provider,
            "quality_evidence": provider != "fake-provider",
            "model_config": model_config,
            "quotes": quotes,
            "usage": {**usage, "provider": provider},
        }

    try:
        with transaction(factory) as session:
            profile = session.get(ModelProfile, profile_id)
            if profile is None:
                return None, "NOT_RUN", f"模型配置不存在：{profile_id}"
            provider = profile.protocol
            snapshot = profile_snapshot(profile, inference_options) or {}
            model_config = {
                key: snapshot[key]
                for key in ("profile_id", "protocol", "model", "params", "inference_options")
            }
            raw = Path(book_path).read_bytes()
            importer = import_epub if book_path.suffix.lower() == ".epub" else import_txt
            outcome = importer(session, settings, filename=book_path.name, raw=raw)
            if outcome.reused_book:
                raise ValueError(
                    "评测书库已有同一原始书籍；请为每次评测使用新的隔离书库，避免旧标注干扰"
                )
            book_id, version_id = outcome.book.id, outcome.version.id
            chapter_ids = list(
                session.scalars(
                    select(Chapter.id)
                    .where(Chapter.book_version_id == version_id)
                    .order_by(Chapter.ordinal)
                )
            )

        for chapter_id in chapter_ids:
            with transaction(factory) as session:
                chapter = session.get(Chapter, chapter_id)
                version = session.get(BookVersion, version_id)
                book = session.get(Book, book_id)
                profile = session.get(ModelProfile, profile_id)
                roster_job, _ = create_roster_job(
                    session,
                    book=book,
                    version=version,
                    chapter=chapter,
                    profile=profile,
                    idempotency_key=f"eval:{run_key}:roster:{chapter_id}",
                    max_input_tokens=budget.get("max_input_tokens"),
                    inference_options=inference_options,
                )
                job_ids.append(roster_job.id)
            outcome = run_job(factory, settings, job_id=job_ids[-1], credentials=credentials)
            if outcome.state is not JobState.COMPLETED:
                return (
                    result([]),
                    "LIVE_FAILED",
                    f"人物任务结束于 {outcome.state.value}：{outcome.errors}",
                )
            with transaction(factory) as session:
                chapter = session.get(Chapter, chapter_id)
                version = session.get(BookVersion, version_id)
                roster = get_roster(session, version=version, chapter=chapter)
                if not roster.candidates:
                    if roster.status is CharacterRosterStatus.CONFIRMED:
                        continue
                    raise ValueError("人物识别没有可确认候选")
                pov = next((c for c in roster.candidates if c.pov_candidate), roster.candidates[0])
                confirm_roster(
                    session,
                    version=version,
                    chapter=chapter,
                    payload=RosterConfirmIn(
                        book_version_id=version_id,
                        expected_version=roster.version,
                        confirmation_mode="automatic",
                        pov_temp_ref=pov.temp_ref,
                        candidates=[
                            RosterConfirmCandidateIn(
                                **candidate.model_dump(exclude={"evidence_refs", "pov_candidate"})
                            )
                            for candidate in roster.candidates
                        ],
                    ),
                )
                job, _ = create_inference_job(
                    session,
                    book=session.get(Book, book_id),
                    version=version,
                    profile=session.get(ModelProfile, profile_id),
                    purpose=JobPurpose.PROCESS,
                    range_payload={
                        "chapter_id": chapter_id,
                        "start_cp": chapter.start_cp,
                        "end_cp": chapter.end_cp,
                        "context_policy": context_policy,
                        "force_reprocess": True,
                    },
                    budget=budget,
                    inference_options=inference_options,
                    idempotency_key=f"eval:{run_key}:dialogue:{chapter_id}",
                    reading_mode=reading_mode,
                    visible_horizon_cp=None,
                )
                job_ids.append(job.id)
            outcome = run_job(factory, settings, job_id=job_ids[-1], credentials=credentials)
            if outcome.state is not JobState.COMPLETED:
                return (
                    result([]),
                    "LIVE_FAILED",
                    f"对白任务结束于 {outcome.state.value}：{outcome.errors}",
                )

        with factory() as session:
            version = session.get(BookVersion, version_id)
            projection = build_projection(
                session,
                book_id=book_id,
                book_version_id=version_id,
                start_cp=0,
                end_cp=version.canonical_length_cp,
                reading_mode=reading_mode,
                visible_horizon_cp=None,
                include_pending_reviews=False,
            )
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
        return result(quotes), "COMPLETED", ""
    except Exception as exc:
        if not job_ids:
            raise
        return result([]), "LIVE_FAILED", f"评测任务失败：{exc}"
    finally:
        engine.dispose()
