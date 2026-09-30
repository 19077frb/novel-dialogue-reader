from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from fixtures.corrections import create_fake_profile, run_deterministic_job
from ndr.config import Settings
from ndr.domain.enums import CorrectionAction, CorrectionTargetType, ExportFormat, JobState
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.models import (
    Annotation,
    BookVersion,
    Correction,
    ExportArtifact,
    ExportSnapshot,
    Job,
    Quote,
)
from ndr.storage.transactions import transaction


def import_book(client: TestClient, body: str) -> dict:
    response = client.post(
        "/api/books/import",
        files={"file": ("同名书.txt", body.encode(), "text/plain")},
        data={"title": "同名书"},
    )
    assert response.status_code == 202, response.text
    return response.json()["data"]


def test_delete_only_selected_book_with_annotations_and_exports(
    fake_provider_client: TestClient, migrated_settings: Settings,
) -> None:
    client = fake_provider_client
    first = import_book(client, "第一章\n「你好。」\n「再见。」")
    other = import_book(client, "第一章\n「另一本书。」")
    profile = create_fake_profile(client)
    run_deterministic_job(
        migrated_settings, client, book_id=first["book_id"], profile_id=profile,
        key="delete-annotated",
    )
    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            quote_id = session.execute(select(Quote.id).where(
                Quote.book_version_id == first["book_version_id"],
            )).scalars().first()
            correction = Correction(
                target_type=CorrectionTargetType.QUOTE,
                target_id=quote_id,
                action=CorrectionAction.MARK_UNKNOWN,
            )
            session.add(correction)
            snapshot = ExportSnapshot(
                book_id=first["book_id"], book_version_id=first["book_version_id"],
                source_revision="revision", visibility_policy="reread", snapshot_hash="a" * 64,
            )
            session.add(snapshot)
            session.flush()
            artifact = ExportArtifact(
                snapshot_id=snapshot.id, format=ExportFormat.HTML,
                exporter_version="test", fingerprint="b" * 64,
            )
            session.add(artifact)
            session.flush()
            artifact_id, correction_id = artifact.id, correction.id

        root = migrated_settings.data_dir
        export_dir = root / "exports" / artifact_id
        export_dir.mkdir(parents=True)
        (export_dir / "book.html").write_text("test", encoding="utf-8")
        response = client.delete(f"/api/books/{first['book_id']}")
        assert response.status_code == 204, response.text
        assert response.content == b""
        assert client.get(f"/api/books/{first['book_id']}").status_code == 404
        assert client.get(f"/api/jobs/{first['job_id']}").status_code == 404
        assert client.get(f"/api/books/{other['book_id']}").status_code == 200
        assert [item["id"] for item in client.get("/api/books").json()["data"]["items"]] == [
            other["book_id"],
        ]
        assert not (root / "books" / first["book_id"]).exists()
        assert (root / "books" / other["book_id"]).exists()
        assert not export_dir.exists()
        assert list((root / "trash").glob(f"*/books/{first['book_id']}/source.txt"))
        assert list((root / "trash").glob(f"*/exports/{artifact_id}/book.html"))
        with transaction(factory) as session:
            assert session.get(BookVersion, first["book_version_id"]) is None
            assert session.get(Correction, correction_id) is None
            assert session.get(ExportArtifact, artifact_id) is None
            assert session.execute(select(Annotation)).first() is None

        # 删除后重新导入不复用已删除记录。
        again = import_book(client, "第一章\n「你好。」\n「再见。」")
        assert again["book_id"] != first["book_id"]
    finally:
        engine.dispose()


@pytest.mark.parametrize("state", [JobState.QUEUED, JobState.RUNNING, JobState.PAUSING])
def test_delete_rejects_active_tasks(
    migrated_client: TestClient, migrated_settings: Settings, state: JobState,
) -> None:
    imported = import_book(migrated_client, "第一章\n「你好。」")
    engine = create_db_engine(migrated_settings)
    try:
        with transaction(create_session_factory(engine)) as session:
            session.get(Job, imported["job_id"]).state = state
        response = migrated_client.delete(f"/api/books/{imported['book_id']}")
        assert response.status_code == 409
        assert "任务" in response.json()["error"]["message"]
        assert (migrated_settings.data_dir / "books" / imported["book_id"]).exists()
        assert migrated_client.get(f"/api/books/{imported['book_id']}").status_code == 200
    finally:
        engine.dispose()


def test_delete_missing_book(migrated_client: TestClient) -> None:
    assert migrated_client.delete("/api/books/missing").status_code == 404


def test_file_move_failure_keeps_book_and_rolls_back_records(
    migrated_client: TestClient, migrated_settings: Settings, monkeypatch,
) -> None:
    imported = import_book(migrated_client, "第一章\n「你好。」")
    engine = create_db_engine(migrated_settings)
    try:
        with transaction(create_session_factory(engine)) as session:
            snapshot = ExportSnapshot(
                book_id=imported["book_id"], book_version_id=imported["book_version_id"],
                source_revision="revision", visibility_policy="reread", snapshot_hash="a" * 64,
            )
            session.add(snapshot)
            session.flush()
            artifact = ExportArtifact(
                snapshot_id=snapshot.id, format=ExportFormat.HTML,
                exporter_version="test", fingerprint="b" * 64,
            )
            session.add(artifact)
            session.flush()
            artifact_id = artifact.id
    finally:
        engine.dispose()
    export_dir = migrated_settings.data_dir / "exports" / artifact_id
    export_dir.mkdir(parents=True)
    original_rename = Path.rename
    def fail_move(path, target):
        if path == export_dir:
            raise OSError("文件被占用")
        return original_rename(path, target)
    monkeypatch.setattr(Path, "rename", fail_move)
    response = migrated_client.delete(f"/api/books/{imported['book_id']}")
    assert response.status_code == 503
    assert "无法移动书籍文件" in response.json()["error"]["message"]
    assert migrated_client.get(f"/api/books/{imported['book_id']}").status_code == 200
    assert (migrated_settings.data_dir / "books" / imported["book_id"]).exists()
    assert export_dir.exists()
