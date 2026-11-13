"""Portable startup guards, without touching the real library or credentials."""

from __future__ import annotations

import json
import socket
import sqlite3
import struct
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


def test_application_icon_contains_seven_rgba_png_frames():
    source = REPO_ROOT / "assets/icons/app.png"
    assert source.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    data = (REPO_ROOT / "assets/icons/app.ico").read_bytes()
    assert struct.unpack_from("<HHH", data) == (0, 1, 7)
    sizes = set()
    for index in range(7):
        width, height, _, _, planes, bits, length, offset = struct.unpack_from(
            "<BBBBHHII", data, 6 + index * 16
        )
        size = (width or 256, height or 256)
        sizes.add(size)
        assert planes == 0 and bits == 32
        frame = data[offset:offset + length]
        assert len(frame) == length and frame.startswith(b"\x89PNG\r\n\x1a\n")
        assert struct.unpack_from(">II", frame, 16) == size
        assert frame[25] == 6  # PNG RGBA, not an opaque RGB image.
    assert sizes == {(size, size) for size in (16, 24, 32, 48, 64, 128, 256)}
    script = (REPO_ROOT / "scripts/build-portable.ps1").read_text(encoding="utf-8")
    assert "'--icon', $appIcon" in script
    assert "assets/icons/app.ico" in script


@pytest.mark.parametrize("running", [False, True])
def test_duplicate_main_never_opens_or_changes_running_instance_logs(
    tmp_settings, monkeypatch, capsys, running,
):
    from ndr import portable

    tmp_settings.log_dir.mkdir(parents=True)
    log_path = tmp_settings.log_dir / "tasks.log"
    original = b"existing-running-instance-diagnostics\n"
    log_path.write_bytes(original)
    installed = []
    monkeypatch.setattr(sys, "argv", ["ndr-portable", "--no-browser"])
    monkeypatch.setattr(portable, "portable_settings", lambda **kwargs: tmp_settings)
    monkeypatch.setattr(portable, "start_diagnostics", lambda path: installed.append(path))
    monkeypatch.setattr(portable, "running_url",
                        lambda _: "http://127.0.0.1:8765" if running else None)
    monkeypatch.setattr(portable.time, "sleep", lambda _: None)
    with LibraryLock(tmp_settings.data_dir):
        result = portable.main()
    assert result == (0 if running else 1)
    assert installed == []
    assert log_path.read_bytes() == original
    output = capsys.readouterr()
    if running:
        assert "阅读器已经启动" in output.out
    else:
        assert "这份书库正在运行" in output.err
        assert "本次未初始化诊断日志" in output.err
        assert "tasks.log" not in output.err


@pytest.mark.parametrize(("code", "hint"), [
    ("missing_pages", "页面文件缺失"),
    ("port_in_use", "端口被占用"),
])
def test_main_keeps_controlled_startup_error_hints(tmp_settings, monkeypatch, capsys, code, hint):
    from ndr import portable
    from ndr.runtime_logging import StartupError

    def fail(*args, **kwargs):
        raise StartupError(code)

    monkeypatch.setattr(sys, "argv", ["ndr-portable", "--no-browser"])
    monkeypatch.setattr(portable, "portable_settings", lambda **kwargs: tmp_settings)
    monkeypatch.setattr(portable, "launch", fail)
    assert portable.main() == 1
    output = capsys.readouterr().err
    assert hint in output
    assert "logs/tasks.log" in output


def test_invalid_runtime_argument_does_not_claim_a_log_was_written(monkeypatch, capsys):
    from ndr import portable

    installed = []
    monkeypatch.setattr(sys, "argv", ["ndr-portable", "--no-browser", "--run-seconds", "0"])
    monkeypatch.setattr(portable, "start_diagnostics", lambda path: installed.append(path))
    assert portable.main() == 1
    output = capsys.readouterr().err
    assert "限时运行秒数必须大于 0" in output
    assert "本次未初始化诊断日志" in output
    assert "tasks.log" not in output
    assert installed == []


def test_configuration_failure_is_safe_and_does_not_claim_a_log(monkeypatch, capsys):
    from ndr import __main__ as server_main
    from ndr import portable

    def fail(*args, **kwargs):
        raise ValueError("private-configuration-text secret-key")

    monkeypatch.setattr(sys, "argv", ["ndr-portable", "--no-browser"])
    monkeypatch.setattr(portable, "portable_settings", fail)
    assert portable.main() == 1
    monkeypatch.setattr(server_main, "get_settings", fail)
    with pytest.raises(SystemExit) as stopped:
        server_main.main()
    assert stopped.value.code == 1
    output = capsys.readouterr().err
    assert output.count("配置读取失败") == 2
    assert output.count("本次未初始化诊断日志") == 2
    assert "tasks.log" not in output
    assert "private-configuration-text" not in output
    assert "secret-key" not in output
