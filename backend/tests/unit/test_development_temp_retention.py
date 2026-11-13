"""Exercise the real pytest configuration in isolated child processes."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _run_pytest(workspace: Path, *, fail: bool = False, basetemp: Path | None = None):
    test_file = workspace / "test_sample.py"
    test_file.write_text(
        "import os\n"
        "def test_sample(tmp_path, tmp_path_factory, pytestconfig):\n"
        "    assert pytestconfig.getini('tmp_path_retention_policy') == 'failed'\n"
        "    assert int(pytestconfig.getini('tmp_path_retention_count')) == 3\n"
        "    (tmp_path / 'sample.txt').write_text('isolated', encoding='utf-8')\n"
        "    template = tmp_path_factory.mktemp('template')\n"
        "    (template / 'empty.sqlite3').touch()\n"
        "    assert os.environ.get('RETENTION_TEST_FAIL') != '1'\n"
        "def test_successful(tmp_path):\n"
        "    (tmp_path / 'passed.txt').touch()\n",
        encoding="utf-8",
    )
    temp_root = workspace / "sessions"
    temp_root.mkdir(exist_ok=True)
    env = dict(os.environ)
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)
    env.update(
        PYTEST_DEBUG_TEMPROOT=str(temp_root),
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        RETENTION_TEST_FAIL="1" if fail else "0",
    )
    command = [
        sys.executable, "-m", "pytest", "-c", str(CONFIG),
        "--confcutdir", str(workspace), "-p", "no:cacheprovider", str(test_file),
    ]
    if basetemp is not None:
        command.extend(["--basetemp", str(basetemp)])
    result = subprocess.run(
        command, cwd=workspace, env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=30, check=False,
    )
    assert result.returncode == (1 if fail else 0), result.stdout + result.stderr
    return sorted(path for path in temp_root.glob("pytest-of-*/pytest-*")
                  if path.name.removeprefix("pytest-").isdigit())


def test_successful_session_cleans_test_data_and_session_template(tmp_path):
    sentinel = tmp_path / "unrelated.txt"
    sentinel.write_text("keep", encoding="utf-8")
    assert _run_pytest(tmp_path) == []
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_failed_sessions_keep_at_most_three_and_clean_successful_test_data(tmp_path):
    for run in range(4):
        sessions = _run_pytest(tmp_path, fail=True)
        assert len(sessions) == min(run + 1, 3)
    assert sessions[0].name == "pytest-1"
    assert all(list(path.glob("test_sample*/sample.txt")) for path in sessions)
    assert all(list(path.glob("template*/empty.sqlite3")) for path in sessions)
    assert not any(list(path.glob("test_successful*")) for path in sessions)


def test_explicit_basetemp_retains_session_root_and_only_resets_that_directory(tmp_path):
    base = tmp_path / "explicit-base"
    base.mkdir()
    old_file = base / "old.txt"
    old_file.touch()
    sentinel = tmp_path / "outside.txt"
    sentinel.touch()
    assert _run_pytest(tmp_path, basetemp=base) == []
    assert not old_file.exists()
    assert not list(base.glob("test_sample*/sample.txt"))
    assert list(base.glob("template*/empty.sqlite3"))
    assert sentinel.exists()
