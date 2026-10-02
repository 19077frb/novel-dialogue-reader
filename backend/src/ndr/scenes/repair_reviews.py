"""Explicit, offline repair of stale reviews and unambiguous cached bindings.

Run without --apply for a preview. Never dispatch models, replay whole windows,
create identities, or change job states. Applying takes a write lock and a backup.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..config import Settings
from ..domain.enums import (
    AnnotationSource,
    AnnotationStatus,
    Assignment,
    InferenceRunState,
    JobState,
)
from ..llm.errors import InvalidModelOutput
from ..llm.validation import parse_output
from ..storage.engine import create_db_engine, create_session_factory
from ..storage.models import (
    Annotation,
    Book,
    BookCharacter,
    Gap,
    InferenceRun,
    Job,
    Quote,
    ResultCache,
    Scene,
    SpeakerGroup,
)
from .acceptance import decide_acceptance
from .engine import _record_annotation
from .review_sync import sync_attribution_reviews


def repair_book_reviews(session: Session, book_id: str, *, apply: bool = False) -> dict:
    """Repair only current automatic annotations with matching cached evidence."""
    book = session.get(Book, book_id)
    if book is None or not book.active_version_id:
        raise ValueError("书籍不存在或没有可用版本")
    version_id = book.active_version_id
    busy = session.scalar(select(Job.id).where(Job.book_id == book_id, Job.state.in_(
        (JobState.QUEUED, JobState.RUNNING, JobState.PAUSING))))
    if apply and busy:
        raise ValueError("本书仍有排队或运行任务，请等待结束后修复")
    annotations = list(session.scalars(select(Annotation).join(Quote).where(
        Quote.book_version_id == version_id)))
    counts = {"resolved_reason_records": 0, "restored_quotes": 0, "skipped_unknown": 0}
    for annotation in annotations:
        if annotation.status is AnnotationStatus.ACCEPTED and not annotation.stale:
            counts["resolved_reason_records"] += sync_attribution_reviews(
                session, annotation, current_reason=None, apply=apply)
    unknown = {row.quote_id: row for row in annotations
               if row.status is AnnotationStatus.UNKNOWN and not row.stale
               and not row.user_locked and row.source is AnnotationSource.MODEL
               and not row.speaker_id}
    latest = {}
    caches = session.execute(select(ResultCache, InferenceRun.id).join(
        InferenceRun, InferenceRun.id == ResultCache.created_run_id).join(Job).where(
        Job.book_version_id == version_id, InferenceRun.state == InferenceRunState.SUCCEEDED,
    ).order_by(InferenceRun.created_at, InferenceRun.id))
    for cache, run_id in caches:
        try:
            output = parse_output(json.loads(cache.result_json))
        except (ValueError, TypeError, InvalidModelOutput):
            continue
        declarations = {}
        for speaker in output.new_speakers:
            declarations.setdefault(speaker.temp_ref, []).append(speaker)
        for label in output.labels:
            if label.quote_id in unknown:
                latest[label.quote_id] = (label, declarations, run_id)
    quotes = {row.id: row for row in session.scalars(select(Quote).where(
        Quote.book_version_id == version_id))}
    positions = {ref: row.end_cp for ref, row in quotes.items()}
    positions.update({row.id: row.end_cp for row in session.scalars(select(Gap).where(
        Gap.book_version_id == version_id))})
    characters = {row.id for row in session.scalars(select(BookCharacter).where(
        BookCharacter.book_version_id == version_id))}
    for quote_id, annotation in unknown.items():
        cached = latest.get(quote_id)
        if cached is None:
            counts["skipped_unknown"] += 1
            continue
        label, declarations, run_id = cached
        speakers = declarations.get(label.speaker_ref, [])
        if len(speakers) != 1:
            counts["skipped_unknown"] += 1
            continue
        speaker = speakers[0]
        decision = decide_acceptance(label)
        groups = list(session.scalars(select(SpeakerGroup).where(
            SpeakerGroup.scene_id == annotation.scene_id,
            SpeakerGroup.character_id == speaker.character_id)))
        scene = session.get(Scene, annotation.scene_id) if annotation.scene_id else None
        valid = (label.assignment in {Assignment.EXISTING, Assignment.NEW}
                 and speaker.scene_ref == label.scene_ref
                 and speaker.character_id in characters and len(groups) == 1
                 and scene is not None and scene.book_version_id == version_id
                 and speaker.first_quote_id in quotes
                 and quotes[speaker.first_quote_id].start_cp <= quotes[quote_id].start_cp
                 and set(label.evidence_refs) == set(json.loads(annotation.evidence_refs_json))
                 and bool(label.evidence_refs)
                 and decision.status is AnnotationStatus.ACCEPTED)
        evidence_positions = []
        for ref in label.evidence_refs:
            if ref in positions:
                evidence_positions.append(positions[ref])
            elif match := re.fullmatch(r"overlap:(\d+)-(\d+)", ref):
                start, end = map(int, match.groups())
                if 0 <= start < end <= max(positions.values(), default=0):
                    evidence_positions.append(end)
                else:
                    valid = False
            else:
                valid = False
        if not valid:
            counts["skipped_unknown"] += 1
            continue
        counts["restored_quotes"] += 1
        if apply:
            updated, _ = _record_annotation(session, quote_id=quote_id,
                scene_id=annotation.scene_id, kind=label.kind, assignment=Assignment.EXISTING,
                basis=label.basis, speaker_id=groups[0].id, status=decision.status,
                source=AnnotationSource.MODEL, evidence_refs=label.evidence_refs,
                visible_from_cp=max([annotation.visible_from_cp or 0, *evidence_positions]),
                dependency_hash=annotation.dependency_hash or "", run_id=run_id)
            counts["resolved_reason_records"] += sync_attribution_reviews(
                session, updated, current_reason=None)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="离线检查或修复模型待确认记录，不消耗 Tokens")
    parser.add_argument("--book-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    if args.apply:
        backup_dir = settings.data_dir / "backups"
        backup_dir.mkdir(exist_ok=True)
        backup = backup_dir / f"review-repair-{datetime.now(UTC):%Y%m%d-%H%M%S-%f}.sqlite3"
        with (sqlite3.connect(settings.data_dir / "ndr.sqlite3") as source,
              sqlite3.connect(backup) as target):
            source.backup(target)
        print(json.dumps({"backup": str(backup)}, ensure_ascii=False))
    engine = create_db_engine(settings)
    try:
        with create_session_factory(engine)() as session:
            if args.apply:
                session.execute(text("BEGIN IMMEDIATE"))
            result = repair_book_reviews(session, args.book_id, apply=args.apply)
            if args.apply:
                session.commit()
            else:
                session.rollback()
            print(json.dumps(result, ensure_ascii=False))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
