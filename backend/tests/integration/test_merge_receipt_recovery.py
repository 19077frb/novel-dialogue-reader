"""Isolated merge receipts survive local storage contention without paid retries."""

import json
import sqlite3

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from tests.integration.test_character_directory import _auto_job, _merge_group
from tests.integration.test_character_directory import populated as populated

from ndr.characters import auto_merge
from ndr.domain.enums import InferenceRunState, JobState
from ndr.jobs import local_finalization
from ndr.llm.adapters.fake import FakeProviderAdapter
from ndr.llm.errors import ProviderError, ProviderErrorKind
from ndr.recovery.service import job_recovery, recover_on_startup
from ndr.storage.models import BookCharacter, InferenceRun, Job
from ndr.storage.run_archive import decode_archive, encode_archive
from ndr.storage.transactions import transaction


@pytest.fixture(autouse=True)
def retain_debug_receipts(migrated_client):
    migrated_client.app.state.settings.retain_completed_call_details = True


def storage_error(code=sqlite3.SQLITE_BUSY):
    error = sqlite3.OperationalError("synthetic storage failure")
    error.sqlite_errorcode = code
    error.sqlite_errorname = "SQLITE_BUSY" if code == sqlite3.SQLITE_BUSY else "SQLITE_IOERR"
    return OperationalError("private SQL", {"private": "value"}, error)


def run(client, job, adapter):
    return auto_merge.run_auto_merge_job(
        client.app.state.session_factory, client.app.state.settings,
        job_id=job["id"], adapter_factory=lambda *_: adapter,
    )


@pytest.mark.parametrize("unknown", [False, True])
@pytest.mark.parametrize("code", [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_IOERR])
def test_merge_deferred_return_preserves_receipt_usage_and_never_dispatches_again(
    migrated_client, populated, monkeypatch, unknown, code,
):
    client, ids = migrated_client, populated
    factory = client.app.state.session_factory
    job, _ = _auto_job(client, ids)
    adapter = FakeProviderAdapter(script=[{"groups": [_merge_group(ids)]}],
                                  usage=None if unknown else {"total_tokens": 40})
    saved = auto_merge.save_run_archive
    blocked = [True]

    def save(*args, **kwargs):
        if blocked[0] and kwargs.get("phase") == "returned":
            raise storage_error(code)
        return saved(*args, **kwargs)

    monkeypatch.setattr(auto_merge, "save_run_archive", save)
    monkeypatch.setattr("ndr.storage.transactions.time.sleep", lambda _: None)
    try:
        assert run(client, job, adapter) == (JobState.RUNNING, 1)
        assert local_finalization.settlement_pending(job["id"])
        with factory() as session:
            attempt = session.scalar(select(InferenceRun).where(InferenceRun.job_id == job["id"]))
            assert attempt.state is InferenceRunState.DISPATCHED
            assert attempt.error_code is None
            assert decode_archive(attempt.call_archive)["phase"] == "prepared"
        assert run(client, job, adapter) == (JobState.RUNNING, 0)
        blocked[0] = False
        local_finalization.retry_settlements(factory)
        assert not local_finalization.settlement_pending(job["id"])
        with factory() as session:
            current = session.get(Job, job["id"])
            assert current.state is JobState.COMPLETED
            assert json.loads(current.checkpoint_json)["phase"] == "awaiting_confirmation"
            attempt = session.scalar(select(InferenceRun).where(InferenceRun.job_id == job["id"]))
            assert attempt.error_code is None
            assert decode_archive(attempt.call_archive)["phase"] == "returned"
            assert (attempt.usage_json is None) is unknown
            if not unknown:
                assert json.loads(attempt.usage_json)["total_tokens"] == 40
            assert session.get(BookCharacter, ids["source"]) is not None
        assert len(adapter.calls) == 1
    finally:
        local_finalization.forget_settlements(factory)


def test_merge_transient_real_writer_lock_retries_return_only(migrated_client, populated, monkeypatch):
    client, ids = migrated_client, populated
    factory = client.app.state.session_factory
    job, _ = _auto_job(client, ids)
    lock = sqlite3.connect(client.app.state.settings.database_path)
    released = []
    with factory() as session:
        session.connection().exec_driver_sql("PRAGMA busy_timeout=1")

    class LockingAdapter(FakeProviderAdapter):
        async def generate_labels(self, payload):
            raw = await super().generate_labels(payload)
            lock.execute("BEGIN IMMEDIATE")
            return raw

    def unlock(_):
        released.append(True)
        lock.rollback()

    monkeypatch.setattr("ndr.storage.transactions.time.sleep", unlock)
    adapter = LockingAdapter(script=[{"groups": [_merge_group(ids)]}], usage={"total_tokens": 40})
    try:
        assert run(client, job, adapter) == (JobState.COMPLETED, 1)
        assert released
        assert len(adapter.calls) == 1
    finally:
        lock.close()


@pytest.mark.parametrize("failure", ["schema", "apply"])
def test_merge_semantic_failure_keeps_returned_archive_and_usage(
    migrated_client, populated, monkeypatch, failure,
):
    client, ids = migrated_client, populated
    job, _ = _auto_job(client, ids)
    if failure == "apply":
        def fail(*_):
            raise RuntimeError("local semantic failure")
        monkeypatch.setattr(auto_merge, "_check_current", fail)
    groups = [_merge_group(ids)]
    adapter = FakeProviderAdapter(script=[{"groups": groups} if failure == "apply" else {"bad": []}],
                                  usage={"total_tokens": 40})
    assert run(client, job, adapter) == (JobState.FAILED, 1)
    with client.app.state.session_factory() as session:
        attempt = session.scalar(select(InferenceRun).where(InferenceRun.job_id == job["id"]))
        assert json.loads(attempt.usage_json)["total_tokens"] == 40
        assert decode_archive(attempt.call_archive)["phase"] == "returned"
        assert attempt.error_code == ("INVALID_MODEL_OUTPUT" if failure == "schema"
                                      else "MERGE_FINALIZATION_FAILED")
        assert session.get(BookCharacter, ids["source"]) is not None


@pytest.mark.parametrize("tamper", [None, "job", "messages", "profile", "fingerprint", "corrupt",
                                   "max_tokens", "target_quote_ids", "json_object"])
def test_merge_restart_recovers_bound_return_without_adapter_or_credentials(
    migrated_client, populated, monkeypatch, tamper,
):
    client, ids = migrated_client, populated
    factory = client.app.state.session_factory
    job, _ = _auto_job(client, ids)
    adapter = FakeProviderAdapter(script=[{"groups": [_merge_group(ids)]}], usage={"total_tokens": 40})
    real_finish = auto_merge.finish_local_write

    def interrupted(factory, action, **kwargs):
        if action.__name__ == "finalize":
            raise storage_error()
        return real_finish(factory, action, **kwargs)

    monkeypatch.setattr(auto_merge, "finish_local_write", interrupted)
    assert run(client, job, adapter) == (JobState.RUNNING, 1)
    local_finalization.forget_settlements(factory)  # Simulate process loss after durable return.
    monkeypatch.setattr(auto_merge, "finish_local_write", real_finish)
    with transaction(factory) as session:
        attempt = session.scalar(select(InferenceRun).where(InferenceRun.job_id == job["id"]))
        archive = decode_archive(attempt.call_archive)
        if tamper == "job":
            attempt.window_id = "character-merge:another-job"
        elif tamper == "messages":
            archive["request"]["messages"][0]["content"] = "different request"
        elif tamper == "profile":
            archive["request"]["profile_snapshot"]["model"] = "different model"
        elif tamper == "fingerprint":
            attempt.request_fingerprint = "different fingerprint"
        elif tamper == "max_tokens":
            archive["request"]["max_tokens"] = 1
        elif tamper == "target_quote_ids":
            archive["request"]["target_quote_ids"] = ["unexpected"]
        elif tamper == "json_object":
            del archive["request"]["json_object"]
        if tamper:
            attempt.call_archive = b"corrupt" if tamper == "corrupt" else encode_archive(archive)
    recover_on_startup(factory)
    with factory() as session:
        current = session.get(Job, job["id"])
        assert current.state is JobState.PARTIAL
        recovery = job_recovery(session, current, has_credential=False)
        if tamper:
            assert recovery.actions == []
        else:
            assert recovery.actions[0].action == "resume"
            assert recovery.actions[0].paid is False
            assert recovery.requires_credential is False
    def no_adapter(*_):
        pytest.fail("local recovery constructed an adapter")
    monkeypatch.setattr(auto_merge, "_build_adapter", no_adapter)
    response = client.post(f"/api/jobs/{job['id']}/resume")
    assert response.status_code == 202, response.text
    with factory() as session:
        current = session.get(Job, job["id"])
        assert current.state is (JobState.FAILED if tamper else JobState.COMPLETED)
        assert len(list(session.scalars(select(InferenceRun).where(
            InferenceRun.job_id == job["id"],
        )))) == 1
        assert session.get(BookCharacter, ids["source"]) is not None
    assert len(adapter.calls) == 1


@pytest.mark.parametrize("case", ["invalid_output", "budget_unknown", "budget_excess"])
def test_merge_unrecoverable_result_does_not_offer_local_resume(migrated_client, populated, case):
    client, ids = migrated_client, populated
    job, _ = _auto_job(client, ids, max_total_tokens=10000)
    adapter = FakeProviderAdapter(
        script=[{"bad": []} if case == "invalid_output" else {"groups": [_merge_group(ids)]}],
        usage=None if case == "budget_unknown" else {"total_tokens": 20000 if case == "budget_excess" else 40},
    )
    state, _ = run(client, job, adapter)
    assert state is (JobState.FAILED if case == "invalid_output" else JobState.BUDGET_EXHAUSTED)
    with client.app.state.session_factory() as session:
        current = session.get(Job, job["id"])
        assert not auto_merge.has_returned_merge_result(session, current)
        assert job_recovery(session, current).actions == []


def test_merge_provider_error_keeps_response_receipt_and_usage(migrated_client, populated):
    client, ids = migrated_client, populated
    job, _ = _auto_job(client, ids)
    error = ProviderError(ProviderErrorKind.INVALID_OUTPUT, "合成坏输出",
                          details={"usage": {"total_tokens": 40}},
                          receipt={"response": {"body": "synthetic invalid JSON"}})
    adapter = FakeProviderAdapter(script=[error])
    assert run(client, job, adapter) == (JobState.FAILED, 1)
    with client.app.state.session_factory() as session:
        attempt = session.scalar(select(InferenceRun).where(InferenceRun.job_id == job["id"]))
        archive = decode_archive(attempt.call_archive)
        assert archive["phase"] == "returned"
        assert archive["provider_receipt"] == error.receipt
        assert archive["error"]["kind"] == "INVALID_OUTPUT"
        assert json.loads(attempt.usage_json)["total_tokens"] == 40
        assert not auto_merge.has_returned_merge_result(session, session.get(Job, job["id"]))
    assert len(adapter.calls) == 1
