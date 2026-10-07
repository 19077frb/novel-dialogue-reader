import asyncio
import json
from copy import deepcopy
from dataclasses import replace

import pytest

from ndr.evaluation.compact import Candidate, CompactTask
from ndr.evaluation.evidence_view import NonblankEvidenceViewAdapter
from ndr.evaluation.frame_trial import frame_trial_fingerprint, isolate_frame_block, run_frame_trial
from ndr.evaluation.journal import CallJournal, JournaledAdapter
from ndr.evaluation.partial_retry import isolate_seeded_block
from ndr.llm.errors import InvalidModelOutput, ProviderError, ProviderErrorKind


def task():
    return CompactTask(
        ("Q1", "Q2", "Q3"),
        {q: q.lower() for q in ("Q1", "Q2", "Q3", "G1", "E1")},
        (
            {"ref": "E1", "text": "林舟说道。", "start_cp": 0, "end_cp": 5},
            {"ref": "Q1", "text": "你好。", "start_cp": 5, "end_cp": 8},
            {"ref": "G1", "text": "\n", "start_cp": 8, "end_cp": 9},
            {"ref": "Q2", "text": "谢谢。", "start_cp": 9, "end_cp": 12},
            {"ref": "Q3", "text": "再见。", "start_cp": 12, "end_cp": 15},
        ),
        (Candidate("C1", "a", "林舟"), Candidate("C2", "b", "周遥")),
        gap_next_quote={"G1": "Q2"},
    )


def row(q, **changes):
    return {
        "q": q,
        "kind": "speech",
        "character": "C1",
        "basis": "direct",
        "evidence": ["E1"],
        "addressee": "C2",
        "addressee_evidence": [q],
        **changes,
    }


def bad():
    return {"labels": [row("Q1"), row("Q2", evidence=["G1"]), row("Q3")]}


def factory(t):
    return NonblankEvidenceViewAdapter(None, t)


class Recording:
    def __init__(self, responses):
        self.responses, self.seen = responses, []

    async def generate_labels(self, request):
        self.seen.append(deepcopy(request))
        response = self.responses[len(self.seen) - 1]
        if isinstance(response, BaseException):
            raise response
        return deepcopy(response)


def test_isolation_preserves_frames_and_original_boundary_namespace():
    t, raw = task(), {**bad(), "breaks": ["B1"]}
    before = deepcopy(raw)
    block = isolate_frame_block(raw, t, factory)
    assert raw == before
    assert block.retained.pending == ("Q2",)
    assert block.retained.retained_count == 2
    child = block.retained.retry_task()
    assert child.context == t.context and child.candidates == t.candidates
    assert not child.gap_next_quote
    merged = block.combine({"labels": [row("Q2")]}, factory(child))
    assert merged["labels"] == [row("Q1"), row("Q2"), row("Q3")]
    assert merged["breaks"] == ["B1"]
    stripped, _ = factory(t).compile_payload(merged)
    assert stripped["breaks"] == ["G1"]


@pytest.mark.parametrize("field", ["evidence", "addressee_evidence"])
def test_both_speaker_and_receiver_dependencies_are_quarantined(field):
    raw = bad()
    raw["labels"][2][field] = ["Q2"]
    block = isolate_frame_block(raw, task(), factory)
    assert block.retained.pending == ("Q2", "Q3")
    assert list(block.frames) == ["Q1"]


def test_shared_anonymous_identity_is_retried_as_a_unit():
    raw = bad()
    for index in (1, 2):
        raw["labels"][index]["character"] = "N1"
    raw["new_characters"] = [
        {"ref": "N1", "name": "男客", "description": "店里的顾客", "evidence": ["E1"]}
    ]
    block = isolate_frame_block(raw, task(), factory)
    assert block.retained.pending == ("Q2", "Q3")
    assert not block.retained.reserved_identities


def test_retained_identity_cannot_be_reused_in_partial_retry():
    raw = bad()
    raw["labels"][0]["character"] = "N1"
    raw["new_characters"] = [
        {"ref": "N1", "name": "男客", "description": "店里的顾客", "evidence": ["E1"]}
    ]
    block = isolate_frame_block(raw, task(), factory)
    assert block.retained.reserved_identities == ("N1",)
    child = block.retained.retry_task()
    with pytest.raises(InvalidModelOutput, match="reserved"):
        block.combine(
            {"labels": [row("Q2")], "new_characters": raw["new_characters"]}, factory(child)
        )


def test_anonymous_proof_target_dependency_is_quarantined():
    raw = bad()
    raw["labels"][2]["character"] = "N1"
    raw["new_characters"] = [
        {"ref": "N1", "name": "男客", "description": "店里的顾客", "evidence": ["Q2"]}
    ]
    block = isolate_frame_block(raw, task(), factory)
    assert block.retained.pending == ("Q2", "Q3")


def test_new_trial_fingerprint_does_not_reuse_shared_task_namespace():
    assert frame_trial_fingerprint(task().fingerprint()) != task().fingerprint()
    assert frame_trial_fingerprint("one") != frame_trial_fingerprint("two")


@pytest.mark.parametrize("change", ["missing", "duplicate", "extra", "bad-boundary", "unused-n"])
def test_unisolatable_global_errors_require_whole_window(change):
    raw = bad()
    if change == "missing":
        raw["labels"].pop()
    elif change == "duplicate":
        raw["labels"].append(row("Q1"))
    elif change == "extra":
        raw["unexpected"] = True
    elif change == "bad-boundary":
        raw["breaks"] = ["unsent"]
    else:
        raw["new_characters"] = [
            {"ref": "N1", "name": "顾客", "description": "在店里", "evidence": ["E1"]}
        ]
    assert isolate_frame_block(raw, task(), factory) is None


def test_all_failed_and_already_valid_proposals_have_no_partial_block():
    assert (
        isolate_frame_block(
            {"labels": [row(q, evidence=["G1"]) for q in task().quote_ids]}, task(), factory
        )
        is None
    )
    assert (
        isolate_frame_block({"labels": [row(q) for q in task().quote_ids]}, task(), factory) is None
    )


@pytest.mark.parametrize("change", ["extra-target", "missing", "scene", "receiver"])
def test_partial_response_cannot_change_scope_or_weaken_validation(change):
    block = isolate_frame_block(bad(), task(), factory)
    response = {"labels": [row("Q2")]}
    if change == "extra-target":
        response["labels"].append(row("Q1"))
    elif change == "missing":
        response["labels"] = []
    elif change == "scene":
        response["breaks"] = ["B1"]
    else:
        response["labels"][0]["addressee"] = "C99"
    with pytest.raises(InvalidModelOutput):
        merged = block.combine(response, factory(block.retained.retry_task()))
        factory(task()).compile_payload(merged)


def test_actual_retry_request_has_pending_targets_feedback_and_known_failed_usage():
    backend = Recording(
        [
            {**bad(), "_usage": {"total_tokens": 9}},
            {"labels": [row("Q2")], "_usage": {"total_tokens": 12}},
        ]
    )
    t, before = task(), deepcopy(task())
    result = asyncio.run(
        run_frame_trial(lambda active: NonblankEvidenceViewAdapter(backend, active), t)
    )
    assert t == before
    assert result["ok"] and result["known_tokens"] == 21
    assert not result["first_pass_ok"] and result["unknown_usage_calls"] == 0
    assert [r["requested_targets"] for r in result["attempts"]] == [["Q1", "Q2", "Q3"], ["Q2"]]
    assert result["attempts"][1]["retained_targets"] == 2
    assert json.loads(backend.seen[1]["messages"][1]["content"])["targets"] == ["Q2"]
    assert "G1" in backend.seen[1]["messages"][-1]["content"]
    assert result["frame_payload"]["labels"] == [row(q) for q in t.quote_ids]
    assert result["attempts"][0]["raw"] == bad()


def test_failed_retry_never_accepts_kept_results_or_dispatches_extra_call():
    backend = Recording(
        [
            {**bad(), "_usage": {"total_tokens": 9}},
            {"labels": [row("Q2", evidence=["G1"])], "_usage": {"total_tokens": 12}},
        ]
    )
    result = asyncio.run(run_frame_trial(lambda t: NonblankEvidenceViewAdapter(backend, t), task()))
    assert not result["ok"] and result["output"] is None
    assert result["known_tokens"] == 21 and len(backend.seen) == 2


@pytest.mark.parametrize("valid", [True, False])
def test_unknown_usage_stops_without_accepting_even_valid_output(valid):
    raw = {"labels": [row(q) for q in task().quote_ids]} if valid else bad()
    backend = Recording([{**raw, "_usage": {"unknown": True}}])
    result = asyncio.run(run_frame_trial(lambda t: NonblankEvidenceViewAdapter(backend, t), task()))
    assert not result["ok"] and result["reconciliation_required"]
    assert result["unknown_usage_calls"] == 1 and len(backend.seen) == 1


def test_provider_nonformat_error_is_not_retried():
    backend = Recording(
        [ProviderError(ProviderErrorKind.AUTH, "limit", details={"usage": {"total_tokens": 0}})]
    )
    result = asyncio.run(run_frame_trial(lambda t: NonblankEvidenceViewAdapter(backend, t), task()))
    assert not result["ok"] and len(backend.seen) == 1


@pytest.mark.parametrize("error", [RuntimeError("provider"), asyncio.CancelledError()])
def test_unclassified_exception_and_cancellation_propagate(error):
    backend = Recording([error])
    with pytest.raises(type(error)):
        asyncio.run(run_frame_trial(lambda t: NonblankEvidenceViewAdapter(backend, t), task()))


def test_initial_retry_keeps_visible_horizon_and_candidates():
    t = replace(task(), reading_mode="initial", visible_horizon_cp=15)
    block = isolate_frame_block(bad(), t, factory)
    child = block.retained.retry_task()
    assert child.visible_horizon_cp == 15 and child.reading_mode == "initial"
    assert child.context == t.context and child.identity_facts == t.identity_facts


@pytest.mark.parametrize(
    "seeds,edges", [(("absent",), ()), (("Q2", "Q2"), ()), (("Q2",), (("Q1", "absent"),))]
)
def test_seeded_helper_refuses_undeclared_or_duplicate_targets(seeds, edges):
    with pytest.raises(ValueError):
        isolate_seeded_block({}, task(), failed_targets=seeds, dependency_edges=edges)


@pytest.mark.parametrize("budget", [-1, 6, True, "1"])
def test_invalid_retry_budget_fails_before_generation(budget):
    with pytest.raises(ValueError):
        asyncio.run(run_frame_trial(factory, task(), max_format_retries=budget))


def test_factory_must_use_exact_requested_task():
    with pytest.raises(ValueError, match="immutable task"):
        asyncio.run(run_frame_trial(lambda t: factory(replace(t, pov_ref="C1")), task()))


def test_journal_replay_restores_identical_result_without_new_requests(tmp_path):
    journal = CallJournal(
        tmp_path / "frame.trial.sqlite3",
        dependency_fingerprint="frame-test-1",
        max_calls=2,
        max_tokens=100000,
    )
    backend = Recording(
        [
            {**bad(), "_usage": {"total_tokens": 9}},
            {"labels": [row("Q2")], "_usage": {"total_tokens": 12}},
        ]
    )
    wrapped = JournaledAdapter(backend, journal)

    def make(t):
        return NonblankEvidenceViewAdapter(wrapped, t)

    first = asyncio.run(run_frame_trial(make, task()))
    stats = journal.stats()
    second = asyncio.run(run_frame_trial(make, task()))
    assert first["ok"] and second["ok"] and first["frame_payload"] == second["frame_payload"]
    assert first["known_tokens"] == second["known_tokens"] == 21
    assert len(backend.seen) == 2 and journal.stats() == stats
