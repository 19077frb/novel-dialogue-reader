"""Optimized fixtures must retain data isolation and real migration behavior."""

import sqlite3
from contextlib import closing

import pytest

from fixtures.corrections import import_sample
from fixtures.database import initialize_test_database
from ndr.config import Settings
from ndr.storage.engine import head_revision
from ndr.storage.migrate import run_migrations


def test_template_has_no_business_data_and_copies_are_independent(tmp_settings, migrated_database_template):
    initialize_test_database(tmp_settings, migrated_database_template)
    second = Settings(data_dir=tmp_settings.data_dir.parent / "second", credential_backend="session")
    initialize_test_database(second, migrated_database_template)
    with closing(sqlite3.connect(tmp_settings.database_path)) as db:
        db.execute("CREATE TABLE isolated_marker (value TEXT)")
        db.execute("INSERT INTO isolated_marker VALUES ('only-first')")
        db.commit()
    for path in (migrated_database_template, second.database_path):
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("SELECT count(*) FROM books").fetchone() == (0,)
            assert db.execute("SELECT count(*) FROM jobs").fetchone() == (0,)
            assert db.execute("SELECT version_num FROM alembic_version").fetchone() == (head_revision(),)
            assert db.execute("SELECT name FROM sqlite_master WHERE name='isolated_marker'").fetchall() == []


def test_existing_historical_database_is_upgraded_not_replaced(tmp_settings, migrated_database_template):
    run_migrations(tmp_settings, revision="0019")
    with closing(sqlite3.connect(tmp_settings.database_path)) as db:
        db.execute("CREATE TABLE isolated_marker (value TEXT)")
        db.execute("INSERT INTO isolated_marker VALUES ('preserved')")
        db.commit()
    initialize_test_database(tmp_settings, migrated_database_template)
    with closing(sqlite3.connect(tmp_settings.database_path)) as db:
        assert db.execute("SELECT * FROM isolated_marker").fetchall() == [("preserved",)]
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == (head_revision(),)


@pytest.fixture()
def imported_client(fake_provider_client):
    return fake_provider_client, import_sample(fake_provider_client)


def test_later_migrated_fixture_keeps_live_client_data(imported_client, migrated_settings):
    client, imported = imported_client
    assert client.get(f"/api/books/{imported['book_id']}").status_code == 200
    with closing(sqlite3.connect(migrated_settings.database_path)) as db:
        assert db.execute("SELECT id FROM books").fetchall() == [(imported["book_id"],)]


def test_template_cannot_be_the_writable_target(migrated_database_template):
    settings = Settings(data_dir=migrated_database_template.parent, credential_backend="session")
    with pytest.raises(ValueError, match="shared template"):
        initialize_test_database(settings, migrated_database_template)
