"""CI partitions must cover every test file once and match the workflow."""

import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[3]
spec = importlib.util.spec_from_file_location("ci_test_groups", ROOT / "backend/scripts/run_test_group.py")
assert spec and spec.loader
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_ci_groups_cover_all_test_files_once():
    root = ROOT / "backend/tests"
    groups = runner.test_groups(root)
    selected = [path for paths in groups.values() for path in paths]
    assert len(selected) == len(set(selected))
    assert set(selected) == set(root.rglob("test_*.py"))
    assert {path.name for path in groups["database"]} == runner.DATABASE_FILES


def test_workflow_matrix_matches_runner_and_keeps_full_results():
    workflow = yaml.safe_load((ROOT / ".github/workflows/schema-indexes.yml").read_text(encoding="utf-8"))
    strategy = workflow["jobs"]["tests"]["strategy"]
    assert set(strategy["matrix"]["group"]) == set(runner.TEST_GROUPS)
    assert strategy["fail-fast"] is False
    assert strategy["max-parallel"] == 4
    assert "github.workflow" in workflow["concurrency"]["group"]
    assert workflow["concurrency"]["cancel-in-progress"] is True
    assert set(workflow["jobs"]["database"]["needs"]) == {"checks", "tests"}


def test_unknown_test_directory_fails_instead_of_skipping(tmp_path):
    (tmp_path / "other").mkdir()
    (tmp_path / "other/test_new.py").touch()
    with pytest.raises(ValueError, match="Unassigned"):
        runner.test_groups(tmp_path)


def test_missing_database_registration_fails_instead_of_skipping(tmp_path):
    with pytest.raises(ValueError, match="registration"):
        runner.test_groups(tmp_path)


def test_runner_preserves_pytest_failure_code_and_reports_timings(monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr(runner.pytest, "main", lambda args: captured.extend(args) or 1)
    report = tmp_path / "results/unit.xml"
    base = tmp_path / "isolated"
    assert runner.main(["--group", "unit", "--junitxml", str(report), "--basetemp", str(base)]) == 1
    assert "--durations=20" in captured
    assert f"--junitxml={report}" in captured
    assert f"--basetemp={base}" in captured
    assert report.parent.is_dir()


def test_runner_collection_mode_does_not_execute_tests(monkeypatch):
    captured = []
    monkeypatch.setattr(runner.pytest, "main", lambda args: captured.extend(args) or 0)
    assert runner.main(["--group", "database", "--collect-only"]) == 0
    assert "--collect-only" in captured
