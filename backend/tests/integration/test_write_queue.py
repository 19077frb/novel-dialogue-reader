"""FIFO writes on isolated SQLite files; no real books or model calls."""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import Integer, String, insert, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ndr.jobs.local_finalization import settlement_progress
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.transactions import local_write_session, sqlite_lock_error, transaction
from ndr.storage.write_queue import WriteQueue, queue_for_engine, writer_job_scope, writer_progress


class Tables(DeclarativeBase):
    pass


class Row(Tables):
    __tablename__ = "queue_test"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[str] = mapped_column(String)


@pytest.fixture
def factory(engine):
    Tables.metadata.create_all(engine)
    return create_session_factory(engine)


def wait_until(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline, "worker did not reach expected phase"
        time.sleep(0.005)


def test_fifo_cross_engine_writes_do_not_block_reads(factory, tmp_settings):
    second_engine = create_db_engine(tmp_settings)
    other_factory = create_session_factory(second_engine)
    assert queue_for_engine(factory.kw["bind"]) is queue_for_engine(second_engine)
    order = []

    @writer_job_scope
    def save(*, job_id, number):
        with transaction(other_factory) as session:
            order.append(number)
            session.add(Row(id=number, value=job_id))

    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            with transaction(factory) as session:
                session.add(Row(id=0, value="first"))
                session.flush()
                futures = []
                for number in range(1, 4):
                    job_id = f"fifo-{number}"
                    futures.append(pool.submit(save, job_id=job_id, number=number))
                    wait_until(lambda job_id=job_id: writer_progress(job_id) is not None)
                    assert writer_progress(job_id)["stage"] == "write_queue_waiting"
                # WAL readers remain independent of the writer/queue.
                with other_factory() as reader:
                    assert list(reader.scalars(select(Row))) == []
                assert order == []
            for future in futures:
                future.result(timeout=5)
        assert order == [1, 2, 3]
        with factory() as session:
            assert len(list(session.scalars(select(Row)))) == 4
        assert all(writer_progress(f"fifo-{i}") is None for i in range(1, 4))
    finally:
        second_engine.dispose()


@pytest.mark.parametrize("kind", ["orm", "core", "text", "immediate"])
def test_direct_session_write_paths_hold_until_root_end(factory, kind):
    with factory() as writer:
        if kind == "orm":
            writer.add(Row(id=1, value="orm"))
            writer.flush()
        elif kind == "core":
            writer.execute(insert(Row).values(id=1, value="core"))
        elif kind == "text":
            writer.execute(text("INSERT INTO queue_test VALUES (1, 'text')"))
        else:
            writer.execute(text("BEGIN IMMEDIATE"))
        assert "ndr_writer_turn" in writer.info
        with ThreadPoolExecutor(max_workers=1) as pool:
            @writer_job_scope
            def next_write(*, job_id):
                with transaction(factory) as session:
                    session.add(Row(id=2, value="next"))
            future = pool.submit(next_write, job_id=f"next-{kind}")
            wait_until(lambda: writer_progress(f"next-{kind}") is not None)
            assert not future.done()
            writer.commit()
            future.result(timeout=5)
        assert "ndr_writer_turn" not in writer.info


def test_nested_transaction_does_not_release_writer(factory):
    with local_write_session(factory, job_id="nested") as session:
        with session.begin_nested():
            session.add(Row(id=1, value="nested"))
            session.flush()
        assert writer_progress("nested")["stage"] == "write_queue_active"
        with pytest.raises(ValueError), session.begin_nested():
            raise ValueError("rollback savepoint only")
        assert writer_progress("nested") is not None
        assert settlement_progress("nested", {"other": True}) == {
            "other": True, "stage": "write_queue_active", "message": "正在保存任务数据"}
    assert writer_progress("nested") is None


@pytest.mark.parametrize("ending", ["rollback", "close", "flush_error"])
def test_failed_or_closed_session_releases_turn(factory, ending):
    writer = factory()
    writer.add(Row(id=1, value="one"))
    writer.flush()
    if ending == "flush_error":
        writer.add(Row(id=1, value="duplicate"))
        with pytest.raises(IntegrityError):
            writer.flush()
        # A failed flush keeps the root reservation until explicit rollback.
        assert "ndr_writer_turn" in writer.info
        writer.rollback()
    else:
        getattr(writer, ending)()
    assert "ndr_writer_turn" not in writer.info
    writer.close()
    with transaction(factory) as session:
        session.add(Row(id=2, value="survives"))


def test_queue_timeout_removes_ticket_and_next_writer_runs(monkeypatch):
    queue = WriteQueue()
    holder = queue.acquire("holder")
    logs = []
    monkeypatch.setattr("ndr.storage.write_queue.logger.warning",
                        lambda message, *args: logs.append(message % args))
    with ThreadPoolExecutor(max_workers=1) as pool:
        expired = pool.submit(queue.acquire, "expired", timeout=0.02)
        with pytest.raises(OperationalError) as error:
            expired.result(timeout=5)
        assert sqlite_lock_error(error.value)
        assert writer_progress("expired") is None
        next_writer = pool.submit(queue.acquire, "next")
        wait_until(lambda: writer_progress("next") is not None)
        queue.release(holder)
        queue.release(next_writer.result(timeout=5))
    assert writer_progress("next") is None
    assert "write_queue_timeout job=expired" in logs[0]
    assert "SQL" not in logs[0] and "queue_test" not in logs[0]


def test_separate_databases_and_model_phase_have_no_shared_turn(factory, tmp_path):
    from ndr.config import Settings
    other_engine = create_db_engine(Settings(data_dir=tmp_path / "other"))
    try:
        assert queue_for_engine(factory.kw["bind"]) is not queue_for_engine(other_engine)
        @writer_job_scope
        def workflow(*, job_id):
            with transaction(factory) as session:
                session.add(Row(id=1, value="prepared"))
            # Represents the provider call: no write turn survives the commit.
            assert writer_progress(job_id) is None
            with ThreadPoolExecutor(max_workers=1) as pool:
                def another_writer():
                    with transaction(factory) as session:
                        session.add(Row(id=2, value="parallel"))
                pool.submit(another_writer).result(timeout=5)
        workflow(job_id="model-phase")
    finally:
        other_engine.dispose()


def test_same_thread_second_session_fails_without_deadlocking(factory):
    with transaction(factory), factory() as other:
        with pytest.raises(OperationalError) as error:
            other.execute(insert(Row).values(id=1, value="nested session"))
        assert sqlite_lock_error(error.value)


def test_after_commit_session_can_reenter_without_duplicate_release(factory):
    with factory() as session:
        for number in range(3):
            session.add(Row(id=number, value="reused"))
            session.commit()
            assert "ndr_writer_turn" not in session.info


@pytest.mark.parametrize("kind", ["dialogue", "roster"])
def test_real_job_releases_turn_during_fake_provider_call(fake_provider_client, kind):
    from tests.integration.test_expression_pipeline import StageAdapter, prepare
    from tests.integration.test_sourced_roster_jobs import prepare as prepare_roster
    from tests.integration.test_sourced_roster_jobs import response

    from ndr.domain.enums import JobState
    from ndr.jobs.roster import run_character_roster_job
    from ndr.jobs.scheduler import run_job
    from ndr.llm.adapters.fake import FakeProviderAdapter

    client = fake_provider_client
    factory = client.app.state.session_factory
    if kind == "dialogue":
        create, _ = prepare(client)
        job = create("queue-provider-dialogue")
        parent = StageAdapter
    else:
        _, job = prepare_roster(client)
        parent = FakeProviderAdapter
    calls = []

    class CheckingAdapter(parent):
        async def generate_labels(self, payload):
            assert writer_progress(job["id"]) is None
            with ThreadPoolExecutor(max_workers=1) as pool:
                def independent_write():
                    with transaction(factory) as session:
                        # Even an empty write context must obtain its turn.
                        assert "ndr_writer_turn" in session.info
                pool.submit(independent_write).result(timeout=5)
            calls.append(True)
            return await super().generate_labels(payload)

    adapter = CheckingAdapter(["林舟"]) if kind == "dialogue" else CheckingAdapter(script=[response()])
    runner = run_job if kind == "dialogue" else run_character_roster_job
    outcome = runner(factory, client.app.state.settings, job_id=job["id"],
                     adapter_factory=lambda *_: adapter)
    assert outcome.state is JobState.COMPLETED and calls
    assert writer_progress(job["id"]) is None
