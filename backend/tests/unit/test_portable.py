"""Portable startup guards, without touching the real library or credentials."""

from __future__ import annotations

import json
import socket
import sqlite3
import sys

import pytest
from alembic.script import ScriptDirectory

from ndr.config import REPO_ROOT, Settings, default_data_dir
from ndr.portable import (
    LibraryInUseError,
    LibraryLock,
    backup_before_upgrade,
    launch,
    portable_settings,
    running_url,
)
from ndr.storage.migrate import build_alembic_config, run_migrations


def test_frozen_default_data_is_stable_user_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "profile"))
    monkeypatch.chdir(tmp_path)
    assert default_data_dir() == (tmp_path / "profile/NovelDialogueReader/data").resolve()


def test_launcher_ignores_dotenv_and_forces_loopback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("NDR_PORT=8001\nNDR_HOST=0.0.0.0\n", encoding="utf-8")
    monkeypatch.setenv("NDR_HOST", "0.0.0.0")
    settings = portable_settings(data_dir=tmp_path / "library", port=8899)
    assert settings.host == "127.0.0.1"
    assert settings.port == 8899
    assert settings.data_dir == (tmp_path / "library").resolve()
    assert settings.static_dir == REPO_ROOT / "frontend/dist"
    assert not settings.auto_migrate


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_launcher_rejects_invalid_ports(tmp_path, port):
    with pytest.raises(ValueError):
        portable_settings(data_dir=tmp_path, port=port)


def test_library_lock_blocks_second_instance_and_releases(tmp_path):
    with LibraryLock(tmp_path), pytest.raises(LibraryInUseError), LibraryLock(tmp_path):
        pytest.fail("Two owners must not enter")
    with LibraryLock(tmp_path):
        pass


def test_port_conflict_does_not_modify_library(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>test</html>", encoding="utf-8")
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        settings = Settings(
            data_dir=tmp_path / "data", port=blocker.getsockname()[1], static_dir=static,
            credential_backend="session",
        )
        with LibraryLock(settings.data_dir), pytest.raises(RuntimeError, match="被占用"):
            launch(settings, no_browser=True, run_seconds=1)
        assert not settings.database_path.exists()


def test_missing_page_fails_before_database_write(tmp_settings):
    with pytest.raises(RuntimeError, match="页面文件缺失"):
        launch(tmp_settings, no_browser=True, run_seconds=1)
    assert not tmp_settings.database_path.exists()


def test_upgrade_backs_up_once_and_preserves_wal_data(tmp_settings):
    assert backup_before_upgrade(tmp_settings) is None
    scripts = ScriptDirectory.from_config(build_alembic_config(tmp_settings))
    head = scripts.get_current_head()
    prior = scripts.get_revision(head).down_revision
    assert isinstance(prior, str)
    run_migrations(tmp_settings, revision=prior)
    with sqlite3.connect(tmp_settings.database_path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE portable_sentinel (value TEXT)")
        connection.execute("INSERT INTO portable_sentinel VALUES ('original')")
        connection.commit()
        backup = backup_before_upgrade(tmp_settings)
    assert backup is not None
    with sqlite3.connect(backup) as saved:
        assert saved.execute("SELECT value FROM portable_sentinel").fetchone()[0] == "original"
        assert saved.execute("SELECT version_num FROM alembic_version").fetchone()[0] == prior
    run_migrations(tmp_settings)
    assert backup_before_upgrade(tmp_settings) is None
    assert len(list((tmp_settings.data_dir / "backups").iterdir())) == 1


def test_newer_library_is_not_downgraded(migrated_settings):
    with sqlite3.connect(migrated_settings.database_path) as connection:
        connection.execute("UPDATE alembic_version SET version_num='future-unknown'")
    with pytest.raises(RuntimeError, match="不要降级"):
        backup_before_upgrade(migrated_settings)
    assert not (migrated_settings.data_dir / "backups").exists()


@pytest.mark.parametrize("port", ["8765", -1, 65536, True])
def test_duplicate_start_never_opens_untrusted_address(tmp_path, port):
    (tmp_path / "portable-server.json").write_text(json.dumps({
        "port": port, "url": "https://example.com",
    }), encoding="utf-8")
    assert running_url(tmp_path) is None


def test_frozen_resource_root_has_migrations(tmp_path, monkeypatch):
    # Isolate module loading to avoid changing the config class used by other tests.
    import importlib.util

    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    spec = importlib.util.spec_from_file_location("frozen_config_test", REPO_ROOT / "backend/src/ndr/config.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "frozen_config_test", module)
    spec.loader.exec_module(module)
    assert tmp_path == module.REPO_ROOT
    assert module.Settings().alembic_ini_path == tmp_path / "backend/alembic.ini"
    assert module.Settings.model_config["env_file"] is None


def test_release_workflow_requires_explicit_dispatch_and_does_not_overwrite():
    workflow = (REPO_ROOT / ".github/workflows/release-portable.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow and "push:" not in workflow
    assert "persist-credentials: false" in workflow
    assert "scripts/verify.ps1" in workflow and "scripts/test-portable.ps1" in workflow
    assert "--draft" in workflow and "--draft=false" in workflow
    assert "--clobber" not in workflow
    assert "sha256sum --check" in workflow
