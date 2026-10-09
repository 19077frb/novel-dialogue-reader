"""A returned roster survives local finalization failures without another charge."""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from tests.integration.test_sourced_roster_jobs import prepare, response

from ndr.domain.enums import InferenceRunState, JobState
from ndr.jobs.roster import run_character_roster_job
from ndr.jobs.scheduler import reconcile_job
from ndr.llm.adapters.fake import FakeProviderAdapter
from ndr.llm.errors import ProviderError, ProviderErrorKind
from ndr.storage.models import BookCharacter, InferenceRun, Job
from ndr.storage.run_archive import decode_archive


def test_returned_roster_recovers_final_commit_failure_without_recharging(migrated_client, monkeypatch):
    client = migrated_client
    data, job = prepare(client)
    factory, settings = client.app.state.session_factory, client.app.state.settings
    original_commit = Session.commit
    failed = []

    def fail_final_commit(session):
        if not failed and any(isinstance(row, Job) and row.state == JobState.COMPLETED
                              for row in session.dirty):
            failed.append(True)
            raise RuntimeError("injected final commit failure")
        return original_commit(session)

    monkeypatch.setattr(Session, "commit", fail_final_commit)
    adapter = FakeProviderAdapter(script=[response()])
    outcome = run_character_roster_job(factory, settings, job_id=job["id"],
                                      adapter_factory=lambda *_: adapter)
    assert failed and outcome.state == JobState.FAILED and outcome.calls == 1
    with factory() as session:
        stored = session.get(Job, job["id"])
        assert stored.state == JobState.FAILED and "RuntimeError" in stored.last_error
        attempt = session.scalar(select(InferenceRun).where(InferenceRun.job_id == job["id"]))
        assert json.loads(attempt.usage_json)["total_tokens"] == 19
        assert attempt.elapsed_ms is not None
        archived = attempt.call_archive
        assert decode_archive(archived)["phase"] == "returned"

    def no_new_call(*_):
        pytest.fail("Recovery must not even construct a provider adapter")

    monkeypatch.setattr("ndr.jobs.roster._build_adapter", no_new_call)
    recovery = client.get(f"/api/jobs/{job['id']}/recovery").json()["data"]
    assert recovery["actions"][0]["action"] == "resume"
    assert recovery["actions"][0]["paid"] is False
    assert client.post(f"/api/jobs/{job['id']}/resume").status_code == 202
    assert client.get(f"/api/jobs/{job['id']}").json()["data"]["state"] == "COMPLETED"
    again = run_character_roster_job(factory, settings, job_id=job["id"],
                                    adapter_factory=no_new_call)
    assert again.state == JobState.COMPLETED and again.calls == 0
    with factory() as session:
        attempts = list(session.scalars(select(InferenceRun).where(InferenceRun.job_id == job["id"])))
        assert len(attempts) == 1 and attempts[0].call_archive == archived
        assert attempts[0].state == InferenceRunState.SUCCEEDED
        assert len(list(session.scalars(select(BookCharacter).where(
            BookCharacter.book_version_id == data["book_version_id"],
        )))) == 1


@pytest.mark.parametrize("pipeline", [False, True])
@pytest.mark.parametrize("failure", ["busy_once", "busy_always", "disk_error"])
def test_roster_finalization_retries_only_local_locks_atomically(
    migrated_client, monkeypatch, pipeline, failure,
):
    import sqlite3

    client = migrated_client
    _, job = prepare(client)
    if pipeline:
        from tests.integration.test_roster_pipeline import update
        update(client, job)
    factory, settings = client.app.state.session_factory, client.app.state.settings
    commit = Session.commit
    attempts = 0

    def fail_final_commit(session):
        nonlocal attempts
        if any(isinstance(row, Job) and row.state == JobState.COMPLETED
               for row in session.dirty):
            attempts += 1
            if failure != "busy_once" or attempts == 1:
                error = sqlite3.OperationalError("private database detail")
                error.sqlite_errorcode = (sqlite3.SQLITE_IOERR if failure == "disk_error"
                                          else sqlite3.SQLITE_BUSY)
                error.sqlite_errorname = ("SQLITE_IOERR" if failure == "disk_error"
                                          else "SQLITE_BUSY")
                raise OperationalError("private SQL", {"secret": "private"}, error)
        return commit(session)

    monkeypatch.setattr(Session, "commit", fail_final_commit)
    monkeypatch.setattr("ndr.storage.transactions.time.sleep", lambda *_: None)
    adapter = FakeProviderAdapter(script=[response()])
    outcome = run_character_roster_job(factory, settings, job_id=job["id"],
                                      adapter_factory=lambda *_: adapter)
    assert len(adapter.calls) == 1
    assert attempts == {"busy_once": 2, "busy_always": 3, "disk_error": 1}[failure]
    with factory() as session:
        people = list(session.scalars(select(BookCharacter)))
        stored = session.get(Job, job["id"])
        runs = list(session.scalars(select(InferenceRun).where(InferenceRun.job_id == job["id"])))
        assert len(runs) == 1
        assert decode_archive(runs[0].call_archive)["phase"] == "returned"
        if failure == "busy_once":
            assert outcome.state == stored.state == JobState.COMPLETED
            assert len(people) == 1
        else:
            assert outcome.state == stored.state == JobState.FAILED
            assert not people
            expected = "SQLITE_IOERR" if failure == "disk_error" else "SQLITE_BUSY"
            assert expected in stored.last_error and "private" not in stored.last_error


@pytest.mark.parametrize("pipeline", [False, True])
def test_stop_between_finalization_lock_retries_does_not_save_people(
    migrated_client, monkeypatch, pipeline,
):
    import sqlite3

    client = migrated_client
    _, job = prepare(client)
    if pipeline:
        from tests.integration.test_roster_pipeline import update
        update(client, job)
    factory, settings = client.app.state.session_factory, client.app.state.settings
    commit = Session.commit
    failed = []

    def fail_once(session):
        if not failed and any(isinstance(row, Job) and row.state == JobState.COMPLETED
                              for row in session.dirty):
            failed.append(True)
            raise OperationalError(None, None, sqlite3.OperationalError("database is locked"))
        return commit(session)

    def pause(_):
        with factory() as session:
            session.get(Job, job["id"]).state = JobState.PAUSING
            session.commit()

    monkeypatch.setattr(Session, "commit", fail_once)
    monkeypatch.setattr("ndr.storage.transactions.time.sleep", pause)
    adapter = FakeProviderAdapter(script=[response()])
    outcome = run_character_roster_job(factory, settings, job_id=job["id"],
                                      adapter_factory=lambda *_: adapter)
    assert outcome.state == JobState.PAUSED and len(adapter.calls) == 1
    with factory() as session:
        assert not list(session.scalars(select(BookCharacter)))
        assert session.get(Job, job["id"]).state == JobState.PAUSED


def test_unknown_roster_requires_explicit_reconciliation_before_new_call(migrated_client):
    client = migrated_client
    _, job = prepare(client)
    factory, settings = client.app.state.session_factory, client.app.state.settings

    class Timeout:
        async def generate_labels(self, request):
            raise ProviderError(ProviderErrorKind.TIMEOUT, "fixture timeout")

    outcome = run_character_roster_job(factory, settings, job_id=job["id"],
                                      adapter_factory=lambda *_: Timeout())
    assert outcome.state == JobState.NEEDS_RECONCILIATION
    blocked = run_character_roster_job(factory, settings, job_id=job["id"],
                                      adapter_factory=lambda *_: pytest.fail("No automatic resend"))
    assert blocked.state == JobState.NEEDS_RECONCILIATION
    with factory() as session:
        reconcile_job(session, session.get(Job, job["id"]), action="retry")
        session.commit()
    adapter = FakeProviderAdapter(script=[response()])
    retried = run_character_roster_job(factory, settings, job_id=job["id"],
                                     adapter_factory=lambda *_: adapter)
    assert retried.state == JobState.COMPLETED and retried.calls == 1


def test_roster_pause_keeps_returned_result_and_reentry_does_not_call(migrated_client):
    client = migrated_client
    _, job = prepare(client)
    factory, settings = client.app.state.session_factory, client.app.state.settings

    class PauseAfterReturn:
        async def generate_labels(self, request):
            nested = run_character_roster_job(factory, settings, job_id=job["id"],
                                             adapter_factory=lambda *_: pytest.fail("Duplicate call"))
            assert nested.state == JobState.RUNNING and nested.calls == 0
            with factory() as session:
                session.get(Job, job["id"]).state = JobState.PAUSING
                session.commit()
            return response()

    outcome = run_character_roster_job(factory, settings, job_id=job["id"],
                                      adapter_factory=lambda *_: PauseAfterReturn())
    assert outcome.state == JobState.PAUSED
    with factory() as session:
        session.get(Job, job["id"]).state = JobState.QUEUED
        session.commit()
    resumed = run_character_roster_job(factory, settings, job_id=job["id"],
                                      adapter_factory=lambda *_: pytest.fail("Replay expected"))
    assert resumed.state == JobState.COMPLETED and resumed.calls == 0


@pytest.mark.parametrize("damage", ["fingerprint", "chapter", "profile", "incomplete"])
def test_returned_roster_rejects_damaged_binding_without_new_call(migrated_client, damage):
    from ndr.storage.run_archive import encode_archive

    client = migrated_client
    _, job = prepare(client)
    factory, settings = client.app.state.session_factory, client.app.state.settings
    adapter = FakeProviderAdapter(script=[response()])
    assert run_character_roster_job(factory, settings, job_id=job["id"],
                                   adapter_factory=lambda *_: adapter).state == JobState.COMPLETED
    with factory() as session:
        stored = session.get(Job, job["id"])
        stored.state = JobState.RUNNING
        attempt = session.scalar(select(InferenceRun).where(InferenceRun.job_id == job["id"]))
        attempt.state = InferenceRunState.DISPATCHED
        if damage == "fingerprint":
            attempt.request_fingerprint = "wrong"
        elif damage == "chapter":
            attempt.window_id = "roster:another-chapter"
        elif damage == "profile":
            attempt.profile_snapshot_json = "{}"
        else:
            archive = decode_archive(attempt.call_archive)
            archive["phase"] = "prepared"
            attempt.call_archive = encode_archive(archive)
        session.commit()
    outcome = run_character_roster_job(factory, settings, job_id=job["id"],
                                      adapter_factory=lambda *_: pytest.fail("No paid fallback"))
    assert outcome.state == JobState.NEEDS_RECONCILIATION and outcome.calls == 0
    actions = client.get(f"/api/jobs/{job['id']}/recovery").json()["data"]["actions"]
    assert {item["action"] for item in actions} == {"reconcile_keep", "reconcile_retry"}


@pytest.mark.parametrize("error_kind", [None, "INVALID_OUTPUT"])
def test_only_local_validation_failure_offers_saved_result_revalidation(migrated_client, monkeypatch,
                                                                       error_kind):
    from ndr.storage.run_archive import encode_archive

    client = migrated_client
    _, job = prepare(client)
    factory, settings = client.app.state.session_factory, client.app.state.settings
    adapter = FakeProviderAdapter(script=[response()])
    assert run_character_roster_job(factory, settings, job_id=job["id"],
                                   adapter_factory=lambda *_: adapter).state == JobState.COMPLETED
    with factory() as session:
        stored = session.get(Job, job["id"])
        stored.state = JobState.FAILED
        attempt = session.scalar(select(InferenceRun).where(InferenceRun.job_id == job["id"]))
        attempt.state = InferenceRunState.FAILED
        archive = decode_archive(attempt.call_archive)
        archive["error"] = {"kind": error_kind, "message": "previous validation failure"}
        attempt.call_archive = encode_archive(archive)
        saved_archive = attempt.call_archive
        session.commit()
    action = client.get(f"/api/jobs/{job['id']}/recovery").json()["data"]["actions"][0]
    assert action["action"] == ("resume" if error_kind is None else "run")
    if error_kind is not None:
        return
    assert action["paid"] is False
    monkeypatch.setattr("ndr.jobs.roster._build_adapter",
                        lambda *_: pytest.fail("Local revalidation must not call provider"))
    assert client.post(f"/api/jobs/{job['id']}/resume").status_code == 202
    assert client.get(f"/api/jobs/{job['id']}").json()["data"]["state"] == "COMPLETED"
    with factory() as session:
        attempts = list(session.scalars(select(InferenceRun).where(InferenceRun.job_id == job["id"])))
        assert len(attempts) == 1 and attempts[0].call_archive == saved_archive
