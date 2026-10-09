"""Exited dialogue workers: isolated authored text and fake providers only."""

import json

import pytest
from sqlalchemy import select

from integration.test_expression_pipeline import StageAdapter, prepare
from ndr.domain.enums import InferenceRunState, JobState
from ndr.jobs import scheduler
from ndr.jobs.dialogue_lifecycle import acquire, release
from ndr.jobs.service import _spent_tokens
from ndr.recovery.service import job_recovery, recover_on_startup
from ndr.storage.models import InferenceRun, Job, JobWindow
from ndr.storage.transactions import transaction


def test_returned_primary_can_resume_without_resending(fake_provider_client, monkeypatch):
    client = fake_provider_client
    create, factory = prepare(client)
    job_id = create("receipt-local-failure")["id"]
    with transaction(factory) as session:
        job = session.get(Job, job_id)
        budget = json.loads(job.budget_json)
        job.budget_json = json.dumps({**budget, "max_recheck_rounds": 0})
    adapter = StageAdapter(["林舟"])
    accept = scheduler._apply_payload

    def broken(*args, **kwargs):
        raise RuntimeError("private novel text must not appear in diagnostics")

    monkeypatch.setattr(scheduler, "_apply_payload", broken)
    result = scheduler.run_job(factory, client.app.state.settings, job_id=job_id,
                               adapter_factory=lambda *_: adapter)
    assert result.state is JobState.FAILED
    assert len(adapter.calls) == 1
    with factory() as session:
        job = session.get(Job, job_id)
        assert "private novel" not in job.last_error
        assert json.loads(job.checkpoint_json)["dialogue_receipts"]
        assert job_recovery(session, job).actions[0].action == "resume"
        run = session.scalar(select(InferenceRun).where(InferenceRun.job_id == job_id))
        assert json.loads(run.usage_json)["total_tokens"] == 30
    monkeypatch.setattr(scheduler, "_apply_payload", accept)
    with transaction(factory) as session:
        session.get(Job, job_id).state = JobState.QUEUED
    result = scheduler.run_job(factory, client.app.state.settings, job_id=job_id,
                               adapter_factory=lambda *_: adapter)
    assert result.state is JobState.COMPLETED, result.errors
    assert len(adapter.calls) == 1
    assert result.calls == 0


def test_adapter_crash_keeps_unknown_without_resending(fake_provider_client):
    client = fake_provider_client
    create, factory = prepare(client)
    job_id = create("unknown-worker")["id"]

    class Broken(StageAdapter):
        async def generate_labels(self, payload):
            raise RuntimeError("transport exited")

    adapter = Broken(["林舟"])
    result = scheduler.run_job(factory, client.app.state.settings, job_id=job_id,
                               adapter_factory=lambda *_: adapter)
    assert result.state is JobState.NEEDS_RECONCILIATION
    with factory() as session:
        run = session.scalar(select(InferenceRun).where(InferenceRun.job_id == job_id))
        assert run.state is InferenceRunState.UNKNOWN_OUTCOME
        assert session.scalar(select(JobWindow).where(JobWindow.job_id == job_id)).state is JobState.NEEDS_RECONCILIATION


@pytest.mark.parametrize("state", [JobState.COMPLETED, JobState.PAUSED])
def test_final_states_are_not_overwritten(fake_provider_client, monkeypatch, state):
    client = fake_provider_client
    create, factory = prepare(client)
    job_id = create(f"final-{state}")["id"]

    def broken(*args, **kwargs):
        with transaction(factory) as session:
            session.get(Job, job_id).state = state
        raise RuntimeError("after final state")

    monkeypatch.setattr(scheduler, "_run_job", broken)
    assert scheduler.run_job(factory, client.app.state.settings, job_id=job_id).state is state


def test_duplicate_worker_does_not_dispatch(fake_provider_client):
    client = fake_provider_client
    create, factory = prepare(client)
    job_id = create("duplicate-worker")["id"]
    assert acquire(job_id)
    try:
        result = scheduler.run_job(factory, client.app.state.settings, job_id=job_id,
                                   adapter_factory=lambda *_: pytest.fail("duplicate dispatch"))
        assert result.state is JobState.QUEUED
    finally:
        release(job_id)


def test_restart_classifies_recent_unreturned_call(fake_provider_client):
    client = fake_provider_client
    create, factory = prepare(client)
    job_id = create("restart-unknown")["id"]
    with transaction(factory) as session:
        session.get(Job, job_id).state = JobState.RUNNING
        session.add(InferenceRun(job_id=job_id, window_id="pending",
                                 state=InferenceRunState.DISPATCHED,
                                 request_fingerprint="frozen", profile_snapshot_json="{}"))
    recover_on_startup(factory)
    with factory() as session:
        assert session.get(Job, job_id).state is JobState.NEEDS_RECONCILIATION


@pytest.mark.parametrize("change", ["request", "snapshot", "corrupt"])
def test_changed_receipt_blocks_dispatch(fake_provider_client, monkeypatch, change):
    from ndr.storage.run_archive import decode_archive, encode_archive

    client = fake_provider_client
    create, factory = prepare(client)
    job_id = create(f"changed-{change}")["id"]
    adapter = StageAdapter(["林舟"])
    original = scheduler._apply_payload
    monkeypatch.setattr(scheduler, "_apply_payload", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    scheduler.run_job(factory, client.app.state.settings, job_id=job_id,
                      adapter_factory=lambda *_: adapter)
    with transaction(factory) as session:
        job = session.get(Job, job_id)
        job.state = JobState.QUEUED
        run = session.scalar(select(InferenceRun).where(InferenceRun.job_id == job_id))
        if change == "snapshot":
            run.profile_snapshot_json = '{"model":"changed"}'
        elif change == "request":
            archive = decode_archive(run.call_archive)
            archive["request"]["max_tokens"] = 1
            run.call_archive = encode_archive(archive)
        else:
            run.call_archive = b"corrupt"
    monkeypatch.setattr(scheduler, "_apply_payload", original)
    outcome = scheduler.run_job(factory, client.app.state.settings, job_id=job_id,
                                 adapter_factory=lambda *_: adapter)
    assert outcome.state is not JobState.COMPLETED
    # One primary and one review already returned before the injected local error.
    assert len(adapter.calls) == 2


def test_returned_receipt_commit_retries_only_local_write(fake_provider_client, monkeypatch):
    import sqlite3

    from sqlalchemy.exc import OperationalError

    from ndr.storage import run_archive

    client = fake_provider_client
    create, factory = prepare(client)
    job_id = create("receipt-busy")["id"]
    adapter = StageAdapter(["林舟"])
    original = run_archive.save_run_archive
    locks = []

    def busy(run, request, **kwargs):
        if kwargs.get("phase") == "returned" and len(locks) < 2:
            locks.append(True)
            raise OperationalError("sensitive SQL", {}, sqlite3.OperationalError("database is locked"))
        return original(run, request, **kwargs)

    monkeypatch.setattr(run_archive, "save_run_archive", busy)
    outcome = scheduler.run_job(factory, client.app.state.settings, job_id=job_id,
                                 adapter_factory=lambda *_: adapter)
    assert outcome.state is JobState.COMPLETED, outcome.errors
    assert len(locks) == 2
    assert len(adapter.calls) == 2


def test_startup_preserves_returned_receipt(fake_provider_client, monkeypatch):
    client = fake_provider_client
    create, factory = prepare(client)
    job_id = create("startup-returned")["id"]
    adapter = StageAdapter(["林舟"])
    monkeypatch.setattr(scheduler, "_apply_payload", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    scheduler.run_job(factory, client.app.state.settings, job_id=job_id,
                      adapter_factory=lambda *_: adapter)
    with transaction(factory) as session:
        session.get(Job, job_id).state = JobState.RUNNING
        runs = list(session.scalars(select(InferenceRun).where(InferenceRun.job_id == job_id)))
        runs[-1].state = InferenceRunState.DISPATCHED
    recover_on_startup(factory, lease_seconds=0)
    with factory() as session:
        assert session.get(Job, job_id).state is JobState.FAILED
        assert all(run.state is not InferenceRunState.UNKNOWN_OUTCOME for run in session.scalars(
            select(InferenceRun).where(InferenceRun.job_id == job_id)
        ))
    assert len(adapter.calls) == 2


def test_missing_usage_after_local_failure_is_unknown():
    from types import SimpleNamespace

    spent = _spent_tokens([SimpleNamespace(
        state=InferenceRunState.FAILED, usage_json=None,
        error_code="LOCAL_FINALIZATION_FAILED",
    )])
    assert spent["unknown_runs"] == 1
    assert spent["total_tokens"] == 0


def test_format_correction_receipts_replay_exact_requests(fake_provider_client):
    from ndr.jobs.dialogue_lifecycle import restore_dispatch
    from ndr.storage.run_archive import save_run_archive

    create, factory = prepare(fake_provider_client)
    job_id = create("format-receipts")["id"]
    requests = [{"messages": ["primary"]}, {"messages": ["correction"]}]
    with transaction(factory) as session:
        job = session.get(Job, job_id)
        ids = []
        for request in requests:
            run = InferenceRun(
                job_id=job_id, window_id="one", state=InferenceRunState.FAILED,
                profile_snapshot_json="{}",
                request_fingerprint=scheduler._request_fingerprint(request, {}),
            )
            session.add(run)
            session.flush()
            save_run_archive(run, request, raw={"value": request["messages"][0]}, phase="returned")
            ids.append(run.id)
        job.checkpoint_json = json.dumps({"dialogue_receipts": {"one": ids}})
    with factory() as session:
        for request, expected in zip(requests, ids, strict=True):
            raw, error, run_id, _ = restore_dispatch(
                session, job_id=job_id, window_id="one", request=request,
                snapshot={}, fingerprint=scheduler._request_fingerprint(request, {}),
            )
            assert run_id == expected and error is None
            assert raw.dispatch_attempts == 0
