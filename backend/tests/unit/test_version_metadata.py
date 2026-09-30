"""Keep package, runtime, lockfile and API release versions aligned."""

import json
import tomllib
from pathlib import Path

from ndr import __version__


def test_release_metadata_versions_match() -> None:
    backend = Path(__file__).resolve().parents[2]
    root = backend.parent
    project = tomllib.loads((backend / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((backend / "uv.lock").read_text(encoding="utf-8"))
    assert project["project"]["version"] == __version__
    assert next(item for item in lock["package"] if item["name"] == "ndr")["version"] == __version__
    for name in ("package.json", "package-lock.json"):
        package = json.loads((root / "frontend" / name).read_text(encoding="utf-8"))
        assert package["version"] == __version__
        if name == "package-lock.json":
            assert package["packages"][""]["version"] == __version__
    schema = json.loads((root / "docs" / "openapi.json").read_text(encoding="utf-8"))
    assert schema["info"]["version"] == __version__
