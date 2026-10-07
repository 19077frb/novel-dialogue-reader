import asyncio
import json
from copy import deepcopy

import pytest

from ndr.evaluation.compact import Candidate, CompactTask, compile_output
from ndr.evaluation.compact_trial import run_trial
from ndr.evaluation.journal import CallJournal, JournaledAdapter
from ndr.evaluation.partial_retry import isolate_failed_block
from ndr.llm.errors import InvalidModelOutput


def task():
    return CompactTask(
        ("Q1", "Q2", "Q3", "Q4"),
        {**{q: f"quote:{q}" for q in ["Q1", "Q2", "Q3", "Q4"]}, "G1": "gap", "E1": "proof"},
        tuple(
            {"ref": q, "text": q, "end_cp": i * 10}
            for i, q in enumerate(["Q1", "Q2", "G1", "Q3", "Q4", "E1"], 1)
        ),
        (Candidate("C1", "a", "林舟"),),
        {"G1": "Q3"},
    )


def label(q, **kwargs):
    return {
        "q": q,
        "kind": "speech",
        "character": "C1",
        "basis": "direct",
        "evidence": ["E1"],
        **kwargs,
    }


def payload(*rows, tokens=10, **kwargs):
    return {"labels": list(rows), "_usage": {"total_tokens": tokens}, **kwargs}


def proposal(**kwargs):
    return {"labels": [label(q) for q in task().quote_ids], **kwargs}


class Adapter:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.requests = []

    async def generate_labels(self, request):
        self.requests.append(deepcopy(request))
        return deepcopy(next(self.responses))


def request_targets(adapter):
    return [json.loads(r["messages"][1]["content"])["targets"] for r in adapter.requests]


def test_independent_error_retries_only_that_target_and_preserves_original_rows():
    bad = proposal()
    bad["labels"][1]["basis"] = "insufficient"
    block = isolate_failed_block(bad, task())
    assert block.pending == ("Q2",)
    assert block.retained_count == 3
    assert block.retry_task().context == task().context
    assert block.retry_task().candidates == task().candidates
    assert block.retry_task().gap_next_quote == {}
    combined = block.combine({"labels": [label("Q2")]})
    assert combined["labels"][0] == bad["labels"][0]
    assert len(compile_output(combined, task()).labels) == 4


def test_target_evidence_transitive_dependency_is_quarantined_in_both_directions():
    bad = proposal()
    bad["labels"][1]["basis"] = "wrong_enum"
    bad["labels"][2]["evidence"] = ["Q2"]
    bad["labels"][3]["evidence"] = ["Q3"]
    assert isolate_failed_block(bad, task()).pending == ("Q2", "Q3", "Q4")


def test_shared_invalid_new_identity_and_its_target_evidence_are_one_block():
    bad = proposal(
        new_characters=[{"ref": "N1", "name": "", "description": "门卫", "evidence": ["Q3"]}]
    )
    bad["labels"][1]["character"] = bad["labels"][3]["character"] = "N1"
    block = isolate_failed_block(bad, task())
    assert block.pending == ("Q2", "Q3", "Q4")
    assert not block.payload["new_characters"]


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_q",
        "missing_q",
        "unknown_q",
        "unknown_break",
        "duplicate_break",
        "unknown_request",
        "bad_identity_ref",
        "extra_top",
    ],
)
def test_unlocatable_global_errors_require_complete_retry(mutation):
    bad = proposal()
    if mutation == "duplicate_q":
        bad["labels"][1]["q"] = "Q1"
    elif mutation == "missing_q":
        bad["labels"].pop()
    elif mutation == "unknown_q":
        bad["labels"][1]["q"] = "Q9"
    elif mutation == "unknown_break":
        bad["breaks"] = ["G9"]
    elif mutation == "duplicate_break":
        bad["breaks"] = ["G1", "G1"]
    elif mutation == "unknown_request":
        bad["needs_context"] = ["G1"]
    elif mutation == "bad_identity_ref":
        bad["new_characters"] = [{"ref": None}]
    else:
        bad["unexpected"] = True
    assert isolate_failed_block(bad, task()) is None


def test_valid_scene_boundary_is_retained_but_partial_reply_cannot_change_it():
    bad = proposal(breaks=["G1"])
    bad["labels"][1]["basis"] = "wrong_enum"
    block = isolate_failed_block(bad, task())
    with pytest.raises(InvalidModelOutput, match="boundaries"):
        block.combine({"labels": [label("Q2")], "breaks": ["G1"]})
    compiled = compile_output(block.combine({"labels": [label("Q2")]}), task())
    assert compiled.labels[0].scene_ref != compiled.labels[2].scene_ref
    assert compiled.scene_updates[0].starts_at_quote_id == "quote:Q3"


def test_new_identity_references_are_reserved_not_merged_by_name():
    bad = proposal(
        new_characters=[{"ref": "N1", "name": "门卫", "description": "甲门卫", "evidence": ["E1"]}]
    )
    bad["labels"][0]["character"] = "N1"
    bad["labels"][1]["basis"] = "wrong_enum"
    block = isolate_failed_block(bad, task())
    with pytest.raises(InvalidModelOutput, match="reserved"):
        block.combine(
            {"labels": [label("Q2", character="N1")], "new_characters": bad["new_characters"]}
        )
    complete = block.combine(
        {
            "labels": [label("Q2", character="N2")],
            "new_characters": [
                {"ref": "N2", "name": "门卫", "description": "乙门卫", "evidence": ["E1"]}
            ],
        }
    )
    compiled = compile_output(complete, task())
    anonymous = [p for p in compiled.new_speakers if p.character_id is None]
    assert len(anonymous) == 2 and anonymous[0].temp_ref != anonymous[1].temp_ref


def test_successive_retries_preserve_newly_valid_items_and_account_failed_cost():
    adapter = Adapter(
        payload(
            label("Q1"),
            label("Q2", basis="wrong"),
            label("Q3", basis="wrong"),
            label("Q4"),
            tokens=10,
        ),
        payload(label("Q2"), label("Q3", basis="wrong"), tokens=20),
        payload(label("Q3"), tokens=30),
    )
    result = asyncio.run(run_trial(adapter, task(), max_format_retries=2, max_tokens=4000))
    assert result["ok"] and not result["first_pass_ok"]
    assert result["known_tokens"] == 60
    assert request_targets(adapter) == [["Q1", "Q2", "Q3", "Q4"], ["Q2", "Q3"], ["Q3"]]
    assert [r["retained_targets"] for r in result["attempts"]] == [0, 2, 3]
    assert [r["ok"] for r in result["attempts"]] == [False, False, True]
    assert result["attempts"][1]["raw"]["labels"][1]["basis"] == "wrong"
    assert all(r["max_tokens_override"] == r["max_tokens"] == 4000 for r in adapter.requests)
    assert len(result["output"]["labels"]) == 4


def test_bad_partial_reply_cannot_overwrite_kept_items_or_expand_targets():
    initial = payload(label("Q1"), label("Q2", basis="wrong"), label("Q3"), label("Q4"))
    adapter = Adapter(
        initial,
        payload(label("Q1", character=None, basis="insufficient", evidence=[])),
        payload(label("Q2")),
    )
    result = asyncio.run(run_trial(adapter, task(), max_format_retries=2))
    assert result["ok"]
    assert request_targets(adapter)[1:] == [["Q2"], ["Q2"]]
    assert result["final_payload"]["labels"][0] == initial["labels"][0]


def test_unknown_usage_during_partial_failure_stops_and_never_returns_partial_output():
    adapter = Adapter(
        payload(label("Q1"), label("Q2", basis="wrong"), label("Q3"), label("Q4")),
        {"labels": [label("Q2", basis="wrong")]},
        payload(label("Q2")),
    )
    result = asyncio.run(run_trial(adapter, task(), max_format_retries=2))
    assert not result["ok"] and result["output"] is None and result["reconciliation_required"]
    assert len(adapter.requests) == 2 and result["known_tokens"] == 10


def test_explicit_disabled_strategy_keeps_complete_retry_for_comparison():
    bad = payload(label("Q1"), label("Q2", basis="wrong"), label("Q3"), label("Q4"))
    adapter = Adapter(bad, {**proposal(), "_usage": {"total_tokens": 20}})
    result = asyncio.run(run_trial(adapter, task(), targeted_retries=False))
    assert result["ok"]
    assert request_targets(adapter) == [list(task().quote_ids)] * 2


def test_durable_replay_of_partial_attempts_has_zero_new_provider_calls(tmp_path):
    adapter = Adapter(
        payload(label("Q1"), label("Q2", basis="wrong"), label("Q3"), label("Q4")),
        payload(label("Q2"), tokens=20),
    )
    ledger = CallJournal(
        tmp_path / "partial.trial.sqlite3",
        dependency_fingerprint="original-model-strategy-1",
        max_calls=4,
        max_tokens=100000,
    )
    wrapped = JournaledAdapter(adapter, ledger)
    first = asyncio.run(run_trial(wrapped, task()))
    restored = asyncio.run(run_trial(wrapped, task()))
    assert first["ok"] and restored["ok"]
    assert first["output"] == restored["output"]
    assert len(adapter.requests) == ledger.stats()["calls"] == 2
    assert ledger.stats()["known_tokens"] == 30
    assert all(r["usage"]["journal_replay"] for r in restored["attempts"])
