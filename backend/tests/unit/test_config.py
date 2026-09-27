from __future__ import annotations

from pathlib import Path

from ndr.config import REPO_ROOT, Settings, default_data_dir


def test_default_data_dir_is_absolute_and_inside_repo() -> None:
    resolved = default_data_dir()
    assert resolved.is_absolute()
    assert resolved == (REPO_ROOT / "data").resolve()


def test_data_dir_relative_input_is_resolved_to_absolute(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    settings = Settings(data_dir=Path("relative-data"))
    assert settings.data_dir.is_absolute()
    assert settings.data_dir == (tmp_path / "relative-data").resolve()


def test_env_prefix_overrides_host_and_port(monkeypatch) -> None:
    monkeypatch.setenv("NDR_HOST", "127.0.0.1")
    monkeypatch.setenv("NDR_PORT", "8899")
    settings = Settings()
    assert settings.host == "127.0.0.1"
    assert settings.port == 8899


def test_database_path_sits_under_data_dir(tmp_settings: Settings) -> None:
    assert tmp_settings.database_path.parent == tmp_settings.data_dir
