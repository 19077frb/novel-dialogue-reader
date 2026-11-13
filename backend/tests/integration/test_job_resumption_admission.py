"""Recovery admission uses isolated authored text and a recording dispatcher only."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select

from ndr.domain.enums import JobKind, JobState
from ndr.jobs.dialogue_lifecycle import acquire, release
from ndr.jobs.local_finalization import defer_settlement, forget_settlements
from ndr.storage.models import InferenceRun, Job, JobWindow, Quote
from ndr.storage.transactions import transaction

TEXT = "第一章\n「开门吧。」林舟说。\n「我带来了灯。」许遥答。\n第二章\n「天亮了。」\n"


@pytest.fixture()
def recovery_db(migrated_client, monkeypatch):
    client = migrated_client
    result = client.post("/api/books/import", files={
        "file": ("original-recovery.txt", TEXT.encode(), "text/plain"),
    })
    assert result.status_code == 202, result.text
    imported = result.json()["data"]
    factory = client.app.state.session_factory
    dispatched = []
    monkeypatch.setattr("ndr.api.jobs.run_job", lambda *_, job_id, **__: dispatched.append(job_id))
    with factory() as session:
        quotes = list(session.scalars(select(Quote).where(
            Quote.book_version_id == imported["book_version_id"],
        ).order_by(Quote.start_cp)))
    yield client, factory, imported, quotes, dispatched
    forget_settlements(factory)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(InferenceRun)) == 0


def record(factory, imported, *, state=JobState.PAUSED, kind=JobKind.INFERENCE,
           target=None, checkpoint=None):
    with transaction(factory) as session:
        job = Job(
            book_id=imported["book_id"], book_version_id=imported["book_version_id"],
            kind=kind, state=state,
            range_json=json.dumps(target or {"start_cp": 0, "end_cp": len(TEXT)}),
            checkpoint_json=json.dumps(checkpoint or {}), budget_json="{}",
        )
        session.add(job)
        session.flush()
        return job.id


def bounds(quote):
    return {"start_cp": quote.start_cp, "end_cp": quote.end_cp}


def state_of(factory, job_id):
    with factory() as session:
        return session.get(Job, job_id).state


@pytest.mark.parametrize("candidate_state", [JobState.PAUSED, JobState.PARTIAL,
                                              JobState.BUDGET_EXHAUSTED])
@pytest.mark.parametrize("owner_state", [JobState.QUEUED, JobState.RUNNING,
                                          JobState.PAUSING, JobState.NEEDS_RECONCILIATION])
def test_resume_rechecks_target_and_keeps_original_state_on_conflict(
    recovery_db, candidate_state, owner_state,
):
    client, factory, imported, _, dispatched = recovery_db
    paused = record(factory, imported, state=candidate_state)
    owner = record(factory, imported, state=owner_state, kind=JobKind.RECHECK)
    response = client.post(f"/api/jobs/{paused}/resume")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["job_id"] == owner
    assert state_of(factory, paused) is candidate_state
    assert not dispatched


def test_disjoint_target_can_resume_and_duplicate_resume_does_not_dispatch(recovery_db):
    client, factory, imported, quotes, dispatched = recovery_db
    paused = record(factory, imported, target=bounds(quotes[0]))
    record(factory, imported, state=JobState.RUNNING, target=bounds(quotes[-1]))
    with transaction(factory) as session:
        session.add(JobWindow(job_id=paused, window_id="done", state=JobState.COMPLETED))
    first = client.post(f"/api/jobs/{paused}/resume")
    second = client.post(f"/api/jobs/{paused}/resume")
    assert first.status_code == second.status_code == 202
    assert first.json()["data"]["windows_done"] == 1
    assert first.json()["data"]["state"] == "QUEUED"
    assert dispatched == [paused]


@pytest.mark.parametrize("damaged", ["broken json", "[]"])
def test_damaged_legacy_scope_keeps_conservative_occupancy(recovery_db, damaged):
    client, factory, imported, _, dispatched = recovery_db
    paused = record(factory, imported)
    owner = record(factory, imported, state=JobState.RUNNING)
    with transaction(factory) as session:
        session.get(Job, paused).range_json = damaged
    response = client.post(f"/api/jobs/{paused}/resume")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["job_id"] == owner
    assert state_of(factory, paused) is JobState.PAUSED
    assert not dispatched


@pytest.mark.parametrize("state", [JobState.QUEUED, JobState.RUNNING, JobState.PAUSING,
                                     JobState.COMPLETED, JobState.FAILED,
                                     JobState.NEEDS_RECONCILIATION])
def test_ineffective_resume_returns_snapshot_without_dispatch(recovery_db, state):
    client, factory, imported, _, dispatched = recovery_db
    job_id = record(factory, imported, state=state)
    response = client.post(f"/api/jobs/{job_id}/resume")
    assert response.status_code == 202
    assert response.json()["data"]["state"] == state.value
    assert state_of(factory, job_id) is state
    assert not dispatched


def test_returned_dialogue_can_resume_but_conflict_preserves_receipts(recovery_db):
    client, factory, imported, _, dispatched = recovery_db
    checkpoint = {"dialogue_receipts": {"old-window": {"run_id": "returned"}}}
    job_id = record(factory, imported, state=JobState.FAILED, checkpoint=checkpoint)
    owner = record(factory, imported, state=JobState.QUEUED)
    assert client.post(f"/api/jobs/{job_id}/resume").status_code == 409
    with transaction(factory) as session:
        assert json.loads(session.get(Job, job_id).checkpoint_json) == checkpoint
        session.get(Job, owner).state = JobState.COMPLETED
    assert client.post(f"/api/jobs/{job_id}/resume").status_code == 202
    assert state_of(factory, job_id) is JobState.QUEUED
    assert dispatched == [job_id]


@pytest.mark.parametrize("entry", ["resume", "retry"])
def test_local_settlement_pending_cannot_change_state_or_dispatch(recovery_db, entry):
    client, factory, imported, _, dispatched = recovery_db
    state = JobState.PAUSED if entry == "resume" else JobState.NEEDS_RECONCILIATION
    job_id = record(factory, imported, state=state)
    defer_settlement(factory, job_id, lambda: None)
    path = f"/api/jobs/{job_id}/{'resume' if entry == 'resume' else 'reconcile'}"
    response = client.post(path, **({"json": {"action": "retry"}} if entry == "retry" else {}))
    assert response.status_code == 409, response.text
    assert "保存" in response.json()["error"]["message"]
    assert state_of(factory, job_id) is state
    assert not dispatched


@pytest.mark.parametrize("kind", [JobKind.INFERENCE, JobKind.CHARACTER_ROSTER])
def test_finishing_worker_must_release_before_resume(recovery_db, monkeypatch, kind):
    client, factory, imported, _, dispatched = recovery_db
    job_id = record(factory, imported, kind=kind)
    if kind is JobKind.CHARACTER_ROSTER:
        monkeypatch.setattr("ndr.jobs.resumption.roster_job_is_active", lambda _: True)
    else:
        assert acquire(job_id)
    try:
        response = client.post(f"/api/jobs/{job_id}/resume")
        assert response.status_code == 409, response.text
        assert state_of(factory, job_id) is JobState.PAUSED
        assert not dispatched
    finally:
        if kind is JobKind.INFERENCE:
            release(job_id)


def test_reconcile_retry_checks_occupancy_before_mutating_unknown_windows(recovery_db):
    client, factory, imported, _, dispatched = recovery_db
    checkpoint = {"dialogue_receipts": {"window": {"run_id": "old"}}}
    job_id = record(factory, imported, state=JobState.NEEDS_RECONCILIATION,
                    checkpoint=checkpoint)
    owner = record(factory, imported, state=JobState.RUNNING)
    with transaction(factory) as session:
        session.add(JobWindow(job_id=job_id, window_id="window",
                              state=JobState.NEEDS_RECONCILIATION))
    response = client.post(f"/api/jobs/{job_id}/reconcile", json={"action": "retry"})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["job_id"] == owner
    with factory() as session:
        job = session.get(Job, job_id)
        assert job.state is JobState.NEEDS_RECONCILIATION
        assert json.loads(job.checkpoint_json) == checkpoint
        assert session.scalar(select(JobWindow).where(
            JobWindow.job_id == job_id,
        )).state is JobState.NEEDS_RECONCILIATION
    assert not dispatched


def test_reconcile_keep_unknown_remains_available_during_other_work(recovery_db):
    client, factory, imported, _, dispatched = recovery_db
    job_id = record(factory, imported, state=JobState.NEEDS_RECONCILIATION)
    record(factory, imported, state=JobState.RUNNING)
    defer_settlement(factory, job_id, lambda: None)
    response = client.post(f"/api/jobs/{job_id}/reconcile", json={"action": "keep_unknown"})
    assert response.status_code == 200, response.text
    assert state_of(factory, job_id) is JobState.PARTIAL
    assert not dispatched


def test_concurrent_resume_and_creation_admit_only_one_target_owner(recovery_db):
    client, factory, imported, _, dispatched = recovery_db
    paused = record(factory, imported)
    start = Barrier(2)

    def resume():
        start.wait(timeout=5)
        return client.post(f"/api/jobs/{paused}/resume")

    def create():
        start.wait(timeout=5)
        return client.post("/api/jobs", json={
            "book_id": imported["book_id"], "book_version_id": imported["book_version_id"],
            "range": {"start_cp": 0, "end_cp": len(TEXT)}, "reading_mode": "reread",
            "idempotency_key": "concurrent-new", "run_now": False,
        })

    with ThreadPoolExecutor(max_workers=2) as pool:
        resumed, created = [future.result(timeout=15) for future in
                            [pool.submit(resume), pool.submit(create)]]
    assert sorted([resumed.status_code, created.status_code]) == [202, 409]
    with factory() as session:
        active = list(session.scalars(select(Job).where(
            Job.book_version_id == imported["book_version_id"], Job.state == JobState.QUEUED,
        )))
        assert len(active) == 1
    assert dispatched == ([paused] if resumed.status_code == 202 else [])


def test_concurrent_duplicate_resume_dispatches_once(recovery_db):
    client, factory, imported, _, dispatched = recovery_db
    job_id = record(factory, imported)
    start = Barrier(2)

    def resume():
        start.wait(timeout=5)
        return client.post(f"/api/jobs/{job_id}/resume")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=15) for future in
                   [pool.submit(resume), pool.submit(resume)]]
    assert [response.status_code for response in results] == [202, 202]
    assert state_of(factory, job_id) is JobState.QUEUED
    assert dispatched == [job_id]


@pytest.mark.parametrize("state", [JobState.PAUSED, JobState.PARTIAL, JobState.BUDGET_EXHAUSTED,
                                     JobState.FAILED, JobState.NEEDS_RECONCILIATION])
def test_manual_run_cannot_bypass_resume_admission(migrated_client, monkeypatch, state):
    client = migrated_client
    imported = client.post("/api/books/import", files={
        "file": ("original-run-boundary.txt", TEXT.encode(), "text/plain"),
    }).json()["data"]
    factory = client.app.state.session_factory
    job_id = record(factory, imported, state=state)
    owner = record(factory, imported, state=JobState.RUNNING)

    def unexpected_dispatch(*_, **__):
        pytest.fail("stopped task must pass explicit resume/reconcile admission first")

    monkeypatch.setattr("ndr.jobs.scheduler.build_adapter", unexpected_dispatch)
    response = client.post(f"/api/jobs/{job_id}/run")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["state"] == state.value
    assert state_of(factory, job_id) is state
    assert state_of(factory, owner) is JobState.RUNNING
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(InferenceRun)) == 0


def test_manual_run_still_executes_queued_job_and_returns_declared_statistics(
    fake_provider_client, monkeypatch,
):
    from ndr.llm.adapters.fake import FakeProviderAdapter

    client = fake_provider_client
    imported = client.post("/api/books/import", files={
        "file": ("original-queued-run.txt", TEXT.encode(), "text/plain"),
    }).json()["data"]
    created = client.post("/api/jobs", json={
        "book_id": imported["book_id"], "range": {"start_cp": 0, "end_cp": len(TEXT)},
        "idempotency_key": "queued-run", "run_now": False,
    })
    assert created.status_code == 202, created.text
    job_id = created.json()["data"]["id"]
    adapter = FakeProviderAdapter()
    monkeypatch.setattr("ndr.jobs.scheduler._build_adapter", lambda *_, **__: adapter)
    response = client.post(f"/api/jobs/{job_id}/run")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["state"] == "COMPLETED"
    assert data["calls"] == len(adapter.calls) > 0
    assert data["windows_done"] == data["windows_total"] > 0
    assert state_of(client.app.state.session_factory, job_id) is JobState.COMPLETED


@pytest.mark.parametrize("state", [JobState.FAILED, JobState.NEEDS_RECONCILIATION])
def test_old_roster_receipt_keeps_local_resume_and_superseded_protection(
    recovery_db, monkeypatch, state,
):
    client, factory, imported, _, dispatched = recovery_db
    monkeypatch.setattr("ndr.jobs.roster_receipts.has_returned_result", lambda *_: True)
    job_id = record(factory, imported, state=state, kind=JobKind.CHARACTER_ROSTER,
                    target={"chapter_id": "chapter-one"},
                    checkpoint={"roster_retry_after_run_id": "old"})
    response = client.post(f"/api/jobs/{job_id}/resume")
    assert response.status_code == 202, response.text
    assert dispatched == [job_id]
    with transaction(factory) as session:
        job = session.get(Job, job_id)
        assert "roster_retry_after_run_id" not in json.loads(job.checkpoint_json)
        job.state = state
        job.checkpoint_json = json.dumps({"superseded_by": "new-task"})
    response = client.post(f"/api/jobs/{job_id}/resume")
    assert response.status_code == 409
    assert response.json()["error"]["details"]["job_id"] == "new-task"
    assert state_of(factory, job_id) is state
    assert dispatched == [job_id]


def test_roster_resume_checks_same_chapter_without_blocking_dialogue(recovery_db):
    client, factory, imported, _, dispatched = recovery_db
    target = {"chapter_id": "chapter-one"}
    paused = record(factory, imported, kind=JobKind.CHARACTER_ROSTER, target=target)
    owner = record(factory, imported, kind=JobKind.CHARACTER_ROSTER, target=target,
                    state=JobState.RUNNING)
    assert client.post(f"/api/jobs/{paused}/resume").status_code == 409
    with transaction(factory) as session:
        session.get(Job, owner).state = JobState.COMPLETED
    record(factory, imported, state=JobState.RUNNING)
    assert client.post(f"/api/jobs/{paused}/resume").status_code == 202
    assert dispatched == [paused]


def test_repair_roster_unknown_does_not_implicitly_retry_returned_attempt(recovery_db, monkeypatch):
    client, factory, imported, _, dispatched = recovery_db
    monkeypatch.setattr("ndr.jobs.roster_receipts.has_returned_result", lambda *_: True)
    job_id = record(factory, imported, state=JobState.NEEDS_RECONCILIATION,
                    kind=JobKind.CHARACTER_ROSTER,
                    target={"chapter_id": "chapter-one", "roster_repair_protocol": "roster-repair-1"})
    response = client.post(f"/api/jobs/{job_id}/resume")
    assert response.status_code == 202
    assert state_of(factory, job_id) is JobState.NEEDS_RECONCILIATION
    assert not dispatched


@pytest.mark.parametrize("state", [JobState.COMPLETED, JobState.FAILED,
                                     JobState.NEEDS_RECONCILIATION])
def test_merge_terminal_resume_does_not_restart_one_shot_analysis(recovery_db, state):
    client, factory, imported, _, dispatched = recovery_db
    job_id = record(factory, imported, state=state, kind=JobKind.CHARACTER_MERGE)
    response = client.post(f"/api/jobs/{job_id}/resume")
    assert response.status_code == 202
    assert state_of(factory, job_id) is state
    assert not dispatched


def test_http_resume_recovers_returned_dialogue_without_resending(fake_provider_client, monkeypatch):
    from integration.test_expression_pipeline import StageAdapter, prepare
    from ndr.jobs import scheduler

    client = fake_provider_client
    create, factory = prepare(client)
    job_id = create("http-receipt-admission")["id"]
    with transaction(factory) as session:
        job = session.get(Job, job_id)
        job.budget_json = json.dumps({**json.loads(job.budget_json), "max_recheck_rounds": 0})
    adapter = StageAdapter(["林舟"])
    apply_payload = scheduler._apply_payload

    def fail_local_save(*_, **__):
        raise RuntimeError("isolated local finalization failure")

    monkeypatch.setattr(scheduler, "_apply_payload", fail_local_save)
    first = scheduler.run_job(factory, client.app.state.settings, job_id=job_id,
                              adapter_factory=lambda *_: adapter)
    assert first.state is JobState.FAILED and len(adapter.calls) == 1
    monkeypatch.setattr(scheduler, "_apply_payload", apply_payload)
    dispatched = []

    def recover(factory, settings, *, job_id, **_):
        dispatched.append(job_id)
        return scheduler.run_job(factory, settings, job_id=job_id,
                                 adapter_factory=lambda *_: adapter)

    monkeypatch.setattr("ndr.api.jobs.run_job", recover)
    assert client.post(f"/api/jobs/{job_id}/resume").status_code == 202
    assert state_of(factory, job_id) is JobState.COMPLETED
    assert client.post(f"/api/jobs/{job_id}/resume").status_code == 202
    assert dispatched == [job_id]
    assert len(adapter.calls) == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(InferenceRun).where(
            InferenceRun.job_id == job_id,
        )) == 1
