"""New user starts adopt queued work or replace locally terminated unknown rosters."""

import json

import pytest
from sqlalchemy import select
from tests.integration.test_sourced_roster_jobs import prepare, response

from ndr.domain.enums import InferenceRunState, JobState
from ndr.jobs.roster import run_character_roster_job
from ndr.llm.adapters.fake import FakeProviderAdapter
from ndr.llm.errors import ProviderError, ProviderErrorKind
from ndr.storage.models import InferenceRun, Job


def restart(client, data, job, **changes):
    path = f"/api/books/{data['book_id']}/chapters/{job['range']['chapter_id']}/character-roster/analyze"
    with client.app.state.session_factory() as session:
        snapshot = json.loads(session.get(Job, job["id"]).profile_snapshot_json)
    return client.post(path, json={
        "profile_id": snapshot["profile_id"],
        "idempotency_key": "new-start", "run_now": False,
        "max_input_tokens": 12345, **changes,
    })


@pytest.mark.parametrize("state", [JobState.QUEUED, JobState.RUNNING, JobState.PAUSING])
def test_new_settings_adopt_same_chapter_active_request(migrated_client, state):
    client = migrated_client
    data, old = prepare(client)
    with client.app.state.session_factory() as session:
        session.get(Job, old["id"]).state = state
        session.commit()
    result = restart(client, data, old)
    assert result.status_code == 202, result.text
    assert result.json()["data"]["id"] == old["id"]
    with client.app.state.session_factory() as session:
        assert json.loads(session.get(Job, old["id"]).budget_json)["max_input_tokens"] is None


def test_adopted_queued_request_is_dispatched_and_finishes_only_once(migrated_client, monkeypatch):
    client = migrated_client
    data, old = prepare(client)
    adapter = FakeProviderAdapter(script=[response()])
    dispatched = []

    def worker(factory, settings, *, job_id, **_):
        dispatched.append(job_id)
        return run_character_roster_job(factory, settings, job_id=job_id,
                                       adapter_factory=lambda *_: adapter)

    monkeypatch.setattr("ndr.api.characters.run_job", worker)
    first = restart(client, data, old, run_now=True)
    assert first.status_code == 202 and first.json()["data"]["id"] == old["id"]
    repeated = restart(client, data, old, idempotency_key="sourced", max_input_tokens=None, run_now=True)
    assert repeated.status_code == 202
    assert repeated.json()["data"]["state"] == "COMPLETED"
    assert dispatched == [old["id"]]
    with client.app.state.session_factory() as session:
        assert len(list(session.scalars(select(InferenceRun)))) == 1


def test_explicit_timeout_retry_queued_without_worker_is_woken_by_new_start(migrated_client, monkeypatch):
    client = migrated_client
    data, old = prepare(client)
    factory, settings = client.app.state.session_factory, client.app.state.settings
    failed = FakeProviderAdapter(script=[ProviderError(ProviderErrorKind.TIMEOUT, "test timeout")])
    outcome = run_character_roster_job(factory, settings, job_id=old["id"],
                                       adapter_factory=lambda *_: failed)
    assert outcome.state is JobState.NEEDS_RECONCILIATION
    assert client.post(f"/api/jobs/{old['id']}/reconcile", json={"action": "retry"}).status_code == 200
    adapter = FakeProviderAdapter(script=[response()])
    monkeypatch.setattr("ndr.api.characters.run_job", lambda factory, settings, job_id, **_:
        run_character_roster_job(factory, settings, job_id=job_id, adapter_factory=lambda *_: adapter))
    result = restart(client, data, old, run_now=True)
    assert result.status_code == 202 and result.json()["data"]["id"] == old["id"]
    with factory() as session:
        assert session.get(Job, old["id"]).state is JobState.COMPLETED
        runs = list(session.scalars(select(InferenceRun).where(InferenceRun.job_id == old["id"])))
        assert len(runs) == 2
        assert {run.state for run in runs} == {InferenceRunState.UNKNOWN_OUTCOME, InferenceRunState.SUCCEEDED}


@pytest.mark.parametrize("protection", ["terminated", "worker", "dispatched", "same_key"])
def test_unknown_local_outcome_replacement_retains_history_and_protects_execution(
    migrated_client, monkeypatch, protection,
):
    client = migrated_client
    data, old = prepare(client)
    factory = client.app.state.session_factory
    with factory() as session:
        job = session.get(Job, old["id"])
        job.state = JobState.NEEDS_RECONCILIATION
        attempt = InferenceRun(job_id=job.id, window_id="roster:test",
                               profile_snapshot_json=job.profile_snapshot_json,
                               request_fingerprint="test", state=(InferenceRunState.DISPATCHED
                                   if protection == "dispatched" else InferenceRunState.UNKNOWN_OUTCOME),
                               error_code="PROVIDER_TIMEOUT")
        session.add(attempt)
        session.commit()
        run_id = attempt.id
    if protection == "worker":
        monkeypatch.setattr("ndr.jobs.roster.roster_job_is_active", lambda _: True)
    result = restart(client, data, old, **({"idempotency_key": "sourced", "max_input_tokens": None}
                                         if protection == "same_key" else {}))
    assert result.status_code == 202, result.text
    new_id = result.json()["data"]["id"]
    with factory() as session:
        stored = session.get(Job, old["id"])
        attempt = session.get(InferenceRun, run_id)
        assert attempt is not None and attempt.usage_json is None
        if protection == "terminated":
            assert new_id != old["id"] and stored.state is JobState.PAUSED
            assert json.loads(stored.checkpoint_json)["superseded_by"] == new_id
        else:
            assert new_id == old["id"] and stored.state is JobState.NEEDS_RECONCILIATION
            assert not json.loads(stored.checkpoint_json or "{}").get("superseded_by")
    if protection == "terminated":
        recovery = client.get(f"/api/jobs/{old['id']}/recovery").json()["data"]
        assert recovery["actions"] == [] and "接替" in recovery["summary"]
        adapter = FakeProviderAdapter(script=[])
        assert run_character_roster_job(factory, client.app.state.settings, job_id=old["id"],
            adapter_factory=lambda *_: adapter).state is JobState.PAUSED
        for operation, body in [("resume", None), ("reconcile", {"action": "retry"})]:
            blocked = client.post(f"/api/jobs/{old['id']}/{operation}", json=body)
            assert blocked.status_code == 409, blocked.text
            assert blocked.json()["error"]["details"]["job_id"] == new_id


@pytest.mark.parametrize("state", [JobState.FAILED, JobState.PAUSED, JobState.COMPLETED])
def test_terminal_rosters_do_not_block_new_start(migrated_client, state):
    client = migrated_client
    data, old = prepare(client)
    with client.app.state.session_factory() as session:
        session.get(Job, old["id"]).state = state
        session.commit()
    result = restart(client, data, old)
    assert result.status_code == 202 and result.json()["data"]["id"] != old["id"]
