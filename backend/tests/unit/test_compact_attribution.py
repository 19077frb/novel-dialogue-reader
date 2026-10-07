from dataclasses import replace

import pytest
from pydantic import ValidationError

from ndr.domain.enums import Assignment
from ndr.evaluation.compact import Candidate, CompactTask, compile_output, identity_scores
from ndr.llm.errors import InvalidModelOutput
from ndr.scenes.acceptance import decide_acceptance


def task():
    return CompactTask(
        quote_ids=("Q1", "Q2"),
        references={"Q1": "quote1", "Q2": "quote2", "G1": "gap1"},
        context=(
            {"ref": "Q1", "end_cp": 10, "text": "你好"},
            {"ref": "G1", "end_cp": 15, "text": "林舟说"},
            {"ref": "Q2", "end_cp": 20, "text": "再见"},
        ),
        candidates=(Candidate("C1", "person1", "林舟"),),
        gap_next_quote={"G1": "Q2"},
    )


def speech(q="Q1", character="C1"):
    return {"q": q, "kind": "speech", "character": character, "basis": "direct", "evidence": ["G1"]}


def test_compiler_owns_first_use_and_preserves_identity():
    result = compile_output({"labels": [speech("Q2"), speech()]}, task())
    assert [label.assignment for label in result.labels] == [Assignment.NEW, Assignment.EXISTING]
    assert result.new_speakers[0].character_id == "person1"
    assert result.new_speakers[0].first_quote_id == "quote1"
    assert result.labels[0].evidence_refs == ["gap1"]


@pytest.mark.parametrize("requests", [["Q9"], ["G1"], ["quote1"], ["Q1", "Q1"]])
def test_context_requests_cannot_bypass_target_reference_contract(requests):
    with pytest.raises(InvalidModelOutput, match="Context requests"):
        compile_output({"labels": [speech(), speech("Q2")], "needs_context": requests}, task())


def test_boundary_recreates_local_group_not_global_identity():
    result = compile_output({"labels": [speech(), speech("Q2")], "breaks": ["G1"]}, task())
    assert len(result.new_speakers) == 2
    assert {p.character_id for p in result.new_speakers} == {"person1"}
    assert result.labels[0].scene_ref != result.labels[1].scene_ref
    assert all(label.assignment is Assignment.NEW for label in result.labels)


def test_existing_scene_slot_does_not_duplicate():
    candidate = replace(task().candidates[0], existing_ref="S1")
    result = compile_output(
        {"labels": [speech(), {"q": "Q2", "kind": "thought"}]},
        replace(task(), candidates=(candidate,)),
    )
    assert not result.new_speakers
    assert result.labels[0].speaker_ref == "S1"
    assert result.labels[1].speaker_ref is None


@pytest.mark.parametrize(
    "payload",
    [
        {"labels": [speech()]},
        {"labels": [speech(), speech()]},
        {"labels": [speech(), speech("Q3")]},
        {"labels": [speech(character="person1"), speech("Q2")]},
        {"labels": [{**speech(), "evidence": ["E99"]}, speech("Q2")]},
        {"labels": [speech(), speech("Q2")], "breaks": ["G99"]},
        {"labels": [speech(), speech("Q2")], "breaks": ["G1", "G1"]},
        {"labels": [speech(), speech("Q2")], "needs_context": ["G1"]},
        {"labels": [{**speech(), "character": None}, speech("Q2")]},
        {"labels": [{**speech(), "kind": "thought"}, speech("Q2")]},
    ],
)
def test_invalid_dependency_block_cannot_compile(payload):
    with pytest.raises((InvalidModelOutput, ValidationError)):
        compile_output(payload, task())


def test_new_anonymous_person_requires_evidence_and_does_not_merge_names():
    payload = {
        "labels": [speech(character="N1"), speech("Q2", "N2")],
        "new_characters": [
            {"ref": n, "name": "门卫", "description": "独立人物", "evidence": ["G1"]}
            for n in ["N1", "N2"]
        ],
    }
    result = compile_output(payload, task())
    assert len(result.new_speakers) == 2
    assert result.new_speakers[0].temp_ref != result.new_speakers[1].temp_ref
    assert all(p.character_id is None for p in result.new_speakers)


def test_self_citation_still_goes_to_review_not_automatic_acceptance():
    payload = {"labels": [{**speech(), "evidence": ["Q1"]}, speech("Q2")]}
    result = compile_output(payload, task())
    assert decide_acceptance(result.labels[0]).needs_review


def test_fingerprint_includes_identity_mapping_context_and_versioned_prompt():
    original = task()
    assert original.fingerprint() == task().fingerprint()
    assert (
        original.fingerprint()
        != replace(original, candidates=(Candidate("C1", "person2", "林舟"),)).fingerprint()
    )
    assert (
        original.fingerprint()
        != replace(original, reading_mode="initial", visible_horizon_cp=20).fingerprint()
    )


def test_initial_mode_rejects_future_text_and_identity():
    with pytest.raises(ValueError, match="horizon"):
        replace(task(), reading_mode="initial")
    with pytest.raises(ValueError, match="Future text"):
        replace(task(), reading_mode="initial", visible_horizon_cp=10)
    with pytest.raises(ValueError, match="Future identity"):
        replace(
            task(),
            reading_mode="initial",
            visible_horizon_cp=20,
            candidates=(replace(task().candidates[0], visible_from_cp=21),),
        )


def test_exact_identity_scores_reject_swap_and_count_missing_unknown():
    assert identity_scores({"q1": "A", "q2": "B"}, {"q1": "B", "q2": "A"})["correct"] == 0
    score = identity_scores(
        {"q1": "A", "q2": "B", "q3": "C"}, {"q1": "A", "q2": None, "extra": "D"}
    )
    assert (score["correct"], score["unknown"], score["missing"], score["extra"]) == (1, 1, 1, 1)


def test_input_cannot_alias_different_people_to_one_existing_group():
    with pytest.raises(ValueError, match="share"):
        replace(
            task(),
            candidates=(
                Candidate("C1", "a", "林舟", existing_ref="S1"),
                Candidate("C2", "b", "周遥", existing_ref="S1"),
            ),
        )


def test_explicit_scene_plan_is_checked_against_original_positions():
    context = tuple(
        {**row, "start_cp": pos} for row, pos in zip(task().context, [0, 10, 15], strict=True)
    )
    valid = replace(task(), context=context)
    with pytest.raises(ValueError, match="next target"):
        replace(valid, gap_next_quote={"G1": "Q1"})
    with pytest.raises(ValueError, match="next target"):
        replace(valid, gap_next_quote={"G1": None})
