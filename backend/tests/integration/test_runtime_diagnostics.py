"""Runtime diagnostic boundaries use isolated libraries and offline failures."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from ndr.api.errors import ApiError
from ndr.app import create_app
from ndr.diagnostics import diagnostic_scope, log_event, start_diagnostics, stop_diagnostics
from ndr.domain.enums import ErrorCode
from ndr.runtime_logging import SafeServerFormatter
from ndr.storage.migrate import run_migrations


def _events(settings):
    return [json.loads(line) for line in (settings.log_dir / "tasks.log").read_text(
        encoding="utf-8",
    ).splitlines()]


def test_api_errors_are_safe_and_keep_request_context(migrated_settings):
    app = create_app(migrated_settings)

    @app.get("/diagnostics/{value}")
    def fail(value: str):
        raise RuntimeError("private-novel secret-key query-value")

    @app.get("/diagnostics-known")
    def known():
        try:
            raise sqlite3.OperationalError("private-sqlite-message")
        except sqlite3.OperationalError as error:
            raise ApiError(ErrorCode.INTERNAL_ERROR, "private-known-message") from error

    with TestClient(app, raise_server_exceptions=False) as client:
        unexpected = client.get("/diagnostics/private-path?token=secret-query")
        known_response = client.get("/diagnostics-known")
    assert unexpected.status_code == known_response.status_code == 500
    events = _events(migrated_settings)
    failures = [event for event in events if event.get("operation") == "api_unhandled"]
    assert len(failures) == 1
    assert failures[0]["request_id"] == unexpected.json()["request_id"]
    assert failures[0]["route"] == "/diagnostics/{value}"
    assert failures[0]["method"] == "GET"
    known_event = next(event for event in events if event.get("event") == "api_error")
    assert known_event["error"]["chain"][1]["type"] == "OperationalError"
    content = json.dumps(events)
    for secret in ("private-novel", "secret-key", "private-path", "secret-query",
                   "private-known-message", "private-sqlite-message"):
        assert secret not in content


def test_lifespan_startup_failure_is_logged_and_disposes_engine(tmp_settings, monkeypatch):
    settings = tmp_settings.model_copy(update={"auto_migrate": True})
    app = create_app(settings)
    disposed = []
    original_dispose = app.state.engine.dispose

    def dispose():
        disposed.append(True)
        original_dispose()

    def fail_migration(_):
        raise OSError(28, "private-startup-text")

    monkeypatch.setattr(app.state.engine, "dispose", dispose)
    monkeypatch.setattr("ndr.app.run_migrations", fail_migration)
    with pytest.raises(OSError), TestClient(app):
        pass
    assert disposed == [True]
    events = _events(settings)
    failure = next(event for event in events if event.get("operation") == "application_lifespan")
    assert failure["stage"] == "migration"
    assert "private-startup-text" not in json.dumps(events)
    assert any(event.get("event") == "application_stopped" for event in events)


def test_settlement_monitor_recovers_after_unexpected_round_failure(migrated_settings, monkeypatch):
    recovered = threading.Event()
    attempts = []

    def retry(_):
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError("private-monitor-text")
        recovered.set()

    monkeypatch.setattr("ndr.jobs.local_finalization.retry_settlements", retry)
    with TestClient(create_app(migrated_settings)):
        assert recovered.wait(5)
    failures = [event for event in _events(migrated_settings)
                if event.get("operation") == "settlement_monitor"]
    assert len(failures) == 1
    assert "private-monitor-text" not in json.dumps(failures)


def test_embedded_migration_preserves_active_diagnostic_handler(tmp_settings):
    with diagnostic_scope(tmp_settings.log_dir):
        handler = start_diagnostics(tmp_settings.log_dir)
        try:
            log_event("before_migration")
            run_migrations(tmp_settings)
            log_event("after_migration")
        finally:
            stop_diagnostics(handler)
    names = [event["event"] for event in _events(tmp_settings)]
    assert names == ["before_migration", "after_migration"]


def test_concurrent_app_requests_keep_library_scope(migrated_settings, tmp_path):
    other = migrated_settings.model_copy(update={"data_dir": tmp_path / "other"})
    apps = [create_app(settings) for settings in (migrated_settings, other)]
    for app in apps:
        @app.get("/diagnostics")
        def fail():
            raise RuntimeError("private-request-text")

    with TestClient(apps[0], raise_server_exceptions=False) as first, TestClient(
        apps[1], raise_server_exceptions=False,
    ) as second:
        responses = [first.get("/diagnostics"), second.get("/diagnostics")]
    for settings, response in zip((migrated_settings, other), responses, strict=True):
        failures = [event for event in _events(settings)
                    if event.get("operation") == "api_unhandled"]
        assert len(failures) == 1
        assert failures[0]["request_id"] == response.json()["request_id"]


@pytest.mark.parametrize("raw_traceback", [False, True])
def test_server_formatter_never_emits_exception_content(raw_traceback):
    try:
        raise RuntimeError("private-server-text secret-key")
    except RuntimeError as error:
        info = None if raw_traceback else (type(error), error, error.__traceback__)
        record = logging.LogRecord("uvicorn.error", logging.ERROR, __file__, 1,
                                   "private-server-text secret-key", (), info)
    output = SafeServerFormatter().format(record)
    assert "server_error" in output
    assert "private-server-text" not in output
    assert "secret-key" not in output


def test_server_formatter_preserves_os_error_reason_without_paths():
    error = OSError(98, "private-address-or-path")
    record = logging.LogRecord("uvicorn.error", logging.ERROR, __file__, 1, error, (), None)
    output = SafeServerFormatter().format(record)
    assert '"errno": 98' in output
    assert "private-address-or-path" not in output


def test_health_polling_does_not_add_request_events(migrated_settings):
    with TestClient(create_app(migrated_settings)) as client:
        before = (migrated_settings.log_dir / "tasks.log").read_bytes()
        for _ in range(4):
            assert client.get("/api/health").status_code == 200
        assert (migrated_settings.log_dir / "tasks.log").read_bytes() == before


def test_import_failure_records_job_stage_without_uploaded_filename(migrated_client):
    response = migrated_client.post("/api/books/import", files={
        "file": ("private-upload.epub", b"private-invalid-epub", "application/epub+zip"),
    })
    assert response.status_code == 422
    events = _events(migrated_client.app.state.settings)
    failures = [event for event in events if event.get("event") == "import_failed"]
    assert len(failures) == 1
    assert failures[0]["job_id"] == response.json()["error"]["details"]["job_id"]
    assert failures[0]["stage"] == "epub_structure"
    assert "private-upload" not in json.dumps(events)
    assert "private-invalid-epub" not in json.dumps(events)


def test_merge_failure_records_provider_reason_without_model_content(tmp_settings, monkeypatch):
    from ndr.characters.auto_merge import _finish_merge_failure
    from ndr.domain.enums import JobState
    from ndr.llm.errors import ProviderError, ProviderErrorKind

    monkeypatch.setattr("ndr.characters.auto_merge.finish_local_write",
                        lambda *args, **kwargs: (JobState.FAILED, 1))
    with diagnostic_scope(tmp_settings.log_dir):
        handler = start_diagnostics(tmp_settings.log_dir)
        try:
            _finish_merge_failure(None, "merge-job", "merge-run", ProviderError(
                ProviderErrorKind.AUTH, "private-model-content",
            ), 12)
        finally:
            stop_diagnostics(handler)
    failures = [event for event in _events(tmp_settings)
                if event.get("operation") == "character_merge"]
    assert len(failures) == 1
    assert failures[0]["job_id"] == "merge-job"
    assert failures[0]["run_id"] == "merge-run"
    assert "private-model-content" not in json.dumps(failures)


@pytest.mark.parametrize(("target", "operation"), [
    ("_generate_export", "export_render"),
    ("render_html", "export_generate"),
    ("write_receipt", "export_receipt_write"),
])
def test_export_failure_logs_one_safe_boundary(migrated_client, monkeypatch, target, operation):
    from fixtures.corrections import import_sample

    client = migrated_client
    data = import_sample(client)
    preview = client.post(f"/api/books/{data['book_id']}/exports/preview", json={
        "visibility_policy": "reread", "style": {"preset": "color_and_label"},
    })
    assert preview.status_code == 200

    def fail(*args, **kwargs):
        raise OSError(28, "private-export-text")

    monkeypatch.setattr(f"ndr.exports.service.{target}", fail)
    response = client.post(f"/api/books/{data['book_id']}/exports", json={
        "snapshot_id": preview.json()["data"]["snapshot_id"], "format": "html",
        "style": {"preset": "color_and_label"}, "idempotency_key": "diagnostic-export",
    })
    assert response.status_code == 500
    events = _events(client.app.state.settings)
    failures = [event for event in events if event.get("operation") == operation]
    assert len(failures) == 1
    assert failures[0]["artifact_id"] == response.json()["error"]["details"]["artifact_id"]
    assert failures[0]["job_id"]
    assert "private-export-text" not in json.dumps(events)


@pytest.mark.parametrize(("target", "operation"), [
    ("forget_settlements", "settlement_cleanup"),
    ("dispose", "engine_dispose"),
])
def test_shutdown_failure_is_logged_before_handler_closes(
    migrated_settings, monkeypatch, target, operation,
):
    app = create_app(migrated_settings)
    disposed = []
    original_dispose = app.state.engine.dispose

    def dispose():
        disposed.append(True)
        original_dispose()
        if target == "dispose":
            raise RuntimeError("private-cleanup-text")

    def forget(_):
        raise RuntimeError("private-cleanup-text")

    monkeypatch.setattr(app.state.engine, "dispose", dispose)
    if target == "forget_settlements":
        monkeypatch.setattr("ndr.jobs.local_finalization.forget_settlements", forget)
    with pytest.raises(RuntimeError, match="private-cleanup-text"), TestClient(app):
        pass
    assert disposed == [True]
    events = _events(migrated_settings)
    assert any(event.get("operation") == operation for event in events)
    assert events[-1]["event"] == "application_stop_failed"
    assert "private-cleanup-text" not in json.dumps(events)
