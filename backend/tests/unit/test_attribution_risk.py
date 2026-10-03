from dataclasses import replace

import pytest

from ndr.evaluation.compact import Candidate, CompactTask, compile_output
from ndr.evaluation.risk import QuoteRisk, detect_risks, review_blocks, risk_scores


def task():
    return CompactTask(
        ("Q1", "Q2", "Q3"),
        {"Q1": "q1", "Q2": "q2", "Q3": "q3", "G1": "g1"},
        (
            {"ref": "G1", "text": "林舟看向门口。", "end_cp": 10},
            {"ref": "Q1", "text": "你好。", "end_cp": 15},
            {"ref": "Q2", "text": "林舟，你看够了吗？", "end_cp": 25},
            {"ref": "Q3", "text": "再见。", "end_cp": 30},
        ),
        (Candidate("C1", "a", "林舟"), Candidate("C2", "b", "周遥")),
    )


def speech(q, c="C1", **kwargs):
    return {
        "q": q,
        "kind": "speech",
        "character": c,
        "basis": "direct",
        "evidence": ["G1"],
        **kwargs,
    }


def reasons(t, payload):
    return {r.quote_id: set(r.reasons) for r in detect_risks(t, compile_output(payload, t))}


def test_direct_action_self_citation_and_address_are_reviewed_even_if_structurally_valid():
    t = task()
    r = reasons(t, {"labels": [speech("Q1"), speech("Q2"), speech("Q3", evidence=["Q3"])]})
    assert "direct_relation_unverified" in r["Q1"]
    assert "addressed_person_conflict" in r["Q2"]
    assert "no_external_evidence" in r["Q3"]
    assert "window_edge" in r["Q1"] and "window_edge" in r["Q3"]


def test_self_introduction_is_an_exception_to_addressee_conflict():
    t = replace(
        task(),
        context=tuple(
            {**row, "text": "我叫林舟。"} if row["ref"] == "Q2" else row for row in task().context
        ),
    )
    r = reasons(t, {"labels": [speech("Q1"), speech("Q2"), speech("Q3")]})
    assert "addressed_person_conflict" not in r.get("Q2", set())


def test_unknown_and_type_errors_are_reviewed_and_shared_evidence_is_not_trusted():
    t = task()
    payload = {"labels": [speech("Q1"), speech("Q2", "C2"), {"q": "Q3", "kind": "thought"}]}
    r = reasons(t, payload)
    assert "shared_evidence_conflict" in r["Q1"]
    assert "speech_type_ambiguity" in r["Q3"]
    payload["labels"][1] = speech("Q2", None, basis="insufficient", evidence=[])
    r = reasons(t, payload)
    assert {"unknown", "weak_basis", "no_external_evidence"} <= r["Q2"]


def test_dependency_on_unknown_turn_and_unestablished_identity_are_risks():
    t = task()
    r = reasons(
        t,
        {
            "labels": [
                speech("Q1", None, basis="insufficient", evidence=[]),
                speech("Q2", basis="response_link", evidence=["Q1"]),
                speech("Q3", "N1"),
            ],
            "new_characters": [
                {"ref": "N1", "name": "门卫", "description": "新人物", "evidence": ["G1"]}
            ],
        },
    )
    assert "uncertain_evidence_chain" in r["Q2"]
    assert "new_identity" in r["Q3"]


def test_review_blocks_merge_neighbours_and_bound_dependency_blocks():
    t = task()
    assert review_blocks(t, [QuoteRisk("Q1", ("test",)), QuoteRisk("Q3", ("test",))]) == (
        ("Q1", "Q2", "Q3"),
    )
    assert review_blocks(t, [QuoteRisk("Q2", ("test",))], max_targets=2) == (("Q1", "Q2"), ("Q3",))
    assert review_blocks(t, [], max_targets=1) == ()
    with pytest.raises(ValueError, match="unsent"):
        review_blocks(t, [QuoteRisk("Q99", ("test",))])


def test_error_recall_and_unnecessary_review_are_gold_based_not_confidence_based():
    score = risk_scores({"bad1", "bad2"}, {"ok1", "ok2"}, {"bad1", "ok1", "unknown"})
    assert score["error_recall"] == score["unnecessary_review_rate"] == 0.5
    assert score["selected_count"] == 3
    assert risk_scores(set(), set(), {"unknown"})["error_recall"] is None
    with pytest.raises(ValueError, match="overlap"):
        risk_scores({"same"}, {"same"}, set())
