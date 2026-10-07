from copy import deepcopy
from dataclasses import replace

import pytest

from ndr.evaluation.chain_risk import chain_risk_fingerprint, detect_chain_risks
from ndr.evaluation.compact import Candidate, CompactTask, compile_output
from ndr.evaluation.risk import detect_risks


def task(*, opener="周遥，进来吧。", anchor="林舟说，门已经开了。"):
    texts = {"G1": anchor, "Q1": opener, "Q2": "好的。", "Q3": "知道了。", "Q4": "再见。"}
    position = 0
    rows = []
    for ref, text in texts.items():
        rows.append({"ref": ref, "text": text, "start_cp": position, "end_cp": position + len(text)})
        position += len(text)
    return CompactTask(
        ("Q1", "Q2", "Q3", "Q4"),
        {ref: ref.lower() for ref in texts},
        tuple(rows),
        (Candidate("C1", "a", "林舟"), Candidate("C2", "b", "周遥", aliases=("周同学",))),
    )


def speech(q, c="C1", *, evidence=("G1",), basis="direct"):
    return {"q": q, "kind": "speech", "character": c, "basis": basis, "evidence": list(evidence)}


def payload(**overrides):
    return {"labels": [overrides.get(q, speech(q)) for q in ("Q1", "Q2", "Q3", "Q4")]}


def risks(t, p):
    return {r.quote_id: set(r.reasons) for r in detect_chain_risks(t, compile_output(p, t))}


@pytest.mark.parametrize("opener", ["周遥，进来吧。", "「周遥，进来吧。」", "周同学!进来吧。"])
def test_cited_addressee_conflict_is_soft_and_does_not_change_prediction(opener):
    t = task(opener=opener)
    output = compile_output(payload(Q2=speech("Q2", evidence=("Q1",), basis="response_link")), t)
    before = deepcopy(output.model_dump())
    selected = {r.quote_id: r.reasons for r in detect_chain_risks(t, output)}
    assert "response_addressee_conflict" in selected["Q2"]
    assert output.model_dump() == before


@pytest.mark.parametrize("opener", ["我叫周遥，进来吧。", "他说『周遥，进来吧。』", "周遥的门开了。"])
def test_self_introduction_embedded_quote_or_name_mention_is_not_vocative(opener):
    t = task(opener=opener)
    r = risks(t, payload(Q2=speech("Q2", evidence=("Q1",), basis="response_link")))
    assert "response_addressee_conflict" not in r.get("Q2", set())


def test_same_named_multiple_native_people_stay_ambiguous():
    t = task()
    t = replace(t, candidates=(*t.candidates, Candidate("C3", "c", "周遥")))
    r = risks(t, payload(Q2=speech("Q2", evidence=("Q1",), basis="response_link")))
    assert "response_addressee_conflict" not in r.get("Q2", set())


def test_aliases_of_same_person_do_not_invent_multiple_identities():
    t = task()
    t = replace(t, candidates=(t.candidates[0], replace(t.candidates[1], aliases=("周遥", "周同学"))))
    r = risks(t, payload(Q2=speech("Q2", evidence=("Q1",), basis="response_link")))
    assert "response_addressee_conflict" in r["Q2"]


def test_matching_addressee_does_not_certify_or_rewrite_answer():
    t = task()
    r = risks(t, payload(Q2=speech("Q2", "C2", evidence=("Q1",), basis="response_link")))
    assert "response_addressee_conflict" not in r.get("Q2", set())


def test_hard_evidence_failure_propagates_across_multiple_quote_dependencies():
    t = task(opener="请进。", anchor="林舟看向门口。")
    p = payload(
        Q2=speech("Q2", evidence=("Q1",), basis="response_link"),
        Q3=speech("Q3", evidence=("Q2",), basis="coreference"),
    )
    r = risks(t, p)
    assert "direct_relation_unverified" in r["Q1"]
    assert "uncertain_evidence_chain" in r["Q2"]
    assert "uncertain_evidence_chain" in r["Q3"]


def test_audit_and_window_edge_alone_do_not_taint_good_dependency_chain():
    t = task(opener="请进。")
    p = payload(
        Q2=speech("Q2", evidence=("Q1",), basis="response_link"),
        Q3=speech("Q3", evidence=("Q2",), basis="coreference"),
    )
    r = risks(t, p)
    assert {"accepted_sample_audit", "window_edge"} <= r["Q1"]
    assert "uncertain_evidence_chain" not in r.get("Q2", set())
    assert "uncertain_evidence_chain" not in r.get("Q3", set())


def test_self_and_cyclic_evidence_are_bounded_and_deterministic():
    t = task(opener="请进。")
    p = payload(
        Q1=speech("Q1", evidence=("Q1",)),
        Q2=speech("Q2", evidence=("Q3",), basis="style_only"),
        Q3=speech("Q3", evidence=("Q2",), basis="response_link"),
    )
    output = compile_output(p, t)
    r = {r.quote_id: r.reasons for r in detect_chain_risks(t, output)}
    assert "no_external_evidence" in r["Q1"]
    assert "uncertain_evidence_chain" in r["Q3"]
    assert detect_chain_risks(t, output) == detect_chain_risks(t, output)


def test_anonymous_person_is_not_forced_into_named_addressee():
    t = task()
    p = payload(Q2=speech("Q2", "N1", evidence=("Q1",), basis="response_link"))
    p["new_characters"] = [{"ref": "N1", "name": "门卫", "description": "匿名", "evidence": ["G1"]}]
    r = risks(t, p)
    assert "new_identity" in r["Q2"]
    assert "response_addressee_conflict" not in r["Q2"]


def test_original_risks_are_retained_and_input_unchanged():
    t = task(opener="请进。")
    output = compile_output(payload(), t)
    before = deepcopy(t), deepcopy(output.model_dump())
    added = {r.quote_id: set(r.reasons) for r in detect_chain_risks(t, output)}
    for original in detect_risks(t, output):
        assert set(original.reasons) <= added[original.quote_id]
    assert t == before[0] and output.model_dump() == before[1]


def test_unsent_and_duplicate_labels_fail_instead_of_building_false_graph():
    t = task()
    output = compile_output(payload(), t)
    output.labels.append(deepcopy(output.labels[0]))
    with pytest.raises(ValueError, match="unique"):
        detect_chain_risks(t, output)
    output.labels.pop()
    output.labels[0].quote_id = "not-sent"
    with pytest.raises(ValueError, match="unsent"):
        detect_chain_risks(t, output)


def test_initial_task_does_not_require_any_outside_text_or_future_identity():
    t = replace(task(opener="请进。"), reading_mode="initial", visible_horizon_cp=50)
    output = compile_output(payload(), t)
    assert detect_chain_risks(t, output) == detect_chain_risks(t, output)


def test_chain_strategy_fingerprint_is_separate_and_source_sensitive():
    assert chain_risk_fingerprint("old") == chain_risk_fingerprint("old")
    assert chain_risk_fingerprint("old") != chain_risk_fingerprint("new")


def test_scene_existing_slot_is_resolved_without_new_character_declaration():
    t = task()
    t = replace(t, candidates=tuple(replace(p, existing_ref=f"slot-{p.ref}") for p in t.candidates))
    p = payload(Q2=speech("Q2", evidence=("Q1",), basis="response_link"))
    output = compile_output(p, t)
    assert not output.new_speakers
    r = {r.quote_id: r.reasons for r in detect_chain_risks(t, output)}
    assert "response_addressee_conflict" in r["Q2"]


def test_missing_source_label_is_a_seed_even_when_reference_is_sent():
    t = task(opener="请进。")
    output = compile_output(payload(Q2=speech("Q2", evidence=("Q1",), basis="response_link")), t)
    output.labels = output.labels[1:]
    r = {r.quote_id: r.reasons for r in detect_chain_risks(t, output)}
    assert "missing_label" in r["Q1"]
    assert "uncertain_evidence_chain" in r["Q2"]
