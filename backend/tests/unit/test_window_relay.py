import json
from dataclasses import replace

import pytest

from ndr.evaluation.compact import Candidate, CompactTask, compile_output
from ndr.evaluation.relay import attach_dependency_relay, attach_relay
from ndr.evaluation.scene_plan import comparison_plan, validate_scene_plan


def fixtures():
    prior = CompactTask(
        ("Q1",),
        {"Q1": "old_quote", "E1": "old_proof"},
        (
            {"ref": "E1", "text": "林舟说", "start_cp": 0, "end_cp": 3},
            {"ref": "Q1", "text": "「你好。」", "start_cp": 3, "end_cp": 8},
        ),
        (Candidate("C1", "a", "林舟"),),
    )
    current = CompactTask(
        ("Q1",),
        {"Q1": "new_quote"},
        ({"ref": "Q1", "text": "「再见。」", "start_cp": 10, "end_cp": 15},),
        (Candidate("C1", "b", "周遥"), Candidate("C2", "a", "林舟")),
    )
    payload = {
        "labels": [
            {"q": "Q1", "kind": "speech", "character": "C1", "basis": "direct", "evidence": ["E1"]}
        ]
    }
    return prior, current, payload


def test_multiple_parents_share_limits_and_prefer_recent_original_turns():
    prior, current, payload = fixtures()
    middle = replace(
        prior,
        references={"Q1": "middle_quote", "E1": "middle_proof"},
        context=tuple(
            {**row, "start_cp": row["start_cp"] + 8, "end_cp": row["end_cp"] + 8}
            for row in prior.context
        ),
    )
    current = replace(
        current,
        context=tuple(
            {**row, "start_cp": row["start_cp"] + 10, "end_cp": row["end_cp"] + 10}
            for row in current.context
        ),
    )
    predecessors = (
        (prior, compile_output(payload, prior)),
        (middle, compile_output(payload, middle)),
    )
    result = attach_dependency_relay(current, predecessors)
    assert len(result.relay) == 2
    assert [result.references[t["ref"]] for t in result.relay] == ["old_quote", "middle_quote"]
    assert result.candidates == current.candidates and result.scene_ref == current.scene_ref
    limited = attach_dependency_relay(current, predecessors, max_turns=1, max_added_chars=5)
    assert len(limited.relay) == 1 and limited.references[limited.relay[0]["ref"]] == "middle_quote"
    assert (
        sum(len(row["text"]) for row in limited.context if row["ref"] not in current.references)
        <= 5
    )
    empty = attach_dependency_relay(current, predecessors, max_turns=0)
    assert empty == current
    with pytest.raises(ValueError, match="repeat target"):
        attach_dependency_relay(current, (predecessors[0], predecessors[0]))
    with pytest.raises(ValueError, match="complete"):
        attach_dependency_relay(
            current,
            ((prior, compile_output(payload, prior).model_copy(update={"labels": []})),),
            max_added_chars=0,
        )


def test_relay_remaps_identity_and_carries_original_text_not_confirmed_truth():
    prior, current, payload = fixtures()
    result = attach_relay(current, prior, compile_output(payload, prior))
    assert result.relay[0]["candidate"] == "C2"
    assert result.relay[0]["status"] == "unconfirmed"
    assert result.relay[0]["support"] == "external_evidence_candidate"
    assert len(result.context) == 3
    assert result.quote_ids == current.quote_ids
    assert result.fingerprint() != current.fingerprint()
    data = json.loads(result.messages()[1]["content"])
    assert data["previous_turn_candidates"][0]["source"] == "previous_window_model_candidate"
    assert {r["text"] for r in result.context} == {"林舟说", "「你好。」", "「再见。」"}


def test_anonymous_and_unmapped_people_are_not_promoted_by_same_name():
    prior, current, payload = fixtures()
    current = replace(current, candidates=(Candidate("C1", "b", "林舟"),))
    result = attach_relay(current, prior, compile_output(payload, prior))
    assert result.relay[0]["candidate"] is None
    assert result.relay[0]["identity_unmapped"]
    payload["labels"][0]["character"] = "N1"
    payload["new_characters"] = [
        {"ref": "N1", "name": "林舟", "description": "身份未知", "evidence": ["E1"]}
    ]
    anonymous = attach_relay(current, prior, compile_output(payload, prior))
    assert anonymous.relay[0]["candidate"] is None
    assert anonymous.relay[0]["identity_unmapped"]


def test_relay_limits_omit_whole_text_and_do_not_change_future_horizon():
    prior, current, payload = fixtures()
    current = replace(current, reading_mode="initial", visible_horizon_cp=15)
    result = attach_relay(current, prior, compile_output(payload, prior), max_added_chars=5)
    assert len(result.context) == 2
    assert result.relay[0]["evidence_omitted"] == 1
    assert result.visible_horizon_cp == 15
    assert result.relay[0]["support"] == "unresolved_or_weak"
    assert attach_relay(current, prior, compile_output(payload, prior), max_turns=0) == current
    assert not attach_relay(current, prior, compile_output(payload, prior), max_added_chars=4).relay


def test_future_proof_is_not_relayed_even_when_already_in_current_context():
    prior, current, payload = fixtures()
    prior = replace(
        prior,
        references={**prior.references, "E2": "new_quote"},
        context=(*prior.context, {**current.context[0], "ref": "E2"}),
    )
    payload["labels"][0]["evidence"] = ["E2"]
    result = attach_relay(current, prior, compile_output(payload, prior))
    assert result.relay[0]["evidence"] == []
    assert result.relay[0]["evidence_omitted"] == 1


def test_bad_original_coordinates_snapshot_or_previous_block_fail_closed():
    prior, current, payload = fixtures()
    output = compile_output(payload, prior)
    with pytest.raises(ValueError, match="coordinates"):
        attach_relay(
            current,
            replace(prior, context=({**prior.context[0], "start_cp": 1}, prior.context[1])),
            output,
        )
    conflicting = replace(
        current,
        references={**current.references, "E1": "old_proof"},
        context=(*current.context, {**prior.context[0], "text": "周遥说"}),
    )
    with pytest.raises(ValueError, match="snapshots disagree"):
        attach_relay(conflicting, prior, output)
    with pytest.raises(ValueError, match="dependency order"):
        attach_relay(prior, current, output)
    output.labels = []
    with pytest.raises(ValueError, match="complete"):
        attach_relay(current, prior, output)


def test_structurally_invalid_previous_identity_cannot_enter_relay():
    prior, current, payload = fixtures()
    output = compile_output(payload, prior)
    output.new_speakers[0].character_id = "not_in_previous_snapshot"
    with pytest.raises(ValueError, match="strict internal"):
        attach_relay(current, prior, output)


@pytest.mark.parametrize(
    "kwargs", [{"max_turns": 9}, {"max_evidence_per_turn": 4}, {"max_added_chars": -1}]
)
def test_invalid_relay_limits_are_rejected(kwargs):
    prior, current, payload = fixtures()
    with pytest.raises(ValueError, match="limits"):
        attach_relay(current, prior, compile_output(payload, prior), **kwargs)


def test_scene_plan_keeps_each_chain_sequential_and_explicit_cross_scene_dependencies():
    plan = validate_scene_plan(
        {
            "scenes": [
                {"scene": "one", "windows": ["W1", "W2"], "depends_on": []},
                {"scene": "two", "windows": ["W3"], "depends_on": []},
                {"scene": "three", "windows": ["W4"], "depends_on": ["one", "two"]},
            ]
        },
        ("W1", "W2", "W3", "W4"),
    )
    assert [p.depends_on for p in plan] == [(), ("W1",), (), ("W2", "W3")]
    assert comparison_plan(("W1", "W2"), continuous=True)[1].depends_on == ("W1",)
    assert comparison_plan(("W1", "W2"), continuous=False)[1].depends_on == ()


@pytest.mark.parametrize(
    "scenes",
    [
        [{"scene": "a", "windows": ["W1"], "depends_on": []}],
        [{"scene": "a", "windows": ["W2", "W1"], "depends_on": []}],
        [{"scene": "a", "windows": ["W1", "W1"], "depends_on": []}],
        [{"scene": "a", "windows": ["W1", "W2"], "depends_on": ["future"]}],
        [
            {"scene": "a", "windows": ["W1"], "depends_on": []},
            {"scene": "a", "windows": ["W2"], "depends_on": []},
        ],
    ],
)
def test_invalid_plans_never_guess_missing_or_out_of_order_windows(scenes):
    with pytest.raises(ValueError):
        validate_scene_plan({"scenes": scenes}, ("W1", "W2"))
