"""Independent test databases cloned from a genuinely migrated, read-only template."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from ndr.config import Settings
from ndr.storage.migrate import run_migrations


def initialize_test_database(settings: Settings, template: Path) -> None:
    """Clone only a missing database; never replace another fixture's existing data."""
    if settings.database_path.resolve() == template.resolve():
        raise ValueError("The test database must not be the shared template")
    if settings.database_path.exists():
        run_migrations(settings)
        return
    settings.ensure_data_dir()
    with (
        closing(sqlite3.connect(template.resolve().as_uri() + "?mode=ro", uri=True)) as source,
        closing(sqlite3.connect(settings.database_path)) as destination,
    ):
        source.backup(destination)
