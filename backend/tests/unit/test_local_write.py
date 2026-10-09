from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from ndr.storage.transactions import finish_local_write


def test_sqlite_real_writer_lock_is_retried_before_read_and_savepoint(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'isolated.sqlite3'}",
                           connect_args={"timeout": 0})
    with engine.begin() as connection:
        connection.execute(text("PRAGMA journal_mode=WAL"))
        connection.execute(text("CREATE TABLE example (value INTEGER)"))
        connection.execute(text("INSERT INTO example VALUES (0)"))
    factory = sessionmaker(engine)
    writer = engine.connect()
    writer.execute(text("BEGIN IMMEDIATE"))
    writer.execute(text("UPDATE example SET value=1"))
    sleeps = []
    calls = []

    def release_writer(delay):
        sleeps.append(delay)
        writer.commit()

    monkeypatch.setattr("ndr.storage.transactions.time.sleep", release_writer)

    def finalize(session):
        calls.append(session.scalar(text("SELECT value FROM example")))
        with session.begin_nested():
            session.execute(text("UPDATE example SET value=2"))
        return "saved"

    try:
        assert finish_local_write(factory, finalize) == "saved"
        assert sleeps == [0.1] and calls == [1]
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT value FROM example")) == 2
    finally:
        writer.close()
        engine.dispose()
