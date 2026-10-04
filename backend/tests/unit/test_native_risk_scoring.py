import json
from dataclasses import asdict, replace

import pytest

from ndr.evaluation.compact import Candidate, CompactTask, compile_output
from ndr.evaluation.risk import detect_risks
from ndr.evaluation.scoring import SCORING_VERSION, score_attribution_risks


def task():
    return CompactTask(
        ("Q1", "Q2", "Q3"),
        {"Q1": "q1", "Q2": "q2", "Q3": "q3", "G1": "g1", "G2": "g2"},
        (
            {"ref": "G1", "text": "林舟说道。", "start_cp": 0, "end_cp": 5},
            {"ref": "Q1", "text": "「你好。」", "start_cp": 5, "end_cp": 10},
            {"ref": "G2", "text": "周遥答道。", "start_cp": 10, "end_cp": 15},
            {"ref": "Q2", "text": "「好。」", "start_cp": 15, "end_cp": 19},
            {"ref": "Q3", "text": "「谁？」", "start_cp": 19, "end_cp": 23},
        ),
        (Candidate("C1", "native-a", "林舟"), Candidate("C2", "native-b", "周遥")),
    )


def payload():
    return {
        "labels": [
            {"q": "Q1", "kind": "speech", "character": "C1", "basis": "direct", "evidence": ["G1"]},
            {"q": "Q2", "kind": "speech", "character": "C2", "basis": "direct", "evidence": ["G2"]},
            {
                "q": "Q3",
                "kind": "speech",
                "character": None,
                "basis": "insufficient",
                "evidence": [],
            },
        ]
    }


GOLD = {"q1": "gold-a", "q2": "gold-b", "q3": "gold-a"}
MAPPING = {"native-a": "gold-a", "native-b": "gold-b"}


def test_gold_identity_equivalence_is_only_for_predictions_not_risk_evidence():
    t = task()
    output = compile_output(payload(), t)
    before = (asdict(t), output.model_dump(), dict(MAPPING))
    result = score_attribution_risks(t, output, GOLD, identity_mapping=MAPPING)
    assert result["scoring_version"] == SCORING_VERSION
    assert result["identities"]["correct"] == 2 and result["identities"]["unknown"] == 1
    assert result["native_predictions"]["q1"] == "native-a"
    assert result["predictions"]["q1"] == "gold-a"
    assert [r["ref"] for r in result["risks"]] == [r.quote_id for r in detect_risks(t, output)]
    assert all("direct_relation_unverified" not in r["reasons"] for r in result["risks"])
    assert (asdict(t), output.model_dump(), MAPPING) == before


def test_swapping_scored_names_changes_accuracy_not_native_risk_selection():
    t = task()
    output = compile_output(payload(), t)
    good = score_attribution_risks(t, output, GOLD, identity_mapping=MAPPING)
    bad = score_attribution_risks(
        t, output, GOLD, identity_mapping={"native-a": "gold-b", "native-b": "gold-a"}
    )
    assert good["risks"] == bad["risks"]
    assert bad["identities"]["incorrect"] == 2
    assert bad["risk_metrics"]["error_recall"] == 0.5


def test_premapped_declarations_fail_fast_instead_of_inflating_direct_risk():
    t = task()
    output = compile_output(payload(), t)
    output.new_speakers[0].character_id = "gold-a"
    with pytest.raises(ValueError, match="native task namespace"):
        score_attribution_risks(t, output, GOLD, identity_mapping=MAPPING)


def test_scene_local_existing_slots_resolve_native_identity_without_new_declaration():
    t = replace(
        task(),
        candidates=(
            Candidate("C1", "native-a", "林舟", existing_ref="S1"),
            Candidate("C2", "native-b", "周遥", existing_ref="S2"),
        ),
    )
    output = compile_output(payload(), t)
    assert not output.new_speakers
    result = score_attribution_risks(t, output, GOLD, identity_mapping=MAPPING)
    assert result["identities"]["correct"] == 2


def test_missing_and_non_speech_are_not_conflated_or_silently_accepted():
    t = task()
    output = compile_output(payload(), t)
    output.labels.pop()
    result = score_attribution_risks(t, output, GOLD, identity_mapping=MAPPING)
    assert result["identities"]["missing"] == 1 and result["identities"]["unknown"] == 0
    assert any("missing_label" in r["reasons"] for r in result["risks"])
    p = payload()
    p["labels"][-1] = {"q": "Q3", "kind": "thought"}
    result = score_attribution_risks(t, compile_output(p, t), GOLD, identity_mapping=MAPPING)
    assert result["identities"]["unknown"] == 1 and result["identities"]["missing"] == 0
    assert any("speech_type_ambiguity" in r["reasons"] for r in result["risks"])


def test_anonymous_discovery_does_not_guess_a_gold_identity_from_display_name():
    t = task()
    p = payload()
    p["labels"][1]["character"] = "N1"
    p["new_characters"] = [
        {"ref": "N1", "name": "门卫", "description": "在门口", "evidence": ["G2"]}
    ]
    result = score_attribution_risks(t, compile_output(p, t), GOLD, identity_mapping=MAPPING)
    assert result["predictions"]["q2"] is None
    assert result["identities"]["correct"] == 1 and result["identities"]["unknown"] == 2
    assert any("new_identity" in r["reasons"] for r in result["risks"])


def test_initial_task_is_unchanged_and_gold_does_not_enter_model_messages():
    t = replace(task(), reading_mode="initial", visible_horizon_cp=23)
    messages = json.dumps(t.messages(), ensure_ascii=False)
    score_attribution_risks(t, compile_output(payload(), t), GOLD, identity_mapping=MAPPING)
    assert json.dumps(t.messages(), ensure_ascii=False) == messages and "gold-a" not in messages


@pytest.mark.parametrize(
    "change", ["gold_target", "label_target", "duplicate_label", "duplicate_person"]
)
def test_unsent_and_duplicate_analysis_inputs_are_rejected(change):
    t = task()
    output = compile_output(payload(), t)
    gold = dict(GOLD)
    if change == "gold_target":
        gold["future"] = "gold-a"
    if change == "label_target":
        output.labels[0].quote_id = "future"
    if change == "duplicate_label":
        output.labels.append(output.labels[0].model_copy())
    if change == "duplicate_person":
        output.new_speakers.append(output.new_speakers[0].model_copy())
    with pytest.raises(ValueError):
        score_attribution_risks(t, output, gold, identity_mapping=MAPPING)


@pytest.mark.parametrize("mapping", [{"other": "gold-a"}, {"native-a": None}, {"native-a": ""}])
def test_identity_mapping_is_explicit_and_bound_to_native_candidates(mapping):
    t = task()
    with pytest.raises(ValueError, match="Identity mapping"):
        score_attribution_risks(t, compile_output(payload(), t), GOLD, identity_mapping=mapping)


def test_native_scoring_without_equivalence_is_supported():
    t = task()
    result = score_attribution_risks(
        t, compile_output(payload(), t), {"q1": "native-a", "q2": "native-b", "q3": "native-a"}
    )
    assert result["identities"]["correct"] == 2 and result["identities"]["unknown"] == 1
