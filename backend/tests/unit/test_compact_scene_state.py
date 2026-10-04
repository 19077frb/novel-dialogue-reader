from dataclasses import replace

import pytest

from ndr.domain.enums import Assignment
from ndr.evaluation.compact import Candidate, CompactTask, compile_output
from ndr.evaluation.scene_state import continue_scene, root_scene


def fixtures():
    prior = CompactTask(
        ("Q1", "Q2"),
        {"Q1": "q1", "Q2": "q2", "G1": "g1"},
        (
            {"ref": "Q1", "start_cp": 0, "end_cp": 4, "text": "「甲。」"},
            {"ref": "G1", "start_cp": 4, "end_cp": 7, "text": "周遥说"},
            {"ref": "Q2", "start_cp": 7, "end_cp": 11, "text": "「乙。」"},
        ),
        (Candidate("C1", "a", "林舟"), Candidate("C2", "b", "周遥")),
        {"G1": "Q2"},
    )
    current = CompactTask(
        ("Q1",),
        {"Q1": "q3", "E1": "g2"},
        (
            {"ref": "E1", "start_cp": 11, "end_cp": 14, "text": "林舟说"},
            {"ref": "Q1", "start_cp": 14, "end_cp": 18, "text": "「丙。」"},
        ),
        (Candidate("C1", "b", "周遥"), Candidate("C2", "a", "林舟")),
    )
    payload = {
        "labels": [
            {"q": "Q1", "kind": "speech", "character": "C1", "basis": "direct", "evidence": ["G1"]},
            {"q": "Q2", "kind": "speech", "character": "C2", "basis": "direct", "evidence": ["G1"]},
        ]
    }
    return prior, current, payload


def test_continuation_uses_stable_identity_not_candidate_order_or_name():
    prior, current, payload = fixtures()
    output = compile_output(payload, prior)
    continued = continue_scene(current, prior, output)
    assert continued.candidates[0].existing_ref == output.new_speakers[1].temp_ref
    assert continued.candidates[1].existing_ref == output.new_speakers[0].temp_ref
    assert continued.context == current.context and not continued.relay
    result = compile_output(
        {"labels": [{**payload["labels"][0], "character": "C2", "evidence": ["E1"]}]},
        continued,
    )
    assert result.labels[0].assignment is Assignment.EXISTING
    assert not result.new_speakers


def test_break_discards_previous_scene_slots_and_new_first_use_is_distinct():
    prior, current, payload = fixtures()
    output = compile_output({**payload, "breaks": ["G1"]}, prior)
    continued = continue_scene(current, prior, output)
    assert continued.scene_ref == output.labels[-1].scene_ref != prior.scene_ref
    assert continued.candidates[0].existing_ref == output.new_speakers[-1].temp_ref
    assert continued.candidates[1].existing_ref is None
    result = compile_output(
        {"labels": [{**payload["labels"][0], "character": "C2", "evidence": ["E1"]}]},
        continued,
    )
    assert result.labels[0].assignment is Assignment.NEW
    assert result.labels[0].speaker_ref != continued.candidates[0].existing_ref


def test_existing_slot_survives_non_speech_last_turn_without_inventing_a_speaker():
    prior, current, payload = fixtures()
    prior = replace(
        prior, candidates=(replace(prior.candidates[0], existing_ref="S1"), prior.candidates[1])
    )
    payload["labels"] = [{"q": q, "kind": "thought"} for q in prior.quote_ids]
    continued = continue_scene(current, prior, compile_output(payload, prior))
    assert continued.candidates[1].existing_ref == "S1"
    assert continued.candidates[0].existing_ref is None


def test_same_name_anonymous_new_character_is_not_a_stable_identity_link():
    prior, current, payload = fixtures()
    payload["labels"][0]["character"] = "N1"
    payload["new_characters"] = [
        {"ref": "N1", "name": "林舟", "description": "身份未知", "evidence": ["G1"]}
    ]
    continued = continue_scene(current, prior, compile_output(payload, prior))
    assert continued.candidates[1].existing_ref is None
    assert continued.candidates[0].existing_ref is not None


def test_continuation_preserves_current_initial_identity_facts_not_prior_names():
    prior, current, payload = fixtures()
    prior = replace(
        prior,
        candidates=(replace(prior.candidates[0], name="后来才知道的真名"), prior.candidates[1]),
    )
    current = replace(current, reading_mode="initial", visible_horizon_cp=18)
    continued = continue_scene(current, prior, compile_output(payload, prior))
    assert continued.candidates[1].name == "林舟"
    assert continued.visible_horizon_cp == 18
    assert continued.identity_facts == current.identity_facts


def test_incomplete_backward_or_conflicting_explicit_state_is_rejected():
    prior, current, payload = fixtures()
    output = compile_output(payload, prior)
    with pytest.raises(ValueError, match="complete"):
        continue_scene(current, prior, output.model_copy(update={"labels": output.labels[:1]}))
    with pytest.raises(ValueError, match="order"):
        continue_scene(
            prior,
            current,
            compile_output({"labels": [{**payload["labels"][0], "evidence": ["E1"]}]}, current),
        )
    current = replace(
        current,
        candidates=(replace(current.candidates[0], existing_ref="wrong"), current.candidates[1]),
    )
    with pytest.raises(ValueError, match="disagrees"):
        continue_scene(current, prior, output)


def test_root_and_compiler_namespaces_are_deterministic_and_isolated():
    prior, _, payload = fixtures()
    assert root_scene(prior, "a") == root_scene(prior, "a")
    assert root_scene(prior, "a").scene_ref != root_scene(prior, "b").scene_ref
    prior = replace(
        prior,
        scene_ref="compact_scene_1",
        candidates=(
            replace(prior.candidates[0], existing_ref="compact_new_1"),
            prior.candidates[1],
        ),
    )
    output = compile_output(payload, prior)
    assert output.new_speakers[0].temp_ref != "compact_new_1"
    cut = compile_output({**payload, "breaks": ["G1"]}, prior)
    assert cut.scene_updates[0].temp_ref != "compact_scene_1"
    assert cut.model_dump() == compile_output({**payload, "breaks": ["G1"]}, prior).model_dump()
