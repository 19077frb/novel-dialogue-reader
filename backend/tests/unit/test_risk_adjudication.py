import asyncio
import json
from dataclasses import replace

import pytest

from ndr.evaluation.adjudication import adjudicate_window
from ndr.evaluation.compact import Candidate, CompactTask
from ndr.evaluation.journal import CallJournal, JournaledAdapter
from ndr.evaluation.review import Decision
from ndr.llm.schemas import LlmOutput

TEXT = "林舟说：「甲。」\n周遥说：「乙。」\n将来江雨说：「丙。」"


def task():
    return CompactTask(
        ("Q1", "Q2"),
        {"Q1": "q1", "Q2": "q2", "E1": "e1"},
        (
            {"ref": "Q1", "start_cp": 4, "end_cp": 8, "text": TEXT[4:8]},
            {"ref": "Q2", "start_cp": 13, "end_cp": 17, "text": TEXT[13:17]},
            {"ref": "E1", "kind": "overlap", "start_cp": 0, "end_cp": 4, "text": TEXT[:4]},
        ),
        (Candidate("C1", "a", "林舟"), Candidate("C2", "b", "周遥"), Candidate("C3", "c", "江雨")),
    )


def d(person="a", evidence=("e1",), basis="direct"):
    return Decision("speech", person, basis, evidence)


class Adapter:
    def __init__(self, person="C2", *, bad=False, unknown=False, non_speech=False):
        self.person, self.bad, self.unknown, self.non_speech = person, bad, unknown, non_speech
        self.seen = []

    async def generate_labels(self, request):
        data = json.loads(request["messages"][1]["content"])
        self.seen.append(data)
        evidence = next(row["ref"] for row in data["context"] if row["ref"] not in data["targets"])
        labels = [
            {"q": q, "kind": "quotation"}
            if self.non_speech
            else {
                "q": q,
                "kind": "speech",
                "character": self.person,
                "basis": "direct",
                "evidence": [evidence],
            }
            for q in data["targets"]
        ]
        return {
            "labels": [] if self.bad else labels,
            "_usage": {"total_tokens": None if self.unknown else 20, "unknown": self.unknown},
        }


def run(adapter, **kwargs):
    options = {
        "original": {"q1": d(), "q2": d()},
        "reviewed": {"q1": d("b"), "q2": d()},
        "model_scope": "mock:enabled:low",
        "max_format_retries": 0,
    }
    return asyncio.run(
        adjudicate_window(adapter, kwargs.pop("task", task()), text=TEXT, **(options | kwargs))
    )


def ids(result):
    output = LlmOutput.model_validate(result["output"])
    speakers = {s.temp_ref: s.character_id for s in output.new_speakers}
    return [speakers.get(label.speaker_ref) for label in output.labels]


def test_third_corroborates_conflict_but_cannot_change_context_neighbour():
    adapter = Adapter()
    result = run(adapter)
    assert ids(result) == ["b", "a"]
    assert result["eligible_targets"] == ["q1"]
    assert result["resolution_reasons"]["q1"] == "evidence_corroborated"
    assert result["known_tokens"] == 20
    assert "previous_turn_candidates" not in adapter.seen[0]


def test_unmatched_third_identity_is_not_accepted_as_stronger_truth():
    result = run(Adapter("C3"))
    assert ids(result) == [None, "a"]
    assert result["resolution_reasons"]["q1"] == "unresolved_conflict"


def test_anonymous_names_do_not_establish_identity_across_three_sources():
    class AnonymousAdapter(Adapter):
        async def generate_labels(self, request):
            result = await super().generate_labels(request)
            data = self.seen[-1]
            evidence = next(r["ref"] for r in data["context"] if r["ref"] not in data["targets"])
            result["new_characters"] = [
                {
                    "ref": "N1",
                    "name": "女同学",
                    "description": "路过的女同学",
                    "evidence": [evidence],
                }
            ]
            return result

    base = Decision("speech", None, "direct", ("e1",), "base:n")
    second = replace(base, anonymous_ref="review:n")
    declarations = {
        ref: {"name": "女同学", "description": "路过的女同学", "evidence": ["e1"]}
        for ref in ("base:n", "review:n")
    }
    result = run(
        AnonymousAdapter("N1"),
        original={"q1": base, "q2": d()},
        reviewed={"q1": second, "q2": d()},
        anonymous_characters=declarations,
    )
    assert ids(result) == [None, "a"]
    assert result["resolution_reasons"]["q1"] == "unresolved_conflict"


def test_same_identity_can_strengthen_weak_evidence_only():
    weak = {"q1": d(evidence=("q1",), basis="style_only"), "q2": d()}
    result = run(Adapter("C1"), original=weak, reviewed=weak)
    assert ids(result) == ["a", "a"]
    assert result["resolution_reasons"]["q1"] == "evidence_strengthened"


def test_non_speech_conflict_requires_matching_candidate():
    reviewed = {"q1": Decision("quotation", None, None, ()), "q2": d()}
    result = run(Adapter(non_speech=True), reviewed=reviewed)
    assert result["output"]["labels"][0]["kind"] == "quotation"


def test_locks_and_no_risks_make_no_paid_calls():
    adapter = Adapter()
    result = run(adapter, locked={"q1": d()})
    assert not adapter.seen and ids(result) == ["a", "a"]
    assert result["known_tokens"] == 0


def test_known_failure_keeps_valid_unresolved_result_and_counts_cost():
    result = run(Adapter(bad=True), max_format_retries=1)
    assert result["ok"] and ids(result) == [None, "a"]
    assert result["known_tokens"] == 40 and result["adjudication_failures"] == 1


def test_unknown_usage_stops_without_claiming_completed_output():
    adapter = Adapter(unknown=True)
    result = run(adapter, max_format_retries=1)
    assert result["reconciliation_required"] and not result["ok"]
    assert result["output"] is None and result["retained_output"]
    assert len(adapter.seen) == 1 and result["unknown_usage_calls"] == 1


def test_initial_horizon_and_explicit_context_request_are_preserved():
    initial = replace(
        task(), reading_mode="initial", visible_horizon_cp=17, candidates=task().candidates[:2]
    )
    adapter = Adapter()
    result = run(adapter, task=initial, needs_context=("q1",))
    assert result["output"]["needs_context"] == ["q1"]
    assert "江雨" not in json.dumps(adapter.seen, ensure_ascii=False)
    assert all(row["end_cp"] <= 17 for row in result["compiled_task"]["context"])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"needs_context": ("unsent",)},
        {"model_scope": ""},
        {"original": {"q1": d(evidence=("unknown_proof",)), "q2": d()}},
        {"task": replace(task(), context=tuple({**r, "text": "changed"} for r in task().context))},
    ],
)
def test_invalid_snapshot_fails_before_paid_request(kwargs):
    adapter = Adapter()
    with pytest.raises((ValueError, KeyError)):
        run(adapter, **kwargs)
    assert not adapter.seen


def test_recovery_reuses_recorded_response_without_another_paid_call(tmp_path):
    ledger = CallJournal(
        tmp_path / "adjudication.trial.sqlite3",
        dependency_fingerprint="explicit-scope-and-source-v1",
        max_calls=4,
        max_tokens=100000,
    )
    first_adapter = Adapter()
    first = run(JournaledAdapter(first_adapter, ledger))
    second_adapter = Adapter("C3")
    restored = run(JournaledAdapter(second_adapter, ledger))
    assert first["output"] == restored["output"]
    assert not second_adapter.seen and ledger.stats()["calls"] == 1
    assert ledger.stats()["known_tokens"] == 20
