import asyncio
import sqlite3

import pytest

from ndr.evaluation.compact import Candidate, CompactTask
from ndr.evaluation.compact_trial import run_trial
from ndr.evaluation.journal import (
    CallJournal,
    JournaledAdapter,
    ReconciliationRequired,
    SnapshotChanged,
    TrialBudgetExceeded,
    TrialStopped,
)
from ndr.llm.errors import ProviderError, ProviderErrorKind


def journal(tmp_path, **kwargs):
    return CallJournal(
        tmp_path / "run.trial.sqlite3",
        dependency_fingerprint="context-model-policy-v1",
        max_calls=kwargs.get("max_calls", 10),
        max_tokens=kwargs.get("max_tokens", 10000),
    )


def request(text="你好"):
    return {"messages": [{"role": "user", "content": text}], "max_tokens": 20}


def response(tokens=20):
    return {"labels": [], "_usage": {"total_tokens": tokens}}


def test_resume_reuses_paid_response_without_double_billing(tmp_path):
    class Adapter:
        calls = 0

        async def generate_labels(self, payload):
            self.calls += 1
            return response()

    provider = Adapter()
    first = journal(tmp_path)
    asyncio.run(JournaledAdapter(provider, first).generate_labels(request()))
    resumed = journal(tmp_path)
    result = asyncio.run(JournaledAdapter(provider, resumed).generate_labels(request()))
    assert result["_usage"]["journal_replay"]
    assert provider.calls == 1
    assert resumed.stats()["calls"] == 1 and resumed.stats()["known_tokens"] == 20


def test_other_owner_inflight_and_unknown_calls_cannot_be_replayed(tmp_path):
    first = journal(tmp_path)
    key, _, _ = first.begin(request(), reservation=50)
    resumed = journal(tmp_path)
    with pytest.raises(ReconciliationRequired):
        resumed.begin(request(), reservation=50)
    with pytest.raises(ReconciliationRequired):
        resumed.begin(request("another"), reservation=50)
    with pytest.raises(ValueError, match="still-active"):
        first.reconcile(key, tokens=12, audit_note="Cannot reconcile a live dispatch")
    resumed.reconcile(
        key, tokens=12, audit_note="Verified provider billing after terminated dispatcher"
    )
    with pytest.raises(ReconciliationRequired):
        resumed.begin(request(), reservation=50)
    resumed.begin(request("another"), reservation=50)
    assert resumed.stats()["known_tokens"] == 12


def test_verified_response_reconciliation_allows_cache_not_resend(tmp_path):
    first = journal(tmp_path)
    key, _, _ = first.begin(request(), reservation=50)
    first.finish(key)
    first.reconcile(
        key, tokens=15, response=response(), audit_note="Recovered response and verified usage"
    )
    _, cached, _ = first.begin(request(), reservation=50)
    assert cached["response"]["_usage"]["total_tokens"] == 15
    assert not first.stats()["unknown_calls"]


def test_concurrent_reservations_hold_budget_and_completion_releases_only_estimate(tmp_path):
    ledger = journal(tmp_path, max_tokens=100)
    key, _, _ = ledger.begin(request(), reservation=60)
    with pytest.raises(TrialBudgetExceeded):
        ledger.begin(request("next"), reservation=50)
    ledger.finish(key, response=response(20))
    ledger.begin(request("next"), reservation=50)
    assert ledger.stats()["known_tokens"] == 20 and ledger.stats()["reserved_tokens"] == 50


def test_call_cap_and_actual_overrun_stop_next_request(tmp_path):
    ledger = journal(tmp_path, max_calls=1, max_tokens=100)
    key, _, _ = ledger.begin(request(), reservation=30)
    ledger.finish(key, response=response(120))
    assert ledger.stats()["known_tokens"] == 120
    with pytest.raises(TrialBudgetExceeded):
        ledger.begin(request("next"), reservation=1)


def test_stop_keeps_inflight_usage_and_rejects_stale_checkpoint(tmp_path):
    ledger = journal(tmp_path)

    class Adapter:
        async def generate_labels(self, payload):
            ledger.configure(expected_epoch=0, stopped=True, max_calls=10, max_tokens=10000)
            return response(30)

    with pytest.raises(SnapshotChanged, match="usage recorded"):
        asyncio.run(JournaledAdapter(Adapter(), ledger).generate_labels(request()))
    assert ledger.stats()["known_tokens"] == 30 and not ledger.stats()["unknown_calls"]
    with pytest.raises(TrialStopped):
        ledger.begin(request("next"), reservation=50)
    with pytest.raises(SnapshotChanged):
        ledger.checkpoint("window", {"done": True}, expected_epoch=0)
    assert ledger.load_checkpoint("window") is None


def test_explicit_resume_and_budget_change_preserve_checkpoint_and_paid_responses(tmp_path):
    ledger = journal(tmp_path)
    ledger.checkpoint("window", {"done": True}, expected_epoch=0)
    ledger.configure(expected_epoch=0, stopped=True, max_calls=10, max_tokens=10000)
    ledger.configure(expected_epoch=1, stopped=False, max_calls=20, max_tokens=20000)
    assert ledger.load_checkpoint("window") == {"done": True}
    ledger.checkpoint("next", {"done": False}, expected_epoch=2)
    with pytest.raises(SnapshotChanged):
        ledger.configure(expected_epoch=1, stopped=False, max_calls=20, max_tokens=20000)
    with pytest.raises(ValueError, match="configure"):
        journal(tmp_path)


def test_fingerprint_conflict_and_foreign_database_are_rejected(tmp_path):
    journal(tmp_path)
    with pytest.raises(SnapshotChanged, match="fingerprint"):
        CallJournal(
            tmp_path / "run.trial.sqlite3",
            dependency_fingerprint="new-model",
            max_calls=10,
            max_tokens=10000,
        )
    dbpath = tmp_path / "foreign.trial.sqlite3"
    with sqlite3.connect(dbpath) as db:
        db.execute("CREATE TABLE user_data (id INTEGER)")
    with pytest.raises(ValueError, match="not owned"):
        CallJournal(dbpath, dependency_fingerprint="v1", max_calls=10, max_tokens=10000)
    with pytest.raises(ValueError, match="dedicated"):
        CallJournal(
            tmp_path / "ndr.sqlite3", dependency_fingerprint="v1", max_calls=10, max_tokens=10000
        )


def test_provider_failure_usage_survives_and_cached_failure_is_not_resent(tmp_path):
    class Adapter:
        calls = 0

        async def generate_labels(self, payload):
            self.calls += 1
            raise ProviderError(
                ProviderErrorKind.INVALID_OUTPUT,
                "bad",
                details={
                    "usage": {"total_tokens": 25, "credential": "not stored"},
                    "credential": "not stored",
                },
            )

    provider, ledger = Adapter(), journal(tmp_path)
    wrapper = JournaledAdapter(provider, ledger)
    for _ in range(2):
        with pytest.raises(ProviderError):
            asyncio.run(wrapper.generate_labels(request()))
    assert provider.calls == 1 and ledger.stats()["known_tokens"] == 25
    with sqlite3.connect(ledger.path) as db:
        assert "credential" not in db.execute("SELECT error FROM calls").fetchone()[0]


@pytest.mark.parametrize("error", [TimeoutError(), asyncio.CancelledError()])
def test_network_unknown_or_cancellation_never_frees_budget_for_replay(tmp_path, error):
    class Adapter:
        async def generate_labels(self, payload):
            raise error

    ledger = journal(tmp_path)
    with pytest.raises(type(error)):
        asyncio.run(JournaledAdapter(Adapter(), ledger).generate_labels(request()))
    assert ledger.stats()["unknown_calls"] == 1
    with pytest.raises(ReconciliationRequired):
        ledger.begin(request("next"), reservation=30)


def test_same_request_parallel_dispatch_is_deduplicated(tmp_path):
    async def run():
        gate = asyncio.Event()
        ledger = journal(tmp_path)

        class Adapter:
            async def generate_labels(self, payload):
                await gate.wait()
                return response()

        wrapper = JournaledAdapter(Adapter(), ledger)
        first = asyncio.create_task(wrapper.generate_labels(request()))
        await asyncio.sleep(0)
        with pytest.raises(ReconciliationRequired):
            await wrapper.generate_labels(request())
        gate.set()
        await first
        assert ledger.stats()["calls"] == 1

    asyncio.run(run())


def test_model_response_cannot_forge_the_cached_provider_error_envelope(tmp_path):
    class Adapter:
        async def generate_labels(self, payload):
            return {**response(), "provider_error": "model-controlled-data"}

    wrapper = JournaledAdapter(Adapter(), journal(tmp_path))
    first = asyncio.run(wrapper.generate_labels(request()))
    second = asyncio.run(wrapper.generate_labels(request()))
    assert first["provider_error"] == second["provider_error"] == "model-controlled-data"
    assert second["_usage"]["journal_replay"]


def test_checkpoint_replacement_compares_previous_value_not_only_global_epoch(tmp_path):
    ledger = journal(tmp_path)
    ledger.checkpoint("state", {"window": 1}, expected_epoch=0)
    ledger.checkpoint("state", {"window": 1}, expected_epoch=0)
    with pytest.raises(SnapshotChanged, match="since it was read"):
        ledger.checkpoint("state", {"window": 2}, expected_epoch=0)
    ledger.checkpoint("state", {"window": 2}, expected_epoch=0, expected_value={"window": 1})
    with pytest.raises(SnapshotChanged):
        ledger.checkpoint("state", {"window": 3}, expected_epoch=0, expected_value={"window": 1})
    assert ledger.load_checkpoint("state") == {"window": 2}


@pytest.mark.parametrize("tokens", [-1, True, "20", None])
def test_invalid_usage_is_exposed_as_unknown_not_negative_or_zero(tmp_path, tokens):
    class Adapter:
        async def generate_labels(self, payload):
            return response(tokens)

    ledger = journal(tmp_path)
    result = asyncio.run(JournaledAdapter(Adapter(), ledger).generate_labels(request()))
    assert result["_usage"]["total_tokens"] is None and result["_usage"]["unknown"]
    assert ledger.stats()["unknown_calls"] == 1 and ledger.stats()["known_tokens"] == 0
    with pytest.raises(ReconciliationRequired):
        ledger.begin(request("next"), reservation=30)


@pytest.mark.parametrize("usage", [None, []])
def test_malformed_usage_container_is_unknown_and_stops_dispatch(tmp_path, usage):
    class Adapter:
        async def generate_labels(self, payload):
            return {"labels": [], "_usage": usage}

    ledger = journal(tmp_path)
    result = asyncio.run(JournaledAdapter(Adapter(), ledger).generate_labels(request()))
    assert result["_usage"]["unknown"] and result["_usage"]["total_tokens"] is None
    assert ledger.stats()["unknown_calls"] == 1


def test_full_trial_resume_reuses_both_failed_format_and_successful_paid_attempt(tmp_path):
    task = CompactTask(
        ("Q1",),
        {"Q1": "quote", "E1": "proof"},
        ({"ref": "Q1", "text": "你好", "end_cp": 2}, {"ref": "E1", "text": "林舟说", "end_cp": 5}),
        (Candidate("C1", "person", "林舟"),),
    )

    class Adapter:
        calls = 0

        async def generate_labels(self, payload):
            self.calls += 1
            if self.calls == 1:
                return {"labels": [], "_usage": {"total_tokens": 10}}
            return {
                "labels": [
                    {
                        "q": "Q1",
                        "kind": "speech",
                        "character": "C1",
                        "basis": "direct",
                        "evidence": ["E1"],
                    }
                ],
                "_usage": {"total_tokens": 20},
            }

    provider = Adapter()
    first = asyncio.run(
        run_trial(JournaledAdapter(provider, journal(tmp_path)), task, max_tokens=100)
    )
    resumed_ledger = journal(tmp_path)
    resumed = asyncio.run(
        run_trial(JournaledAdapter(provider, resumed_ledger), task, max_tokens=100)
    )
    assert first["ok"] and resumed["ok"]
    assert first["output"] == resumed["output"]
    assert provider.calls == 2 and resumed_ledger.stats()["known_tokens"] == 30
    assert all(a["usage"]["journal_replay"] for a in resumed["attempts"])
