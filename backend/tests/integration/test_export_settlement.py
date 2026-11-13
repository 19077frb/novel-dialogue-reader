"""Isolated export snapshot and local-finalization regressions. No provider network."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from fixtures.corrections import create_fake_profile, import_sample, run_deterministic_job
from ndr.app import create_app
from ndr.domain.enums import JobState
from ndr.exports import service, settlement, snapshot
from ndr.storage.models import (
    Annotation,
    Book,
    BookCharacter,
    Chapter,
    ExportArtifact,
    ExportSnapshot,
    Job,
    SpeakerGroup,
)
from ndr.storage.transactions import transaction


def _preview(client, book_id, policy="reread"):
    response = client.post(f"/api/books/{book_id}/exports/preview", json={
        "visibility_policy": policy, "style": {"preset": "color_and_label"},
    })
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _export(client, book_id, snapshot_id):
    return client.post(f"/api/books/{book_id}/exports", json={
        "snapshot_id": snapshot_id, "format": "html",
        "style": {"preset": "color_and_label"}, "idempotency_key": snapshot_id,
    })


def _busy_terminal(monkeypatch, *, once=False):
    commit = Session.commit
    control = {"busy": True, "attempts": 0}

    def guarded(session):
        terminal = any(isinstance(row, ExportArtifact) and row.state in {"COMPLETED", "FAILED"}
                       for row in session.dirty)
        if terminal and control["busy"]:
            control["attempts"] += 1
            if once:
                control["busy"] = False
            raise OperationalError("local finalization", {}, sqlite3.OperationalError("database is locked"))
        return commit(session)

    monkeypatch.setattr(Session, "commit", guarded)
    deferred = []
    monkeypatch.setattr(settlement, "defer_settlement",
                        lambda factory, job_id, persist: deferred.append((job_id, persist)))
    return control, deferred


def test_snapshot_keeps_one_physical_read_view_during_concurrent_identity_correction(
    fake_provider_client, monkeypatch,
):
    client = fake_provider_client
    data = import_sample(client)
    run_deterministic_job(client.app.state.settings, client, book_id=data["book_id"],
                          profile_id=create_fake_profile(client), key="snapshot-consistent")
    factory = client.app.state.session_factory
    with transaction(factory) as session:
        group = session.scalars(select(SpeakerGroup).order_by(SpeakerGroup.id)).first()
        person = BookCharacter(book_version_id=data["book_version_id"], canonical_name="旧人物")
        session.add(person)
        session.flush()
        group.character_id = person.id
        group.canonical_name = "旧人物"
        group.presentation_history_json = "[]"
        person_id, group_id = person.id, group.id
        chapter = session.scalars(select(Chapter).order_by(Chapter.ordinal)).first()
        chapter.dialogue_processed = False
        chapter_id = chapter.id
    original = snapshot.build_projection

    def change_after_projection(session, **kwargs):
        result = original(session, **kwargs)
        assert session.connection().connection.driver_connection.in_transaction
        # WAL permits this independent writer while the export retains its old view.
        with transaction(factory) as writer:
            writer.get(BookCharacter, person_id).canonical_name = "新人物"
            writer.get(SpeakerGroup, group_id).canonical_name = "新人物"
            writer.get(Chapter, chapter_id).dialogue_processed = True
            writer.get(Book, data["book_id"]).read_position_cp = 1
            for annotation in writer.scalars(select(Annotation)):
                annotation.version += 1
        return result

    monkeypatch.setattr(snapshot, "build_projection", change_after_projection)
    preview = _preview(client, data["book_id"])
    with factory() as session:
        payload = json.loads(session.get(ExportSnapshot, preview["snapshot_id"]).annotation_projection_json)
        assert any(item["label"] == "旧人物" for item in payload["items"])
        person = next(row for row in payload["character_snapshot"]["characters"]
                      if row["identity"] == f"character:{person_id}")
        assert person["name"] == "旧人物"
        assert payload["chapter_processing"][chapter_id]["processed"] is False
        assert session.get(BookCharacter, person_id).canonical_name == "新人物"


def test_terminal_busy_retries_only_database_write(migrated_client, monkeypatch):
    client = migrated_client
    data = import_sample(client)
    preview = _preview(client, data["book_id"])
    control, deferred = _busy_terminal(monkeypatch, once=True)
    render = service.render_html
    rendered = []

    def counted(*args, **kwargs):
        rendered.append(True)
        return render(*args, **kwargs)

    monkeypatch.setattr(service, "render_html", counted)
    response = _export(client, data["book_id"], preview["snapshot_id"])
    assert response.status_code == 201, response.text
    artifact = response.json()["data"]
    assert artifact["state"] == "COMPLETED" and control["attempts"] == 1
    assert len(rendered) == 1 and not deferred
    assert not settlement.receipt_path(client.app.state.settings, artifact["id"]).exists()
    assert client.get(f"/api/exports/{artifact['id']}/download").status_code == 200


def test_concurrent_delete_before_snapshot_save_returns_not_found(migrated_client, monkeypatch):
    client = migrated_client
    data = import_sample(client)
    original = snapshot.build_projection

    def delete_after_read(session, **kwargs):
        result = original(session, **kwargs)
        with transaction(client.app.state.session_factory) as writer:
            writer.execute(delete(Book).where(Book.id == data["book_id"]))
        return result

    monkeypatch.setattr(snapshot, "build_projection", delete_after_read)
    response = client.post(f"/api/books/{data['book_id']}/exports/preview", json={
        "visibility_policy": "reread", "style": {"preset": "color_and_label"},
    })
    assert response.status_code == 404, response.text
    with client.app.state.session_factory() as session:
        assert not list(session.scalars(select(ExportSnapshot)))


def test_continued_busy_reuses_artifact_and_defers_only_settlement(migrated_client, monkeypatch):
    client = migrated_client
    data = import_sample(client)
    preview = _preview(client, data["book_id"])
    control, deferred = _busy_terminal(monkeypatch)
    first = _export(client, data["book_id"], preview["snapshot_id"])
    assert first.status_code == 201, first.text
    artifact = first.json()["data"]
    assert artifact["state"] == "RUNNING" and control["attempts"] == 3
    with client.app.state.session_factory() as session:
        row = session.get(ExportArtifact, artifact["id"])
        assert session.get(Job, row.job_id).state is JobState.RUNNING
        assert deferred[0][0] == row.job_id
    monkeypatch.setattr(service, "render_html", lambda *args, **kwargs: pytest.fail("render repeated"))
    again = _export(client, data["book_id"], preview["snapshot_id"])
    assert again.status_code == 201 and again.json()["data"]["id"] == artifact["id"]
    with client.app.state.session_factory() as session:
        assert len(list(session.scalars(select(ExportArtifact)))) == 1
    control["busy"] = False
    deferred[0][1]()
    completed = client.get(f"/api/exports/{artifact['id']}").json()["data"]
    assert completed["state"] == "COMPLETED" and completed["download_available"]
    monkeypatch.setattr(settlement, "_load_outcome", lambda *args: pytest.fail("receipt re-read"))
    assert client.get(f"/api/exports/{artifact['id']}").status_code == 200
    assert client.get(f"/api/exports/{artifact['id']}/download").status_code == 200


def test_start_busy_then_same_request_claims_queued_artifact(migrated_client, monkeypatch):
    client = migrated_client
    data = import_sample(client)
    preview = _preview(client, data["book_id"])
    commit = Session.commit
    control = {"busy": True, "attempts": 0}

    def guarded(session):
        if control["busy"] and any(isinstance(row, ExportArtifact) and row.state == "RUNNING"
                                   for row in session.dirty):
            control["attempts"] += 1
            raise OperationalError("claim", {}, sqlite3.OperationalError("database is locked"))
        return commit(session)

    monkeypatch.setattr(Session, "commit", guarded)
    with pytest.raises(OperationalError):
        _export(client, data["book_id"], preview["snapshot_id"])
    assert control["attempts"] == 3
    with client.app.state.session_factory() as session:
        queued = session.scalars(select(ExportArtifact)).one()
        artifact_id = queued.id
        assert queued.state == "QUEUED"
    control["busy"] = False
    repeated = _export(client, data["book_id"], preview["snapshot_id"])
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["data"]["id"] == artifact_id
    assert repeated.json()["data"]["state"] == "COMPLETED"


@pytest.mark.parametrize("write_busy", [False, True])
def test_receipt_write_failure_finishes_or_defers_failed_state(migrated_client, monkeypatch, write_busy):
    client = migrated_client
    data = import_sample(client)
    preview = _preview(client, data["book_id"])
    control, deferred = _busy_terminal(monkeypatch)
    control["busy"] = write_busy

    def disk_error(*args, **kwargs):
        raise OSError("isolated receipt write failure")

    monkeypatch.setattr(service, "write_receipt", disk_error)
    response = _export(client, data["book_id"], preview["snapshot_id"])
    assert response.status_code == (201 if write_busy else 500), response.text
    with client.app.state.session_factory() as session:
        row = session.scalars(select(ExportArtifact)).one()
        artifact_id = row.id
        assert row.state == ("RUNNING" if write_busy else "FAILED")
    monkeypatch.setattr(service, "render_html", lambda *args, **kwargs: pytest.fail("render repeated"))
    if write_busy:
        control["busy"] = False
        deferred[0][1]()
    result = client.get(f"/api/exports/{artifact_id}").json()["data"]
    assert result["state"] == "FAILED" and not result["download_available"]
    assert "收尾记录" in result["validation"]["detail"]


def test_receipt_cleanup_failure_keeps_completed_state_without_rewrites(migrated_client, monkeypatch):
    client = migrated_client
    data = import_sample(client)
    preview = _preview(client, data["book_id"])
    unlink = Path.unlink

    def cannot_unlink(path, *args, **kwargs):
        if path.name == "settlement.json":
            raise OSError("isolated cleanup failure")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", cannot_unlink)
    first = _export(client, data["book_id"], preview["snapshot_id"])
    assert first.status_code == 201, first.text
    artifact = first.json()["data"]
    assert artifact["state"] == "COMPLETED"
    monkeypatch.setattr(settlement, "_persist_outcome", lambda *args, **kwargs: pytest.fail("terminal rewritten"))
    monkeypatch.setattr(settlement, "_load_outcome", lambda *args: pytest.fail("file hashed again"))
    assert client.get(f"/api/exports/{artifact['id']}").status_code == 200
    assert client.get(f"/api/exports/{artifact['id']}/download").status_code == 200
    assert _export(client, data["book_id"], preview["snapshot_id"]).status_code == 201


def test_concurrent_same_fingerprint_uses_existing_running_export(migrated_client, monkeypatch):
    client = migrated_client
    data = import_sample(client)
    preview = _preview(client, data["book_id"])
    render = service.render_html
    concurrent = []

    def request_during_render(*args, **kwargs):
        response = _export(client, data["book_id"], preview["snapshot_id"])
        assert response.status_code == 201, response.text
        concurrent.append(response.json()["data"])
        return render(*args, **kwargs)

    monkeypatch.setattr(service, "render_html", request_during_render)
    first = _export(client, data["book_id"], preview["snapshot_id"]).json()["data"]
    assert concurrent[0]["id"] == first["id"]
    assert concurrent[0]["state"] == "RUNNING" and first["state"] == "COMPLETED"


@pytest.mark.parametrize("damage", [None, "json", "file", "fingerprint", "snapshot", "missing"])
def test_startup_recovers_receipt_and_rejects_corruption(migrated_client, monkeypatch, damage):
    client = migrated_client
    data = import_sample(client)
    preview = _preview(client, data["book_id"])
    control, _ = _busy_terminal(monkeypatch)
    artifact = _export(client, data["book_id"], preview["snapshot_id"]).json()["data"]
    settings = client.app.state.settings
    path = settlement.receipt_path(settings, artifact["id"])
    receipt = json.loads(path.read_text(encoding="utf-8"))
    exported = path.parent / receipt["outcome"]["filename"]
    if damage == "json":
        path.write_text("{broken", encoding="utf-8")
    elif damage == "file":
        exported.write_text("corrupt", encoding="utf-8")
    elif damage == "missing":
        exported.unlink()
    elif damage in {"fingerprint", "snapshot"}:
        receipt["fingerprint" if damage == "fingerprint" else "snapshot_hash"] = "wrong"
        path.write_text(json.dumps(receipt), encoding="utf-8")
    control["busy"] = False
    monkeypatch.setattr(service, "render_html", lambda *args, **kwargs: pytest.fail("render repeated"))
    with TestClient(create_app(settings)) as restarted:
        result = restarted.get(f"/api/exports/{artifact['id']}").json()["data"]
        assert result["state"] == ("FAILED" if damage else "COMPLETED")
        with restarted.app.state.session_factory() as session:
            row = session.get(ExportArtifact, artifact["id"])
            assert session.get(Job, row.job_id).state.value == result["state"]
        assert not path.exists()


def test_startup_marks_legacy_interrupted_export_failed(migrated_client):
    client = migrated_client
    data = import_sample(client)
    preview = _preview(client, data["book_id"])
    with transaction(client.app.state.session_factory) as session:
        artifact, _ = service.create_artifact(
            session, snapshot=session.get(ExportSnapshot, preview["snapshot_id"]),
            fmt=service.ExportFormat.HTML, style=service.ExportStylePreset.COLOR_AND_LABEL,
        )
        artifact_id = artifact.id
    with TestClient(create_app(client.app.state.settings)) as restarted:
        result = restarted.get(f"/api/exports/{artifact_id}").json()["data"]
        assert result["state"] == "FAILED" and "进程重启" in result["validation"]["detail"]
        regenerated = _export(restarted, data["book_id"], preview["snapshot_id"])
        assert regenerated.status_code == 201
        assert regenerated.json()["data"]["id"] != artifact_id


def test_legacy_duplicate_fingerprints_prefer_completed_artifact(migrated_client):
    client = migrated_client
    data = import_sample(client)
    preview = _preview(client, data["book_id"])
    completed = _export(client, data["book_id"], preview["snapshot_id"]).json()["data"]
    with transaction(client.app.state.session_factory) as session:
        original = session.get(ExportArtifact, completed["id"])
        # Older application versions could persist several rows for one fingerprint.
        for state in ["RUNNING", "COMPLETED"]:
            session.add(ExportArtifact(
                snapshot_id=original.snapshot_id, format=original.format,
                options_json=original.options_json, exporter_version=original.exporter_version,
                fingerprint=original.fingerprint, state=state,
            ))
    repeated = _export(client, data["book_id"], preview["snapshot_id"])
    assert repeated.status_code == 201
    assert repeated.json()["data"]["id"] == completed["id"]
