"""Run complete, disjoint CI groups without selecting by changed source files."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

TEST_GROUPS = ("unit", "database", "integration-1", "integration-2")
DATABASE_FILES = frozenset({
    "test_schema.py", "test_index_policy.py", "test_database_maintenance.py",
    "test_query_performance.py", "test_upgrade_preserves_data.py", "test_test_database.py",
})


def test_groups(root: Path) -> dict[str, list[Path]]:
    files = sorted(root.rglob("test_*.py"))
    unknown = [path for path in files
               if path.relative_to(root).parts[0] not in {"unit", "integration"}]
    if unknown:
        raise ValueError(f"Unassigned test directories: {unknown}")
    groups: dict[str, list[Path]] = {name: [] for name in TEST_GROUPS}
    ordinary = []
    for path in files:
        if path.relative_to(root).parts[0] == "unit":
            groups["unit"].append(path)
        elif path.name in DATABASE_FILES:
            groups["database"].append(path)
        else:
            ordinary.append(path)
    missing = DATABASE_FILES - {path.name for path in groups["database"]}
    if missing:
        raise ValueError(f"Database test registration no longer exists: {sorted(missing)}")
    for index, path in enumerate(ordinary):
        groups[f"integration-{index % 2 + 1}"].append(path)
    if any(not paths for paths in groups.values()):
        raise ValueError("A CI test group is empty")
    return groups


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=TEST_GROUPS, required=True)
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--junitxml", type=Path)
    parser.add_argument("--basetemp", type=Path)
    args = parser.parse_args(argv)
    try:
        files = test_groups(Path(__file__).resolve().parents[1] / "tests")[args.group]
    except ValueError as error:
        parser.error(str(error))
    pytest_args = [str(path) for path in files] + ["--durations=20", "--durations-min=0.05"]
    if args.collect_only:
        pytest_args.append("--collect-only")
    if args.basetemp:
        pytest_args.append(f"--basetemp={args.basetemp}")
    if args.junitxml:
        args.junitxml.parent.mkdir(parents=True, exist_ok=True)
        pytest_args.append(f"--junitxml={args.junitxml}")
    return int(pytest.main(pytest_args))


if __name__ == "__main__":
    raise SystemExit(main())
