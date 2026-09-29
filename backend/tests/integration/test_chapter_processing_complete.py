from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from ndr.config import Settings
from ndr.domain.enums import (
    AnnotationSource,
    AnnotationStatus,
    JobKind,
    JobPurpose,
    JobState,
    QuoteKind,
)
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.models import Annotation, Job, Quote
from ndr.storage.transactions import transaction


def test_concurrent_windows_require_complete_annotation_coverage(
    migrated_client: TestClient,
    migrated_settings: Settings,
) -> None:
    imported = migrated_client.post(
        "/api/books/import",
        files={"file": ("coverage.txt", "第一章\n「你好。」".encode(), "text/plain")},
    ).json()["data"]
    book_id = imported["book_id"]
    version_id = imported["book_version_id"]
    chapter_id = migrated_client.get(f"/api/books/{book_id}/chapters").json()["data"][0]["id"]
    endpoint = f"/api/books/{book_id}/chapters/{chapter_id}/processing-complete"

    incomplete = migrated_client.post(endpoint, json={"book_version_id": version_id})
    assert incomplete.status_code == 409
    assert incomplete.json()["error"]["details"] == {
        "chapter_id": chapter_id,
        "quote_count": 1,
        "annotated_quote_count": 0,
    }

    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            quote = session.execute(
                select(Quote).where(Quote.chapter_id == chapter_id)
            ).scalar_one()
            session.add(
                Annotation(
                    quote_id=quote.id,
                    kind=QuoteKind.SPEECH,
                    status=AnnotationStatus.PROVISIONAL,
                    source=AnnotationSource.MODEL,
                    evidence_refs_json="[]",
                )
            )
    finally:
        engine.dispose()

    completed = migrated_client.post(endpoint, json={"book_version_id": version_id})
    assert completed.status_code == 200, completed.text
    assert completed.json()["data"] == {
        "chapter_id": chapter_id,
        "dialogue_processed": True,
        "quote_count": 1,
        "annotated_quote_count": 1,
    }
    chapter = migrated_client.get(f"/api/books/{book_id}/chapters").json()["data"][0]
    assert chapter["dialogue_processed"] is True


def test_chapter_directory_reads_persisted_state_without_scanning_completed_jobs(
    migrated_client: TestClient,
    migrated_settings: Settings,
) -> None:
    imported = migrated_client.post(
        "/api/books/import",
        files={"file": ("direct-state.txt", "第一章\n「你好。」".encode(), "text/plain")},
    ).json()["data"]
    book_id = imported["book_id"]
    version_id = imported["book_version_id"]
    chapter_id = migrated_client.get(f"/api/books/{book_id}/chapters").json()["data"][0]["id"]

    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            session.add(Job(
                kind=JobKind.INFERENCE,
                purpose=JobPurpose.PROCESS,
                book_id=book_id,
                book_version_id=version_id,
                range_json=json.dumps({"chapter_id": chapter_id, "selected_window_ids": None}),
                state=JobState.COMPLETED,
                budget_json="{}",
                idempotency_key="legacy-completed-job",
            ))
    finally:
        engine.dispose()

    chapter = migrated_client.get(f"/api/books/{book_id}/chapters").json()["data"][0]
    assert chapter["dialogue_processed"] is False
