"""Content-free local settlement; real file locks and fake providers only."""

import json
import sqlite3
import time

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ndr.diagnostics import log_failure, start_diagnostics, stop_diagnostics
from ndr.domain.enums import JobState
from ndr.jobs import local_finalization, scheduler
from ndr.jobs.roster import run_character_roster_job
from ndr.jobs.service import job_detail
from ndr.llm.adapters.fake import FakeProviderAdapter
from ndr.storage import run_archive
from ndr.storage.models import InferenceRun, Job
from ndr.storage.transactions import finish_local_write, local_write_session


def busy():
    error = sqlite3.OperationalError("private novel and SQL parameter")
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY
    error.sqlite_errorname = "SQLITE_BUSY"
    return OperationalError("private SQL", {"key": "secret"}, error)


@pytest.mark.parametrize("method", ["callback", "session"])
def test_real_file_writer_wait_retries_fresh_transaction(migrated_client, monkeypatch, method):
    factory = migrated_client.app.state.session_factory
    lock = factory()
    lock.execute(text("BEGIN IMMEDIATE"))
    released = []

    def unlock(_):
        released.append(True)
        lock.rollback()

    monkeypatch.setattr("ndr.storage.transactions.time.sleep", unlock)
    # Keep the test fast without changing the application's default timeout.
    with factory() as session:
        session.execute(text("PRAGMA busy_timeout=1"))
    try:
        if method == "callback":
            assert finish_local_write(factory, lambda session: 42) == 42
        else:
            with local_write_session(factory) as session:
                assert session.execute(text("SELECT 42")).scalar_one() == 42
        assert released
    finally:
        lock.close()


@pytest.mark.parametrize("kind", ["roster", "roster_repair", "dialogue"])
def test_returned_receipt_survives_persistent_lock_without_redispatch(
    fake_provider_client, monkeypatch, kind,
):
    client = fake_provider_client
    factory, settings = client.app.state.session_factory, client.app.state.settings
    if kind == "dialogue":
        from tests.integration.test_expression_pipeline import StageAdapter, prepare
        create, _ = prepare(client)
        job_id = create("pending-local-receipt")["id"]
        with factory() as session:
            job = session.get(Job, job_id)
            job.budget_json = json.dumps({**json.loads(job.budget_json), "max_recheck_rounds": 0})
            session.commit()
        adapter = StageAdapter(["林舟"])
        run = scheduler.run_job
    else:
        from tests.integration.test_sourced_roster_jobs import prepare, response
        _, job = prepare(client)
        if kind == "roster_repair":
            from tests.integration.test_roster_pipeline import update
            update(client, job)
        job_id = job["id"]
        adapter = FakeProviderAdapter(script=[response()])
        run = run_character_roster_job
    save = run_archive.save_run_archive
    blocked = [True]

    def fail_returned(*args, **kwargs):
        if blocked[0] and kwargs.get("phase") == "returned":
            raise busy()
        return save(*args, **kwargs)

    monkeypatch.setattr(run_archive, "save_run_archive", fail_returned)
    monkeypatch.setattr("ndr.jobs.roster_pipeline.save_run_archive", fail_returned)
    monkeypatch.setattr("ndr.storage.transactions.time.sleep", lambda _: None)
    result = run(factory, settings, job_id=job_id, adapter_factory=lambda *_: adapter)
    assert result.state is JobState.RUNNING
    assert local_finalization.settlement_pending(job_id)
    with factory() as session:
        assert job_detail(session, session.get(Job, job_id)).progress["stage"] == "local_finalization_pending"
    run(factory, settings, job_id=job_id, adapter_factory=lambda *_: pytest.fail("redispatch"))
    blocked[0] = False
    local_finalization.retry_settlements(factory)
    assert not local_finalization.settlement_pending(job_id)
    with factory() as session:
        stored = session.get(Job, job_id)
        assert stored.state is JobState.FAILED
        attempts = list(session.scalars(select(InferenceRun).where(InferenceRun.job_id == job_id)))
        assert len(attempts) == len(adapter.calls) == 1
        assert run_archive.decode_archive(attempts[0].call_archive)["phase"] == "returned"
        stored.state = JobState.QUEUED
        session.commit()
    resumed = run(factory, settings, job_id=job_id, adapter_factory=lambda *_: adapter)
    assert resumed.state is JobState.COMPLETED
    assert len(adapter.calls) == 1


def test_failed_exception_settlement_is_retried_after_worker_exit(fake_provider_client, monkeypatch):
    from tests.integration.test_sourced_roster_jobs import prepare
    client = fake_provider_client
    _, job = prepare(client)
    factory, settings = client.app.state.session_factory, client.app.state.settings
    commit = Session.commit
    blocked = [True]

    def fail_state(session):
        if blocked[0] and any(isinstance(row, Job) and row.state is JobState.FAILED for row in session.dirty):
            raise busy()
        return commit(session)

    monkeypatch.setattr(Session, "commit", fail_state)
    monkeypatch.setattr("ndr.jobs.roster._run_character_roster_job",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("private text")))
    monkeypatch.setattr("ndr.storage.transactions.time.sleep", lambda _: None)
    run_character_roster_job(factory, settings, job_id=job["id"])
    assert local_finalization.settlement_pending(job["id"])
    blocked[0] = False
    local_finalization.retry_settlements(factory)
    with factory() as session:
        stored = session.get(Job, job["id"])
        assert stored.state is JobState.FAILED
        assert "private" not in stored.last_error


def test_dialogue_planning_precedes_writer_and_prepare_commit_retries(fake_provider_client, monkeypatch):
    from tests.integration.test_expression_pipeline import StageAdapter, prepare
    client = fake_provider_client
    create, factory = prepare(client)
    job_id = create("prepare-lock")["id"]
    load = scheduler.load_window_inputs
    checked = []

    def check_read_only(session, *args, **kwargs):
        raw_connection = session.connection().connection.driver_connection
        checked.append(not raw_connection.in_transaction)
        return load(session, *args, **kwargs)

    commit = Session.commit
    failures = []

    def fail_preparation_once(session):
        if not failures and any(isinstance(row, Job) and row.state is JobState.RUNNING
                                for row in session.dirty):
            failures.append(True)
            raise busy()
        return commit(session)

    monkeypatch.setattr(scheduler, "load_window_inputs", check_read_only)
    monkeypatch.setattr(Session, "commit", fail_preparation_once)
    monkeypatch.setattr("ndr.storage.transactions.time.sleep", lambda _: None)
    adapter = StageAdapter(["林舟"])
    outcome = scheduler.run_job(factory, client.app.state.settings, job_id=job_id,
                                adapter_factory=lambda *_: adapter)
    assert outcome.state is JobState.COMPLETED, outcome.errors
    assert checked and all(checked)
    assert failures


def test_diagnostics_are_bounded_and_do_not_log_exception_content(tmp_path):
    handler = start_diagnostics(tmp_path)
    try:
        assert handler.maxBytes == 5 * 1024 * 1024 and handler.backupCount == 3
        try:
            raise busy()
        except OperationalError as error:
            log_failure("test_write", "safe-job-id", error)
        handler.flush()
        content = (tmp_path / "tasks.log").read_text(encoding="utf-8")
        assert "safe-job-id" in content and "SQLITE_BUSY" in content
        assert "private" not in content and "secret" not in content
    finally:
        stop_diagnostics(handler)


def test_lifespan_monitor_settles_without_user_refresh(migrated_client):
    factory = migrated_client.app.state.session_factory
    finished = []
    local_finalization.defer_settlement(factory, "monitor-only-test", lambda: finished.append(True))
    deadline = time.monotonic() + 5
    while not finished and time.monotonic() < deadline:
        time.sleep(0.02)
    assert finished == [True]
    assert not local_finalization.settlement_pending("monitor-only-test")
