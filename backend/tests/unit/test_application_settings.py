from __future__ import annotations

import json
import sys

import pytest
from fastapi.testclient import TestClient

from ndr import application_settings as preferences
from ndr.api.errors import ApiError
from ndr.app import create_app
from ndr.capacity_settings import BYTES_PER_MB, CAPACITY_KEYS
from ndr.config import Settings
from ndr.portable import portable_settings
from ndr.storage.migrate import run_migrations


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(preferences, "settings_path", lambda: tmp_path / "config/preferences.json")
    monkeypatch.chdir(tmp_path)
    for key in Settings.model_fields:
        monkeypatch.delenv(f"NDR_{key.upper()}", raising=False)
    for key in CAPACITY_KEYS.values():
        monkeypatch.delenv(f"NDR_{key.upper()}", raising=False)
    return tmp_path


@pytest.mark.parametrize("byte_key,mb_key", CAPACITY_KEYS.items())
def test_capacity_mb_sources_priority_lock_and_legacy(isolated, monkeypatch, byte_key, mb_key):
    variable = f"NDR_{mb_key.upper()}"
    legacy = f"NDR_{byte_key.upper()}"
    (isolated / ".env").write_text(f"{variable}=40.5\n{legacy}=123", encoding="utf-8")
    assert getattr(Settings(), byte_key) == int(40.5 * BYTES_PER_MB)
    preferences.settings_path().parent.mkdir()
    preferences.settings_path().write_text(json.dumps({mb_key: 80.25}), encoding="utf-8")
    assert getattr(Settings(), byte_key) == int(80.25 * BYTES_PER_MB)
    monkeypatch.setenv(legacy, "4321")
    assert getattr(Settings(), byte_key) == 4321  # environment wins even over saved MB
    monkeypatch.setenv(variable, "100.5")
    settings = Settings()
    assert getattr(settings, byte_key) == int(100.5 * BYTES_PER_MB)
    assert getattr(Settings(**{byte_key: 999}), byte_key) == 999
    field = next(f for f in preferences.describe_settings(settings).fields if f.key == byte_key)
    assert variable in field.locked_reason
    with pytest.raises(ApiError, match=variable):
        preferences.save_settings(
            settings,
            preferences.ApplicationSettingsPatch(
                revision=preferences.describe_settings(settings).revision, values={byte_key: 1000}
            ),
        )


def test_saved_capacity_migrates_only_on_save_and_restarts_exactly(isolated):
    preferences.settings_path().parent.mkdir()
    legacy = {key: 1048577 for key in CAPACITY_KEYS}
    preferences.settings_path().write_text(json.dumps(legacy), encoding="utf-8")
    before = preferences.settings_path().read_bytes()
    settings = Settings()
    description = preferences.describe_settings(settings)
    assert all(getattr(settings, key) == 1048577 for key in CAPACITY_KEYS)
    assert preferences.settings_path().read_bytes() == before
    preferences.save_settings(
        settings,
        preferences.ApplicationSettingsPatch(revision=description.revision, values={"port": 8800}),
    )
    stored = json.loads(preferences.settings_path().read_text())
    assert all(key not in stored for key in CAPACITY_KEYS)
    assert all(stored[key] == 1048577 / BYTES_PER_MB for key in CAPACITY_KEYS.values())
    assert all(getattr(Settings(), key) == 1048577 for key in CAPACITY_KEYS)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0", "not-a-number", "100000000"])
def test_invalid_mb_environment_rejected(isolated, monkeypatch, value):
    monkeypatch.setenv("NDR_MAX_IMPORT_MB", value)
    with pytest.raises(ValueError, match="NDR_MAX_IMPORT_MB"):
        Settings()


def test_mb_json_has_priority_over_legacy_and_rejects_invalid(isolated):
    preferences.settings_path().parent.mkdir()
    preferences.settings_path().write_text(
        json.dumps({"max_import_bytes": 1, "max_import_mb": 1.5})
    )
    assert Settings().max_import_bytes == int(1.5 * BYTES_PER_MB)
    preferences.settings_path().write_text(json.dumps({"max_import_mb": True}))
    with pytest.raises(ValueError, match="应用配置文件损坏"):
        Settings()


def test_all_fields_have_chinese_definitions_and_no_implicit_write(isolated):
    assert {field.key for field in preferences.DEFINITIONS} == set(Settings.model_fields)
    settings = Settings(_env_file=None)
    result = preferences.describe_settings(settings)
    assert len(result.fields) == 22
    assert all(field.description and field.label for field in result.fields)
    assert not preferences.settings_path().exists()


def test_default_metadata_ignores_environment_dotenv_and_saved_configuration(isolated, monkeypatch):
    (isolated / ".env").write_text("NDR_PORT=8010", encoding="utf-8")
    preferences.settings_path().parent.mkdir()
    preferences.settings_path().write_text(json.dumps({"port": 8020}), encoding="utf-8")
    monkeypatch.setenv("NDR_PORT", "8030")
    settings = Settings(data_dir=isolated / "library")
    before = preferences.settings_path().read_bytes()
    fields = {field.key: field for field in preferences.describe_settings(settings).fields}
    assert fields["port"].default_value == 8765
    assert fields["port"].current_value == 8030
    assert fields["port"].locked_reason
    assert fields["data_dir"].default_value != str(settings.data_dir)
    assert fields["cors_origins"].default_value == [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]
    assert preferences.settings_path().read_bytes() == before


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
