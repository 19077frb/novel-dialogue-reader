import asyncio
import json
from dataclasses import replace

import pytest

from ndr.evaluation.compact import Candidate, CompactTask
from ndr.evaluation.compact_trial import run_trial
from ndr.evaluation.journal import (
    CallJournal,
    ReconciliationRequired,
    SnapshotChanged,
    TrialBudgetExceeded,
)
from ndr.evaluation.pipeline import PipelinePolicy, execute_pipeline, pipeline_fingerprint
from ndr.evaluation.scene_plan import WindowDependency, validate_scene_plan


def setup(tmp_path, *, concurrency=2, relay=True, max_calls=20, max_tokens=100000):
    text = "林舟说：「甲。」\n林舟说：「乙。」\n林舟说：「丙。」\n林舟说：「丁。」\n"
    tasks = {}
    for i, char in enumerate("甲乙丙丁", 1):
        a = text.index(f"「{char}")
        b = a + 4
        tasks[f"W{i}"] = CompactTask(
            ("Q1",),
            {"Q1": f"quote:{a}:{b}", "E1": f"proof:{a - 4}:{a}"},
            (
                {"ref": "E1", "start_cp": a - 4, "end_cp": a, "text": text[a - 4 : a]},
                {"ref": "Q1", "start_cp": a, "end_cp": b, "text": text[a:b]},
            ),
            (Candidate("C1", "a", "林舟"),),
        )
    plan = validate_scene_plan(
        {
            "scenes": [
                {"scene": "a", "windows": ["W1", "W2"], "depends_on": []},
                {"scene": "b", "windows": ["W3", "W4"], "depends_on": []},
            ]
        },
        tuple(tasks),
    )
    policy = PipelinePolicy(
        "mock:model:disabled", concurrency=concurrency, relay=relay, max_format_retries=0
    )
    ledger = CallJournal(
        tmp_path / "calls.trial.sqlite3",
        dependency_fingerprint=pipeline_fingerprint(text, tasks, plan, policy),
        max_calls=max_calls,
        max_tokens=max_tokens,
    )
    return text, tasks, plan, policy, ledger


def result(data, *, unknown=False, bad=False):
    return {
        "labels": []
        if bad
        else [
            {"q": q, "kind": "speech", "character": "C1", "basis": "direct", "evidence": ["E1"]}
            for q in data["targets"]
        ],
        "_usage": {"total_tokens": None if unknown else 20, "unknown": unknown},
    }


class Adapter:
    def __init__(self):
        self.active = self.peak = 0
        self.seen = []

    async def generate_labels(self, request):
        data = json.loads(request["messages"][1]["content"])
        self.seen.append(data)
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return result(data)


def run(adapter, fixture, **kwargs):
    text, tasks, plan, policy, ledger = fixture
    return asyncio.run(
        execute_pipeline(
            adapter, ledger, text=text, tasks=tasks, plan=plan, policy=policy, **kwargs
        )
    )


def quote(data):
    return next(r["text"] for r in data["context"] if r["ref"] == data["targets"][0])


def test_independent_chains_overlap_but_each_chain_waits_and_relay_is_explicit(tmp_path):
    fixture = setup(tmp_path)
    adapter = Adapter()
    completed = run(adapter, fixture)
    assert completed["complete"]
    assert completed["paid_calls_this_invocation"] == 4
    assert completed["known_tokens_this_invocation"] == 80
    assert adapter.peak == 2
    assert [quote(r) for r in adapter.seen[:2]] == ["「甲。」", "「丙。」"]
    assert "previous_turn_candidates" not in adapter.seen[0]
    later = {quote(r): r for r in adapter.seen}
    assert later["「乙。」"]["previous_turn_candidates"][0]["status"] == "unconfirmed"
    assert later["「丁。」"]["previous_turn_candidates"][0]["candidate"] == "C1"


def test_serial_limit_and_disabled_relay_do_not_invent_memory(tmp_path):
    fixture = setup(tmp_path, concurrency=1, relay=False)
    adapter = Adapter()
    assert run(adapter, fixture)["complete"]
    assert adapter.peak == 1
    assert all("previous_turn_candidates" not in r for r in adapter.seen)


@pytest.mark.parametrize("relay", [False, True])
def test_same_chain_reuses_slots_independently_of_answer_relay(tmp_path, relay):
    fixture = setup(tmp_path, relay=relay)
    completed = run(Adapter(), fixture)
    windows = completed["windows"]
    for first, second in [("W1", "W2"), ("W3", "W4")]:
        left, right = windows[first]["result"]["output"], windows[second]["result"]["output"]
        assert left["labels"][0]["assignment"] == "NEW"
        assert right["labels"][0]["assignment"] == "EXISTING"
        assert not right["new_speakers"]
        assert left["labels"][0]["speaker_ref"] == right["labels"][0]["speaker_ref"]
        assert left["labels"][0]["scene_ref"] == right["labels"][0]["scene_ref"]
    assert (
        windows["W1"]["result"]["output"]["labels"][0]["scene_ref"]
        != windows["W3"]["result"]["output"]["labels"][0]["scene_ref"]
    )


def test_cross_scene_dependency_transfers_evidence_candidates_not_scene_slots(tmp_path):
    text, tasks, _, policy, _ = setup(tmp_path)
    plan = validate_scene_plan(
        {
            "scenes": [
                {"scene": "a", "windows": ["W1"], "depends_on": []},
                {"scene": "b", "windows": ["W2"], "depends_on": ["a"]},
                {"scene": "c", "windows": ["W3"], "depends_on": []},
                {"scene": "d", "windows": ["W4"], "depends_on": ["b", "c"]},
            ]
        },
        tuple(tasks),
    )
    ledger = CallJournal(
        tmp_path / "cross.trial.sqlite3",
        dependency_fingerprint=pipeline_fingerprint(text, tasks, plan, policy),
        max_calls=20,
        max_tokens=100000,
    )
    adapter = Adapter()
    result = run(adapter, (text, tasks, plan, policy, ledger))
    observed = {quote(r): r for r in adapter.seen}
    assert "previous_turn_candidates" not in observed["「丙。」"]
    assert len(observed["「乙。」"]["previous_turn_candidates"]) == 1
    assert len(observed["「丁。」"]["previous_turn_candidates"]) == 2
    assert all(
        v["status"] == "unconfirmed" for v in observed["「丁。」"]["previous_turn_candidates"]
    )
    windows = result["windows"]
    assert windows["W2"]["result"]["output"]["labels"][0]["assignment"] == "NEW"
    assert (
        windows["W2"]["result"]["output"]["labels"][0]["scene_ref"]
        != windows["W1"]["result"]["output"]["labels"][0]["scene_ref"]
    )
    replay = Adapter()
    restored = run(replay, (text, tasks, plan, policy, ledger))
    assert restored["paid_calls_this_invocation"] == 0 and not replay.seen


def test_resume_complete_pipeline_has_no_provider_calls_or_incremental_tokens(tmp_path):
    fixture = setup(tmp_path)
    first = run(Adapter(), fixture)
    adapter = Adapter()
    restored = run(adapter, fixture)
    assert restored["complete"]
    assert set(restored["reused_windows"]) == set(fixture[1])
    assert restored["paid_calls_this_invocation"] == restored["known_tokens_this_invocation"] == 0
    assert restored["ledger"]["known_tokens"] == first["ledger"]["known_tokens"] == 80
    assert adapter.seen == []


def test_failed_window_blocks_only_its_chain(tmp_path):
    class Bad(Adapter):
        async def generate_labels(self, request):
            data = json.loads(request["messages"][1]["content"])
            self.seen.append(data)
            await asyncio.sleep(0.01)
            return result(data, bad=quote(data) == "「甲。」")

    fixture = setup(tmp_path)
    adapter = Bad()
    completed = run(adapter, fixture)
    assert not completed["complete"]
    assert [completed["windows"][w]["state"] for w in fixture[1]] == [
        "failed",
        "blocked",
        "complete",
        "complete",
    ]
    assert len(adapter.seen) == 3
    assert completed["ledger"]["known_tokens"] == 60


def test_unknown_usage_stops_new_windows_and_inflight_known_response_is_accounted(tmp_path):
    class Unknown(Adapter):
        async def generate_labels(self, request):
            data = json.loads(request["messages"][1]["content"])
            self.seen.append(data)
            await asyncio.sleep(0.01 if quote(data) == "「甲。」" else 0.03)
            return result(data, unknown=quote(data) == "「甲。」")

    fixture = setup(tmp_path)
    adapter = Unknown()
    with pytest.raises(ReconciliationRequired):
        run(adapter, fixture)
    assert len(adapter.seen) == 2
    assert fixture[-1].stats()["known_tokens"] == 20
    assert fixture[-1].stats()["unresolved_calls"] == 1
    with pytest.raises(ReconciliationRequired):
        run(adapter, fixture)
    assert len(adapter.seen) == 2


def test_stop_or_budget_change_accounts_inflight_without_advancing_stale_checkpoints(tmp_path):
    fixture = setup(tmp_path)
    ledger = fixture[-1]

    class Stop(Adapter):
        async def generate_labels(self, request):
            data = json.loads(request["messages"][1]["content"])
            self.seen.append(data)
            await asyncio.sleep(0.01)
            if quote(data) == "「甲。」":
                ledger.configure(expected_epoch=0, stopped=True, max_calls=20, max_tokens=100000)
            return result(data)

    adapter = Stop()
    with pytest.raises(SnapshotChanged):
        run(adapter, fixture)
    assert len(adapter.seen) == 2 and ledger.stats()["known_tokens"] == 40
    assert ledger.load_checkpoint("window:W1") is None
    assert ledger.load_checkpoint("window:W3") is None
    ledger.configure(expected_epoch=1, stopped=False, max_calls=20, max_tokens=100000)
    resumed = run(Adapter(), fixture)
    assert resumed["complete"]
    assert resumed["paid_calls_this_invocation"] == 2
    assert resumed["ledger"]["known_tokens"] == 80


def test_external_cancel_waits_for_paid_responses_and_sets_stop(tmp_path):
    fixture = setup(tmp_path)
    ledger = fixture[-1]

    async def exercise():
        ready, release = asyncio.Event(), asyncio.Event()

        class Hold(Adapter):
            async def generate_labels(self, request):
                data = json.loads(request["messages"][1]["content"])
                self.seen.append(data)
                if len(self.seen) == 2:
                    ready.set()
                await release.wait()
                return result(data)

        adapter = Hold()
        text, tasks, plan, policy, _ = fixture
        handle = asyncio.create_task(
            execute_pipeline(adapter, ledger, text=text, tasks=tasks, plan=plan, policy=policy)
        )
        await ready.wait()
        handle.cancel()
        await asyncio.sleep(0)
        assert ledger.stats()["stopped"]
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await handle
        assert len(adapter.seen) == 2

    asyncio.run(exercise())
    assert ledger.stats()["known_tokens"] == 40 and not ledger.stats()["unknown_calls"]
    assert ledger.load_checkpoint("window:W1") is None


def test_budget_reservation_failure_keeps_finished_other_window_for_resume(tmp_path):
    fixture = setup(tmp_path, max_calls=1)
    adapter = Adapter()
    with pytest.raises(TrialBudgetExceeded):
        run(adapter, fixture)
    ledger = fixture[-1]
    assert ledger.stats()["calls"] == 1 and ledger.stats()["known_tokens"] == 20
    ledger.configure(expected_epoch=0, stopped=False, max_calls=20, max_tokens=100000)
    resumed = run(Adapter(), fixture)
    assert resumed["complete"] and resumed["paid_calls_this_invocation"] == 3


def test_changed_model_or_mutated_checkpoint_cannot_be_reused(tmp_path):
    fixture = setup(tmp_path)
    text, tasks, plan, policy, ledger = fixture
    with pytest.raises(SnapshotChanged):
        run(Adapter(), (text, tasks, plan, replace(policy, model_scope="other:model"), ledger))
    run(Adapter(), fixture)
    saved = ledger.load_checkpoint("window:W1")
    changed = json.loads(json.dumps(saved))
    changed["compiled_task"]["context"][0]["text"] = "伪造证明"
    ledger.checkpoint("window:W1", changed, expected_epoch=0, expected_value=saved)
    with pytest.raises(SnapshotChanged):
        run(Adapter(), fixture)


def test_custom_processor_cannot_mutate_its_input_snapshot(tmp_path):
    text, tasks, plan, policy, _ = setup(tmp_path)
    policy = replace(policy, processor_version="mutating-test-1")
    ledger = CallJournal(
        tmp_path / "custom.trial.sqlite3",
        dependency_fingerprint=pipeline_fingerprint(text, tasks, plan, policy),
        max_calls=20,
        max_tokens=100000,
    )

    async def processor(adapter, task):
        response = await run_trial(adapter, task, max_format_retries=0)
        task.references["Q1"] = "forged_id"
        return response

    with pytest.raises(SnapshotChanged, match="modified"):
        run(Adapter(), (text, tasks, plan, policy, ledger), process_window=processor)


def test_plan_and_full_original_snapshot_are_checked_before_call(tmp_path):
    text, tasks, plan, policy, ledger = setup(tmp_path)
    with pytest.raises(ValueError, match="snapshot"):
        run(Adapter(), (text.replace("林舟", "周遥"), tasks, plan, policy, ledger))
    bad = (plan[0], WindowDependency("W2", "a", ()), *plan[2:])
    with pytest.raises(ValueError, match="Same-scene"):
        run(Adapter(), (text, tasks, bad, policy, ledger))
    assert ledger.stats()["calls"] == 0


@pytest.mark.parametrize(
    "kwargs",
    [{"concurrency": 3}, {"concurrency": True}, {"max_tokens": 1.5}, {"max_format_retries": True}],
)
def test_invalid_pipeline_policy_is_rejected(kwargs):
    with pytest.raises(ValueError):
        PipelinePolicy("mock", **kwargs)
