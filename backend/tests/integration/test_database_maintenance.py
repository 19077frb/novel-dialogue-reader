"""Safe offline maintenance on disposable libraries, never on real user data."""

from __future__ import annotations

import gzip
import sqlite3
from collections import namedtuple
from contextlib import closing
from unittest.mock import Mock

import pytest
from alembic import command
from sqlalchemy.orm import Session

from ndr.ingest.service import import_txt
from ndr.portable import LibraryInUseError, LibraryLock
from ndr.storage.engine import create_db_engine, head_revision
from ndr.storage.maintenance import (
    _business_columns,
    _logical_digest,
    compact_database,
    inspect_database,
)
from ndr.storage.migrate import build_alembic_config, run_migrations


def seed(settings):
    run_migrations(settings, revision="0018")
    engine = create_db_engine(settings)
    try:
        with Session(engine) as session:
            outcome = import_txt(session, settings, filename="sample.txt", raw=("第一章\n\n「你好。」\n" * 100).encode())
            session.commit()
            return outcome.book.id
    finally:
        engine.dispose()


def test_statistics_are_read_only_and_do_not_create_missing_databases(tmp_settings):
    with pytest.raises(sqlite3.OperationalError):
        inspect_database(tmp_settings)
    assert not tmp_settings.data_dir.exists()
    seed(tmp_settings)
    original = tmp_settings.database_path.read_bytes()
    usage = inspect_database(tmp_settings)
    assert usage["database_bytes"] == len(original)
    assert tmp_settings.database_path.read_bytes() == original
    assert not (tmp_settings.data_dir / "backups").exists()


def test_migration_compaction_and_backup_preserve_all_business_data(tmp_settings, tmp_path):
    seed(tmp_settings)
    with closing(sqlite3.connect(tmp_settings.database_path)) as db:
        old_columns = _business_columns(db)
        digest = _logical_digest(db)
        old_indexes = db.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    outcome = compact_database(tmp_settings)
    assert outcome["business_data_unchanged"]
    assert outcome["saved_bytes"] > 0
    assert outcome["after"]["reclaimable_bytes"] == 0
    with gzip.open(outcome["backup_path"], "rb") as archive:
        restored = tmp_path / "restored.sqlite3"
        restored.write_bytes(archive.read())
    with closing(sqlite3.connect(restored)) as db:
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0018",)
        assert _logical_digest(db) == digest
        assert db.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall() == old_indexes
    with closing(sqlite3.connect(tmp_settings.database_path)) as db:
        assert _logical_digest(db, columns_by_table=old_columns) == digest
        upgraded_digest = _logical_digest(db)
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == (head_revision(),)
    config = build_alembic_config(tmp_settings)
    command.downgrade(config, "0018")
    with closing(sqlite3.connect(tmp_settings.database_path)) as db:
        assert {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")} == {r[0] for r in old_indexes}
        assert _logical_digest(db) == digest
    run_migrations(tmp_settings)
    run_migrations(tmp_settings)  # Normal restarts must not recreate obsolete indexes.
    with closing(sqlite3.connect(tmp_settings.database_path)) as db:
        assert _logical_digest(db) == upgraded_digest
    second = compact_database(tmp_settings)
    assert second["business_data_unchanged"]
    assert second["saved_bytes"] >= 0  # Downgrade/re-upgrade freed rebuilt index pages.
    third = compact_database(tmp_settings)
    assert third["saved_bytes"] == 0


def test_maintenance_refuses_a_portable_library_in_use(tmp_settings):
    seed(tmp_settings)
    with LibraryLock(tmp_settings.data_dir), pytest.raises(LibraryInUseError):
        compact_database(tmp_settings)
    assert not (tmp_settings.data_dir / "backups").exists()


@pytest.mark.parametrize("journal", ["DELETE", "WAL"])
def test_sqlite_reader_blocks_exclusive_maintenance(tmp_settings, journal):
    seed(tmp_settings)
    with closing(sqlite3.connect(tmp_settings.database_path)) as reader:
        reader.execute(f"PRAGMA journal_mode={journal}")
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM books").fetchall()
        with pytest.raises(RuntimeError, match="locked"):
            compact_database(tmp_settings)
        reader.rollback()
    assert not (tmp_settings.data_dir / "backups").exists()


def test_active_jobs_are_not_changed_or_paused(tmp_settings):
    book_id = seed(tmp_settings)
    with closing(sqlite3.connect(tmp_settings.database_path)) as db:
        db.execute("INSERT INTO jobs (id,kind,book_id,state,range_json,budget_json,created_at,updated_at,version) VALUES ('active','INFERENCE',?,'QUEUED','{}','{}','2026-10-03','2026-10-03',1)", (book_id,))
        db.commit()
    with pytest.raises(RuntimeError, match="运行或排队任务"):
        compact_database(tmp_settings)
    with closing(sqlite3.connect(tmp_settings.database_path)) as db:
        assert db.execute("SELECT state FROM jobs WHERE id='active'").fetchone() == ("QUEUED",)
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0018",)


def test_lock_remains_held_during_backup_migration_and_validation(tmp_settings):
    seed(tmp_settings)
    checked = []

    def progress(message):
        with closing(sqlite3.connect(tmp_settings.database_path, timeout=0)) as other, pytest.raises(
            sqlite3.OperationalError, match="locked",
        ):
            other.execute("SELECT count(*) FROM books").fetchone()
        checked.append(message)

    compact_database(tmp_settings, progress=progress)
    assert len(checked) == 3


def test_changed_original_data_rolls_back_migration_before_compaction(tmp_settings, monkeypatch):
    seed(tmp_settings)
    with closing(sqlite3.connect(tmp_settings.database_path)) as db:
        before = _logical_digest(db)

    def corrupt_migration(settings, *, connection):
        run_migrations(settings, connection=connection)
        connection.exec_driver_sql("UPDATE books SET title='incorrect migration'")

    monkeypatch.setattr("ndr.storage.maintenance.run_migrations", corrupt_migration)
    with pytest.raises(RuntimeError, match="原有业务数据不一致"):
        compact_database(tmp_settings)
    with closing(sqlite3.connect(tmp_settings.database_path)) as db:
        assert _logical_digest(db) == before
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0018",)
        assert "identity_facts_json" not in _business_columns(db)["book_characters"]
    assert len(list((tmp_settings.data_dir / "backups").glob("*.sqlite3.gz"))) == 1


def test_new_fact_field_is_included_in_post_upgrade_compaction_digest(tmp_settings, monkeypatch):
    seed(tmp_settings)
    with closing(sqlite3.connect(tmp_settings.database_path)) as db:
        db.execute(
            "INSERT INTO book_characters (id,book_version_id,canonical_name,aliases_json,"
            "source,user_confirmed,name_locked,confirmation_source,presentation_history_json,"
            "version,created_at,updated_at) SELECT 'fact-person',id,'林舟','[]',"
            "'MODEL',0,0,'model','[]',1,'2026-10-06','2026-10-06' FROM book_versions LIMIT 1",
        )
        db.commit()
    real_digest = _logical_digest
    full_calls = 0

    def change_new_fact_before_final_check(db, **kwargs):
        nonlocal full_calls
        if not kwargs:
            full_calls += 1
            if full_calls == 2:
                db.execute("UPDATE book_characters SET identity_facts_json='[{}]'")
        return real_digest(db, **kwargs)

    monkeypatch.setattr("ndr.storage.maintenance._logical_digest", change_new_fact_before_final_check)
    with pytest.raises(RuntimeError, match="维护前后的业务数据不一致"):
        compact_database(tmp_settings)
    assert full_calls == 2
    assert len(list((tmp_settings.data_dir / "backups").glob("*.sqlite3.gz"))) == 1


@pytest.mark.parametrize("removed", ["table", "column"])
def test_old_schema_digest_does_not_hide_removed_fields(removed):
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("CREATE TABLE people (id TEXT PRIMARY KEY, name TEXT)")
        db.execute("INSERT INTO people VALUES ('a','林舟')")
        columns = _business_columns(db)
        if removed == "table":
            db.execute("DROP TABLE people")
        else:
            db.execute("ALTER TABLE people DROP COLUMN name")
        with pytest.raises(RuntimeError, match="原有业务表或字段"):
            _logical_digest(db, columns_by_table=columns)


@pytest.mark.parametrize("failure", ["disk", "backup"])
def test_preparation_failure_never_migrates_or_vacuums(tmp_settings, monkeypatch, failure):
    seed(tmp_settings)
    module = "ndr.storage.maintenance"
    if failure == "disk":
        usage = namedtuple("usage", "total used free")(100, 100, 0)
        monkeypatch.setattr(module + ".shutil.disk_usage", lambda _: usage)
    else:
        monkeypatch.setattr(module + "._backup", Mock(side_effect=OSError("backup failed")))
    with pytest.raises(RuntimeError):
        compact_database(tmp_settings)
    with closing(sqlite3.connect(tmp_settings.database_path)) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0018",)
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='ix_text_mappings_version_source'").fetchone()
