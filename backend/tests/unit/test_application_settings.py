from __future__ import annotations

import json
import sys

import pytest
from fastapi.testclient import TestClient

from ndr import application_settings as preferences
from ndr.api.errors import ApiError
from ndr.app import create_app
from ndr.config import Settings
from ndr.portable import portable_settings
from ndr.storage.migrate import run_migrations


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(preferences, "settings_path", lambda: tmp_path / "config/preferences.json")
    monkeypatch.chdir(tmp_path)
    for key in Settings.model_fields:
        monkeypatch.delenv(f"NDR_{key.upper()}", raising=False)
    return tmp_path


def test_all_fields_have_chinese_definitions_and_no_implicit_write(isolated):
    assert {field.key for field in preferences.DEFINITIONS} == set(Settings.model_fields)
    settings = Settings(_env_file=None)
    result = preferences.describe_settings(settings)
    assert len(result.fields) == 22
    assert all(field.description and field.label for field in result.fields)
    assert not preferences.settings_path().exists()


def test_save_is_restart_only_and_precedence_preserves_explicit_environment(isolated, monkeypatch):
    (isolated / ".env").write_text("NDR_PORT=8010\nNDR_LLM_TIMEOUT_SECONDS=20", encoding="utf-8")
    original = Settings()
    assert original.port == 8010
    output = preferences.save_settings(
        original,
        preferences.ApplicationSettingsPatch(
            revision="missing",
            values={"port": 8020, "llm_timeout_seconds": 90},
        ),
    )
    assert original.port == 8010 and original.llm_timeout_seconds == 20
    assert set(output.restart_required) == {"port", "llm_timeout_seconds"}
    assert Settings().port == 8020
    monkeypatch.setenv("NDR_PORT", "8030")
    assert Settings().port == 8030
    assert Settings(port=8040).port == 8040
    assert preferences.describe_settings(Settings()).fields[2].locked_reason
    with pytest.raises(ApiError, match="环境变量"):
        preferences.save_settings(
            original,
            preferences.ApplicationSettingsPatch(
                revision=output.revision,
                values={"port": 8050},
            ),
        )


def test_directory_change_does_not_hide_configuration_or_move_books(isolated):
    original = Settings(data_dir=isolated / "original")
    original.ensure_data_dir()
    marker = original.data_dir / "book.txt"
    marker.write_text("原文", encoding="utf-8")
    destination = isolated / "new-library"
    preferences.save_settings(
        original,
        preferences.ApplicationSettingsPatch(
            revision="missing",
            values={"data_dir": str(destination)},
        ),
    )
    assert Settings().data_dir == destination
    assert not destination.exists() and marker.read_text(encoding="utf-8") == "原文"
    assert preferences.settings_path() == isolated / "config/preferences.json"


def test_portable_reads_config_but_not_dotenv_and_locks_managed_values(isolated, monkeypatch):
    preferences.settings_path().parent.mkdir()
    preferences.settings_path().write_text(json.dumps({"port": 8810, "llm_timeout_seconds": 180}))
    (isolated / ".env").write_text("NDR_PORT=8000", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    settings = portable_settings(data_dir=None, port=None)
    assert settings.port == 8810 and settings.llm_timeout_seconds == 180
    result = preferences.describe_settings(settings)
    locked = {field.key for field in result.fields if field.locked_reason}
    assert locked == {"host", "static_dir", "auto_migrate"}
    assert portable_settings(data_dir=isolated / "library", port=8811).port == 8811


@pytest.mark.parametrize(
    "values",
    [
        {"port": 0},
        {"port": True},
        {"port": 1.5},
        {"llm_timeout_seconds": 0},
        {"max_import_bytes": -1},
        {"unknown": 1},
        {"recover_on_startup": "true"},
        {"credential_backend": "unknown"},
        {"host": "0.0.0.0"},
        {"data_dir": "relative"},
        {"cors_origins": ["*"]},
        {"cors_origins": ["https://user:pass@example.org"]},
        {"cors_origins": ["https://example.org/path"]},
        {"rate_limit_backoff_base_seconds": 31},
        {"max_epub_entry_bytes": 300 * 1024**2},
        {"environment": "x" * 5000},
    ],
)
def test_invalid_values_never_create_configuration(isolated, values):
    with pytest.raises(ApiError):
        preferences.save_settings(
            Settings(_env_file=None),
            preferences.ApplicationSettingsPatch(
                revision="missing",
                values=values,
            ),
        )
    assert not preferences.settings_path().exists()


def test_conflict_and_failed_replace_preserve_last_valid_config(isolated, monkeypatch):
    settings = Settings(_env_file=None)
    output = preferences.save_settings(
        settings,
        preferences.ApplicationSettingsPatch(
            revision="missing",
            values={"port": 8123},
        ),
    )
    before = preferences.settings_path().read_bytes()
    with pytest.raises(ApiError, match="其他页面"):
        preferences.save_settings(
            settings,
            preferences.ApplicationSettingsPatch(
                revision="missing",
                values={"port": 8234},
            ),
        )

    def fail_replace(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(preferences.os, "replace", fail_replace)
    with pytest.raises(OSError):
        preferences.save_settings(
            settings,
            preferences.ApplicationSettingsPatch(
                revision=output.revision,
                values={"port": 8234},
            ),
        )
    assert preferences.settings_path().read_bytes() == before
    assert not list(preferences.settings_path().parent.glob("*.tmp"))


def test_api_origin_save_restart_and_new_writes_blocked(isolated):
    settings = Settings(data_dir=isolated / "library", credential_backend="session")
    run_migrations(settings)
    app = create_app(settings)
    restarted = []
    app.state.request_restart = lambda: restarted.append(True)
    with TestClient(app) as client:
        description = client.get("/api/settings/application").json()["data"]
        assert description["restart_blocked_reason"] is None
        payload = {"revision": description["revision"], "values": {"llm_timeout_seconds": 180}}
        assert (
            client.patch(
                "/api/settings/application",
                json=payload,
                headers={"Origin": "https://untrusted.example"},
            ).status_code
            == 422
        )
        assert not preferences.settings_path().exists()
        response = client.post("/api/settings/application/save-and-restart", json=payload)
        assert response.status_code == 200 and restarted == [True]
        assert settings.llm_timeout_seconds == 30
        assert client.patch("/api/settings/application", json=payload).status_code == 409
        assert client.get("/api/health").status_code == 200


def test_restart_refused_for_active_jobs_or_external_server_without_saving(isolated):
    from datetime import UTC, datetime

    from ndr.storage.models.jobs import Job

    settings = Settings(data_dir=isolated / "library", credential_backend="session")
    run_migrations(settings)
    app = create_app(settings)
    payload = {"revision": "missing", "values": {"llm_timeout_seconds": 180}}
    with TestClient(app) as client:
        response = client.post("/api/settings/application/save-and-restart", json=payload)
        assert response.status_code == 409 and "启动方式" in response.json()["error"]["message"]
        assert not preferences.settings_path().exists()
        app.state.request_restart = lambda: pytest.fail("Must not restart")
        with app.state.session_factory() as session:
            session.add(
                Job(id="active", kind="INFERENCE", state="RUNNING", created_at=datetime.now(UTC))
            )
            session.commit()
        response = client.post("/api/settings/application/save-and-restart", json=payload)
        assert response.status_code == 409 and "任务" in response.json()["error"]["message"]
        assert not preferences.settings_path().exists()
        assert not app.state.restart_pending
        assert client.patch("/api/settings/application", json=payload).status_code == 200


def test_failed_save_does_not_restart_and_reports_storage_error(isolated, monkeypatch):
    settings = Settings(data_dir=isolated / "library", credential_backend="session")
    run_migrations(settings)
    app = create_app(settings)
    restarted = []
    app.state.request_restart = lambda: restarted.append(True)

    def fail_replace(*args):
        raise OSError("磁盘空间不足")

    monkeypatch.setattr(preferences.os, "replace", fail_replace)
    with TestClient(app) as client:
        response = client.post(
            "/api/settings/application/save-and-restart",
            json={
                "revision": "missing",
                "values": {"llm_timeout_seconds": 90},
            },
        )
        assert response.status_code == 500
        assert "磁盘空间不足" in response.json()["error"]["message"]
        assert not restarted and not app.state.restart_pending
        assert not preferences.settings_path().exists()


def test_restart_refuses_concurrent_write_before_saving(isolated):
    app = create_app(Settings(data_dir=isolated / "library", credential_backend="session"))
    app.state.active_writes = 1
    with TestClient(app) as client:
        response = client.post(
            "/api/settings/application/save-and-restart",
            json={
                "revision": "missing",
                "values": {"llm_timeout_seconds": 90},
            },
        )
        assert response.status_code == 409
        assert "操作正在保存" in response.json()["error"]["message"]
        assert not preferences.settings_path().exists()
        assert app.state.active_writes == 1
