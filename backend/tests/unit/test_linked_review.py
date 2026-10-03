import asyncio
import json

import pytest

from ndr.domain.enums import Assignment
from ndr.evaluation.compact import Candidate, CompactTask, compile_output
from ndr.evaluation.review import (
    Decision,
    candidate_review_task,
    compile_decisions,
    decisions,
    reconcile,
    run_linked_review,
)


def task():
    return CompactTask(
        ("Q1", "Q2"),
        {"Q1": "q1", "Q2": "q2", "E1": "e1"},
        (
            {"ref": "Q1", "text": "你好", "end_cp": 2},
            {"ref": "Q2", "text": "你好", "end_cp": 4},
            {"ref": "E1", "text": "林舟说", "end_cp": 8},
        ),
        (Candidate("C1", "a", "林舟"), Candidate("C2", "b", "周遥")),
    )


def payload():
    return {
        "labels": [
            {"q": q, "kind": "speech", "character": "C1", "basis": "direct", "evidence": ["E1"]}
            for q in task().quote_ids
        ]
    }


def d(person="a", kind="speech", evidence=("e1",), basis="direct"):
    return Decision(kind, person, basis, evidence)


def test_disagreement_is_not_automatically_replaced_by_second_answer():
    result, why = reconcile({"q1": d()}, {"q1": d("b")})
    assert result["q1"].character_id is None and why["q1"] == "unresolved_conflict"
    assert result["q1"].basis == "insufficient"


@pytest.mark.parametrize("weak", [d(evidence=(), basis="style_only"), d(evidence=("q1",))])
def test_agreement_does_not_erase_existing_external_evidence_with_weaker_review(weak):
    base = d()
    result, why = reconcile({"q1": base}, {"q1": weak})
    assert result["q1"] == base
    assert why["q1"] == "agreement_kept_supported_base"


def test_agreement_can_use_supported_review_but_never_invents_support_for_two_weak_answers():
    weak = d(evidence=(), basis="style_only")
    strong = d(evidence=("new_original_proof",))
    result, why = reconcile({"q1": weak}, {"q1": strong})
    assert result["q1"] == strong and why["q1"] == "agreement"
    result, _ = reconcile({"q1": weak}, {"q1": weak})
    assert not result["q1"].supported("q1")


def test_evidence_corroboration_can_fix_or_retain_but_self_citation_is_not_enough():
    result, why = reconcile({"q1": d()}, {"q1": d("b")}, adjudicated={"q1": d("b")})
    assert result["q1"].character_id == "b" and why["q1"] == "evidence_corroborated"
    result, _ = reconcile({"q1": d()}, {"q1": d("b")}, adjudicated={"q1": d("a")})
    assert result["q1"].character_id == "a"
    result, _ = reconcile({"q1": d()}, {"q1": d("b")}, adjudicated={"q1": d("b", evidence=("q1",))})
    assert result["q1"].character_id is None


def test_type_conflict_remains_unknown_unless_third_judgment_corroborates():
    thought = d(None, "thought", (), None)
    result, _ = reconcile({"q1": d()}, {"q1": thought})
    assert result["q1"].kind == "unknown"
    result, _ = reconcile({"q1": d()}, {"q1": thought}, adjudicated={"q1": thought})
    assert result["q1"].kind == "thought"


def test_locks_always_win_and_missing_review_does_not_erase_valid_output():
    result, why = reconcile({"q1": d(), "q2": d()}, {"q1": d("b")}, locked={"q1": d("a")})
    assert result["q1"].character_id == result["q2"].character_id == "a"
    assert why == {"q1": "user_locked", "q2": "review_unavailable"}
    with pytest.raises(ValueError, match="outside"):
        reconcile({"q1": d()}, {"q3": d()})


def test_candidate_arm_is_explicit_and_independent_arm_contains_no_previous_answer():
    class Adapter:
        requests = []

        async def generate_labels(self, request):
            self.requests.append(request)
            return {**payload(), "_usage": {"total_tokens": 20}}

    t = task()
    original = compile_output(payload(), t)
    adapter = Adapter()
    independent = asyncio.run(run_linked_review(adapter, t, t, original))
    candidate = asyncio.run(run_linked_review(adapter, t, t, original, mode="candidate"))
    first, second = [json.loads(r["messages"][1]["content"]) for r in adapter.requests]
    assert "previous_turn_candidates" not in first
    assert second["previous_turn_candidates"][0]["candidate"] == "C1"
    assert independent["review_mode"] == "independent" and candidate["review_mode"] == "candidate"
    assert candidate_review_task(t, t, original).fingerprint() != t.fingerprint()


def test_identity_mapping_handles_same_person_across_scene_slots_and_anonymous_is_call_scoped():
    t = task()
    output = compile_output(payload(), t)
    assert {r.character_id for r in decisions(t, output, call_ref="one").values()} == {"a"}
    raw = payload()
    for label in raw["labels"]:
        label["character"] = "N1"
    raw["new_characters"] = [
        {"ref": "N1", "name": "门卫", "description": "门口的人", "evidence": ["E1"]}
    ]
    output = compile_output(raw, t)
    first = decisions(t, output, call_ref="one")
    second = decisions(t, output, call_ref="two")
    result, _ = reconcile(first, second)
    assert all(r.character_id is None and r.anonymous_ref is None for r in result.values())


def test_atomic_compilation_rebuilds_first_use_after_review_changes_identity():
    t = task()
    compiled = compile_decisions(t, {"q1": d("b"), "q2": d("b")})
    assert len(compiled.new_speakers) == 1 and compiled.new_speakers[0].character_id == "b"
    assert [r.assignment for r in compiled.labels] == [Assignment.NEW, Assignment.EXISTING]
    with pytest.raises(ValueError, match="full dependency"):
        compile_decisions(t, {"q1": d()})
    with pytest.raises(ValueError, match="snapshot"):
        compile_decisions(t, {"q1": d("missing"), "q2": d()})
    with pytest.raises(KeyError):
        compile_decisions(t, {"q1": d(evidence=("unsent",)), "q2": d()})


def test_anonymous_declaration_requires_original_evidence_and_is_not_name_merged():
    t = task()
    resolved = {
        "q1": Decision("speech", None, "direct", ("e1",), "one:new1"),
        "q2": Decision("speech", None, "direct", ("e1",), "one:new2"),
    }
    with pytest.raises(ValueError, match="Missing source"):
        compile_decisions(t, resolved)
    proof = {
        key: {"name": "门卫", "description": "门口的人", "evidence": ["e1"]}
        for key in ["one:new1", "one:new2"]
    }
    compiled = compile_decisions(t, resolved, anonymous_characters=proof)
    assert len(compiled.new_speakers) == 2
