"""Task diagnostics use fake providers and isolated databases without changing execution."""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from integration.test_expression_pipeline import StageAdapter, prepare
from integration.test_jobs import _create_job, _fake_profile, _import, _run_with_fake
from integration.test_roster_pipeline import fixed, primary, run
from integration.test_roster_pipeline import prepare as prepare_roster
from integration.test_sourced_roster_jobs import prepare as prepare_plain_roster
from integration.test_sourced_roster_jobs import response
from ndr import diagnostics
from ndr.api import jobs as job_routes
from ndr.domain.enums import InferenceRunState, JobState
from ndr.jobs import local_finalization, roster, scheduler
from ndr.llm.adapters.fake import FakeProviderAdapter
from ndr.llm.errors import ProviderError, ProviderErrorKind
from ndr.recovery.service import recover_on_startup
from ndr.storage.models import InferenceRun, Job, ModelProfile
from ndr.storage.transactions import transaction


def events(caplog, job_id=None):
    return [record.diagnostic_payload for record in caplog.records
            if hasattr(record, "diagnostic_payload")
            and (job_id is None or record.diagnostic_payload.get("job_id") == job_id)]


def dialogue(client, key, **kwargs):
    create, factory = prepare(client, **kwargs)
    job = create(key)
    with transaction(factory) as session:
        stored = session.get(Job, job["id"])
        stored.budget_json = json.dumps({**json.loads(stored.budget_json), "max_recheck_rounds": 0})
    return job, factory


def test_dialogue_admission_success_and_control_are_correlated(fake_provider_client, caplog):
    caplog.set_level(logging.INFO, logger="ndr.diagnostics")
    client = fake_provider_client
    job, factory = dialogue(client, "diagnostic-success")
    adapter = StageAdapter(["林舟"])
    result = scheduler.run_job(factory, client.app.state.settings, job_id=job["id"],
                               adapter_factory=lambda *_: adapter)
    assert result.state is JobState.COMPLETED and len(adapter.calls) == 1
    rows = events(caplog, job["id"])
    admitted = next(row for row in rows if row["event"] == "job_admitted")
    assert admitted["created"] and not admitted["dispatched"] and admitted["request_id"]
    started = next(row for row in rows if row["event"] == "dialogue_call_start")
    returned = next(row for row in rows if row["event"] == "dialogue_call_returned")
    assert started["run_id"] == returned["run_id"] and started["window_id"] == returned["window_id"]
    assert not returned["usage_unknown"]
    finished = next(row for row in rows if row["event"] == "job_worker_finished")
    assert finished["state"] == "COMPLETED" and finished["calls"] == 1
    assert "林舟" not in json.dumps(rows, ensure_ascii=False)
    assert client.post(f"/api/jobs/{job['id']}/pause").status_code == 202
    control = events(caplog, job["id"])[-2:]
    assert any(row["event"] == "job_control" and row["action"] == "pause"
               and not row["dispatched"] for row in control)


@pytest.mark.parametrize("rounds", [0, 1])
def test_returned_receipt_recovery_logs_zero_new_dispatch(
    fake_provider_client, caplog, monkeypatch, rounds,
):
    caplog.set_level(logging.INFO, logger="ndr.diagnostics")
    client = fake_provider_client
    job, factory = dialogue(client, "diagnostic-restore")
    with transaction(factory) as session:
        stored = session.get(Job, job["id"])
        stored.budget_json = json.dumps({**json.loads(stored.budget_json), "max_recheck_rounds": rounds})
    adapter = StageAdapter(["林舟"])
    apply = scheduler._apply_payload

    def broken(*args, **kwargs):
        raise RuntimeError("secret prompt and novel text")

    monkeypatch.setattr(scheduler, "_apply_payload", broken)
    first = scheduler.run_job(factory, client.app.state.settings, job_id=job["id"],
                              adapter_factory=lambda *_: adapter)
    assert first.state is JobState.FAILED
    failures = [row for row in events(caplog, job["id"]) if row["event"] == "task_failure"]
    assert failures and "secret prompt" not in json.dumps(failures)
    monkeypatch.setattr(scheduler, "_apply_payload", apply)
    with transaction(factory) as session:
        session.get(Job, job["id"]).state = JobState.QUEUED
    caplog.clear()
    second = scheduler.run_job(factory, client.app.state.settings, job_id=job["id"],
                               adapter_factory=lambda *_: adapter)
    assert second.state is JobState.COMPLETED and len(adapter.calls) == 1 + rounds and second.calls == 0
    rows = events(caplog, job["id"])
    assert any(row["event"] == "dialogue_call_restored" and row["restored"]
               and row["calls"] == 0 for row in rows)
    assert not any(row["event"] == "dialogue_call_start" for row in rows)
    assert next(row for row in rows if row["event"] == "job_worker_finished")["calls"] == 0
    if rounds:
        assert any(row["event"] == "dialogue_call_restored" and row["stage"] == "review:1"
                   for row in rows)


@pytest.mark.parametrize("kind", [ProviderErrorKind.TIMEOUT, ProviderErrorKind.RATE_LIMITED])
def test_provider_failure_and_bounded_retry_have_safe_reasons(
    fake_provider_client, caplog, kind,
):
    caplog.set_level(logging.INFO, logger="ndr.diagnostics")
    client = fake_provider_client
    job, factory = dialogue(client, "diagnostic-provider")
    client.app.state.settings.rate_limit_backoff_base_seconds = 0
    adapter = FakeProviderAdapter(script=[ProviderError(
        kind, "private provider response", details={"status_code": 429, "body": "private body"},
    )])
    result = scheduler.run_job(factory, client.app.state.settings, job_id=job["id"],
                               adapter_factory=lambda *_: adapter)
    rows = events(caplog, job["id"])
    returned = next(row for row in rows if row["event"] == "dialogue_call_returned")
    assert returned["usage_unknown"]
    assert "private" not in json.dumps(rows)
    retry = [row for row in rows if row["event"] == "dialogue_call_retry"]
    finished = next(row for row in rows if row["event"] == "job_worker_finished")
    if kind is ProviderErrorKind.TIMEOUT:
        assert result.state is JobState.NEEDS_RECONCILIATION
        assert len(adapter.calls) == 1 and not retry
        assert finished["calls"] == 1
        assert any(row.get("reason_code") == "unknown_outcome" for row in rows)
    else:
        assert len(adapter.calls) == 2
        assert finished["calls"] == 2
        assert retry[0]["retry_type"] == "transport" and retry[0]["delay_ms"] == 0
        with factory() as session:
            runs = list(session.scalars(select(InferenceRun).where(InferenceRun.job_id == job["id"])))
            assert len(runs) == 2 and runs[0].state is InferenceRunState.FAILED


def test_concurrent_workers_keep_dispatch_counts_separate(fake_provider_client, caplog):
    caplog.set_level(logging.INFO, logger="ndr.diagnostics")
    client = fake_provider_client
    first, factory = dialogue(client, "diagnostic-concurrent-one")
    with transaction(factory) as session:
        profile_id = json.loads(session.get(Job, first["id"]).profile_snapshot_json)["profile_id"]
        session.get(ModelProfile, profile_id).name = "stages-one"
    second, _ = dialogue(client, "diagnostic-concurrent-two",
                         text="第一章\n林舟说：「晚上好。」\n许晴答：「你好。」\n顾宁点头。")
    barrier = threading.Barrier(2)

    class ConcurrentAdapter(StageAdapter):
        async def generate_labels(self, payload):
            barrier.wait(timeout=10)
            return await super().generate_labels(payload)

    def execute(job):
        return scheduler.run_job(factory, client.app.state.settings, job_id=job["id"],
                                 adapter_factory=lambda *_: ConcurrentAdapter(["林舟"]))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(execute, [first, second]))
    assert all(result.state is JobState.COMPLETED for result in results)
    for job in [first, second]:
        finished = next(row for row in events(caplog, job["id"])
                        if row["event"] == "job_worker_finished")
        assert finished["calls"] == 1
    assert scheduler._diagnostic_dispatches.get() is None


def test_reused_admission_logs_existing_job_without_dispatch(fake_provider_client, caplog):
    caplog.set_level(logging.INFO, logger="ndr.diagnostics")
    create, _ = prepare(fake_provider_client)
    first = create("diagnostic-idempotent")
    reused = create("diagnostic-idempotent")
    assert first["id"] == reused["id"]
    admitted = [row for row in events(caplog, first["id"]) if row["event"] == "job_admitted"]
    assert [row["created"] for row in admitted] == [True, False]
    assert not any(row["dispatched"] for row in admitted)


def test_failed_control_commit_does_not_emit_success(fake_provider_client, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="ndr.diagnostics")
    client = fake_provider_client
    job, factory = dialogue(client, "diagnostic-control-rollback")

    @contextmanager
    def fail_commit(factory):
        with transaction(factory) as session:
            yield session
            raise RuntimeError("private commit details")

    monkeypatch.setattr(job_routes, "transaction", fail_commit)
    caplog.clear()
    with pytest.raises(RuntimeError, match="private commit details"):
        client.post(f"/api/jobs/{job['id']}/pause")
    assert not any(row["event"] == "job_control" for row in events(caplog, job["id"]))
    with factory() as session:
        assert session.get(Job, job["id"]).state is JobState.QUEUED


def test_roster_pipeline_logs_each_stage_and_retry(migrated_client, caplog):
    caplog.set_level(logging.INFO, logger="ndr.diagnostics")
    client = migrated_client
    _, job = prepare_roster(client, max_roster_repairs=2)
    bad = {"schema_version": "wrong", "_usage": {"total_tokens": 7}}
    adapter = FakeProviderAdapter(script=[primary(), bad, fixed()])
    result = run(client, job, adapter)
    assert result.state is JobState.COMPLETED and len(adapter.calls) == 3
    rows = events(caplog, job["id"])
    starts = [row for row in rows if row["event"] == "roster_call_start"]
    assert [row["stage"] for row in starts] == ["primary:0", "repair:0", "repair:1"]
    assert len({row["run_id"] for row in starts}) == 3
    assert any(row["event"] == "roster_output_rejected" for row in rows)
    assert any(row["event"] == "roster_call_retry" and row["retry_type"] == "repair" for row in rows)


def test_plain_roster_preparation_failure_is_logged_without_call(migrated_client, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="ndr.diagnostics")
    client = migrated_client
    _, job = prepare_plain_roster(client)

    def broken(*args, **kwargs):
        raise ValueError("private source and API key")

    monkeypatch.setattr(roster, "_prepare_input", broken)
    adapter = FakeProviderAdapter(script=[response()])
    result = roster.run_character_roster_job(
        client.app.state.session_factory, client.app.state.settings, job_id=job["id"],
        adapter_factory=lambda *_: adapter,
    )
    assert result.state is JobState.FAILED and not adapter.calls
    rows = events(caplog, job["id"])
    failure = next(row for row in rows if row["event"] == "task_failure")
    assert failure["reason_code"] == "invalid_roster_input" and failure["stage"] == "preparing"
    assert "private" not in json.dumps(rows)
    assert rows[-1]["event"] == "job_worker_finished" and rows[-1]["calls"] == 0


def test_logging_failure_preserves_task_state_and_call_count(fake_provider_client, monkeypatch):
    client = fake_provider_client
    job, factory = dialogue(client, "diagnostic-io-failure")

    def broken(*args, **kwargs):
        raise OSError("private filesystem details")

    monkeypatch.setattr(diagnostics, "_write", broken)
    adapter = StageAdapter(["林舟"])
    result = scheduler.run_job(factory, client.app.state.settings, job_id=job["id"],
                               adapter_factory=lambda *_: adapter)
    assert result.state is JobState.COMPLETED and len(adapter.calls) == 1
    with factory() as session:
        assert session.get(Job, job["id"]).state is JobState.COMPLETED


def test_semantic_rejection_records_program_code_without_model_text(
    fake_provider_client, migrated_settings, caplog,
):
    caplog.set_level(logging.INFO, logger="ndr.diagnostics")
    client = fake_provider_client
    data = _import(client)
    job = _create_job(client, data["book_id"], _fake_profile(client), key="diagnostic-validation",
                      budget={"max_format_retries": 0})
    adapter = FakeProviderAdapter(script=[{"schema_version": "1.0", "labels": []}],
                                  usage={"total_tokens": 10})
    result = _run_with_fake(migrated_settings, job["id"], adapter)
    assert result.state is JobState.FAILED and len(adapter.calls) == 1
    rejected = next(row for row in events(caplog, job["id"])
                    if row["event"] == "dialogue_output_rejected")
    assert rejected["run_id"] and rejected["window_id"]
    assert rejected["error_code"] and rejected["count"] > 0
    assert rejected["stage"] == "schema_validation"


def test_local_settlement_deduplicates_without_changing_retry_schedule(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="ndr.diagnostics")
    factory = object()
    clock, attempts = [0.0], []
    monkeypatch.setattr(local_finalization, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def settle():
        attempts.append(clock[0])
        if len(attempts) < 4:
            raise RuntimeError("private database payload")

    local_finalization.defer_settlement(factory, "settlement-test", settle)
    try:
        for now in [0, 0.5, 1, 2, 3, 6, 7]:
            clock[0] = now
            local_finalization.retry_settlements(factory)
        assert attempts == [0, 1, 3, 7]
        assert not local_finalization.settlement_pending("settlement-test")
        rows = events(caplog, "settlement-test")
        failures = [row for row in rows if row["event"] == "task_failure"]
        assert len(failures) == 1
        repeated = next(row for row in rows if row["event"] == "diagnostic_repeated")
        assert repeated["repeat_count"] == 2
        assert rows[-1]["event"] == "settlement_recovered" and rows[-1]["attempt"] == 4
    finally:
        local_finalization.forget_settlements(factory)
        diagnostics.flush_repeats(job_id="settlement-test")


def test_startup_recovery_logs_committed_state(fake_provider_client, caplog):
    caplog.set_level(logging.INFO, logger="ndr.diagnostics")
    client = fake_provider_client
    job, factory = dialogue(client, "diagnostic-startup")
    with transaction(factory) as session:
        session.get(Job, job["id"]).state = JobState.RUNNING
        session.add(InferenceRun(job_id=job["id"], window_id="interrupted-window",
                                 state=InferenceRunState.DISPATCHED,
                                 request_fingerprint="frozen", profile_snapshot_json="{}"))
    recover_on_startup(factory)
    recovered = next(row for row in events(caplog, job["id"])
                     if row["event"] == "job_startup_recovered")
    with factory() as session:
        assert recovered["state"] == session.get(Job, job["id"]).state.value
    assert recovered["state"] == "NEEDS_RECONCILIATION" and not recovered["dispatched"]
