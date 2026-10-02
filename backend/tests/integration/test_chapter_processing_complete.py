from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from ndr.app import create_app
from ndr.config import Settings
from ndr.domain.enums import (
    AnnotationSource,
    AnnotationStatus,
    JobKind,
    JobPurpose,
    JobState,
    QuoteKind,
)
from ndr.storage.chapter_status import complete_chapter_automatically
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.models import Annotation, Chapter, Job, Quote
from ndr.storage.transactions import transaction


def test_manual_status_is_persistent_and_does_not_pause_running_jobs(
    migrated_client: TestClient, migrated_settings: Settings,
) -> None:
    data = migrated_client.post("/api/books/import", files={
        "file": ("manual.txt", "第一章\n「你好。」".encode(), "text/plain"),
    }).json()["data"]
    path = f"/api/books/{data['book_id']}/chapters"
    chapter = migrated_client.get(path).json()["data"][0]
    endpoint = f"{path}/{chapter['id']}/processing-status"
    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            job = Job(kind=JobKind.INFERENCE, purpose=JobPurpose.PROCESS,
                      book_id=data["book_id"], book_version_id=data["book_version_id"],
                      state=JobState.RUNNING, range_json=json.dumps({"chapter_id": chapter["id"]}),
                      budget_json="{}", idempotency_key="running-manual-status")
            session.add(job)
            session.flush()
            job_id = job.id
        for status in (True, False):
            saved = migrated_client.put(endpoint, json={
                "book_version_id": data["book_version_id"], "dialogue_processed": status,
            })
            assert saved.status_code == 200, saved.text
            assert saved.json()["data"]["dialogue_processed"] is status
            assert saved.json()["data"]["processing_status_override"] is status
            completion = migrated_client.post(f"{path}/{chapter['id']}/processing-complete", json={
                "book_version_id": data["book_version_id"],
            })
            assert completion.status_code == 200, completion.text
            assert completion.json()["data"]["dialogue_processed"] is status
            with factory() as session:
                assert session.get(Job, job_id).state is JobState.RUNNING
        migrated_client.put(endpoint, json={
            "book_version_id": data["book_version_id"], "dialogue_processed": None,
        })
        with factory() as stale:
            cached_chapter = stale.get(Chapter, chapter["id"])
            assert cached_chapter.processing_status_override is None
            migrated_client.put(endpoint, json={
                "book_version_id": data["book_version_id"], "dialogue_processed": False,
            })
            assert complete_chapter_automatically(stale, cached_chapter) is False
            stale.commit()
        with TestClient(create_app(migrated_settings.model_copy(update={"recover_on_startup": False}))) as restarted:
            assert restarted.get(path).json()["data"][0]["processing_status_override"] is False
        assert migrated_client.put(endpoint, json={
            "book_version_id": "wrong-version", "dialogue_processed": True,
        }).status_code == 422
        cleared = migrated_client.put(endpoint, json={
            "book_version_id": data["book_version_id"], "dialogue_processed": None,
        })
        assert cleared.json()["data"]["processing_status_override"] is None
        assert migrated_client.post(f"{path}/{chapter['id']}/processing-complete", json={
            "book_version_id": data["book_version_id"],
        }).status_code == 409
    finally:
        engine.dispose()


def test_manual_unprocessed_heading_is_not_completed_again_on_startup(
    migrated_client: TestClient, migrated_settings: Settings,
) -> None:
    data = migrated_client.post("/api/books/import", files={
        "file": ("plate.txt", "第一卷 插图\n".encode(), "text/plain"),
    }).json()["data"]
    path = f"/api/books/{data['book_id']}/chapters"
    chapter = migrated_client.get(path).json()["data"][0]
    assert chapter["dialogue_processed"] is True
    response = migrated_client.put(f"{path}/{chapter['id']}/processing-status", json={
        "book_version_id": data["book_version_id"], "dialogue_processed": False,
    })
    assert response.status_code == 200, response.text
    with TestClient(create_app(migrated_settings)) as restarted:
        saved = restarted.get(path).json()["data"][0]
        assert saved["dialogue_processed"] is False
        assert saved["processing_status_override"] is False


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
