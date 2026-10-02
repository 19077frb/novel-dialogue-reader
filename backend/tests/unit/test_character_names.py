from dataclasses import replace

import pytest

from ndr.characters.names import matches_name, revealed_name, valid_display_name
from ndr.llm.schemas import output_json_schema
from ndr.llm.validation import LabelingTargets, parse_and_validate
from ndr.scenes.state import ConfirmedCharacter, SceneState
from ndr.speakers.groups import SpeakerRegistry


@pytest.mark.parametrize("role,name", [
    ("女神", "阿库娅"), ("女骑士", "达克妮丝"), ("无头骑士", "贝尔迪亚"),
    ("魔王军干部", "贝尔迪亚"), ("自称无头骑士", "贝尔迪亚"),
])
def test_role_name_can_upgrade_only_with_explicit_unlocked_name(role, name):
    assert revealed_name(role, name) == name
    assert revealed_name(role, name, locked=True) is None
    assert revealed_name(name, role) is None
    assert revealed_name(role, "水之女神") is None
    assert revealed_name(role, "S1") is None


@pytest.mark.parametrize("name", ["贝尔迪亚", "骑士团长雷恩", "无头骑士贝尔迪亚"])
def test_named_people_are_not_treated_as_roles(name):
    assert revealed_name(name, "达克妮丝") is None


@pytest.mark.parametrize("value,expected", [
    ("浅村悠太", True),
    ("浅村悠太（本章第一人称叙述者，书店店员）", True),
    ("浅村悠太的父亲", False),
    ("和浅村悠太一起的同学", False),
])
def test_decorations_not_relationships(value, expected):
    assert matches_name(value, ["浅村悠太"]) is expected


def test_role_plus_explicit_name():
    assert matches_name("书店的女店员读卖栞（悠太的打工前辈）", ["读卖栞"])
    assert not matches_name("读卖栞的同事", ["读卖栞"])


def test_decorated_person_reuses_confirmed_scene_identity():
    state = SceneState(confirmed_characters=[ConfirmedCharacter("yuta", "浅村悠太")])
    registry = SpeakerRegistry(state)
    first = registry.register_temp_speaker(
        temp_ref="new1", first_quote_id="q1", canonical_name="浅村悠太",
    )
    second = registry.register_temp_speaker(
        temp_ref="new2", first_quote_id="q2",
        canonical_name="浅村悠太（本章第一人称叙述者，书店店员）",
    )
    assert second is first
    assert second.character_id == "yuta"
    assert second.canonical_name == "浅村悠太"
    assert len(state.participants) == 1


def test_ambiguous_alias_and_generic_names_are_not_merged():
    state = SceneState(confirmed_characters=[
        ConfirmedCharacter("a", "张三", aliases=("同桌",)),
        ConfirmedCharacter("b", "李四", aliases=("同桌",)),
    ])
    assert state._confirmed_by_name("同桌") is None
    registry = SpeakerRegistry(state)
    first = registry.register_temp_speaker(temp_ref="new1", first_quote_id="q1", canonical_name="男生")
    second = registry.register_temp_speaker(temp_ref="new2", first_quote_id="q2", canonical_name="男生")
    assert first is not second


@pytest.mark.parametrize("name", [
    None, "", "S1", "未知人物", "浅村悠太（书店店员）", "在书店向女店员搭讪的轻浮男客",
])
def test_live_output_requires_separate_short_name(name):
    targets = LabelingTargets(quote_ids=("q1",), require_display_names=True)
    output = {
        "new_speakers": [{"temp_ref": "new1", "scene_ref": "scene_current",
                          "first_quote_id": "q1", "description": "书店的男店员"}],
        "labels": [{"quote_id": "q1", "scene_ref": "scene_current", "kind": "speech",
                    "assignment": "NEW", "speaker_ref": "new1", "basis": "DIRECT"}],
    }
    if name is not None:
        output["new_speakers"][0]["name"] = name
    assert not parse_and_validate(output, targets).ok
    output["new_speakers"][0]["name"] = "轻浮男客"
    assert parse_and_validate(output, targets).ok
    # Historical output parsing remains compatible, but live calls are strict.
    output["new_speakers"][0].pop("name")
    assert parse_and_validate(output, replace(targets, require_display_names=False)).ok


def test_prompt_schema_requires_name():
    speaker_schema = output_json_schema()["$defs"]["NewSpeaker"]
    assert "name" in speaker_schema["required"]
    assert speaker_schema["properties"]["name"]["type"] == "string"
    assert valid_display_name("轻浮男客")


def test_explicit_character_reference_is_scoped_to_sent_catalog():
    targets = LabelingTargets(quote_ids=("q1",), character_ids=("girl",))
    output = {
        "new_speakers": [{"temp_ref": "new1", "scene_ref": "scene_current",
                          "first_quote_id": "q1", "name": "藤波夏帆",
                          "character_id": "girl", "description": "前文的高个子女生",
                          "evidence_refs": ["q1"]}],
        "labels": [{"quote_id": "q1", "scene_ref": "scene_current", "kind": "speech",
                    "assignment": "NEW", "speaker_ref": "new1", "basis": "DIRECT"}],
    }
    assert parse_and_validate(output, targets).ok
    output["new_speakers"][0]["character_id"] = "another-book"
    assert "unknown_character_in_speaker" in parse_and_validate(output, targets).error_codes
    output["new_speakers"][0]["character_id"] = "girl"
    output["new_speakers"][0]["evidence_refs"] = []
    assert "missing_character_evidence" in parse_and_validate(output, targets).error_codes


def test_later_window_temp_ref_does_not_override_stable_identity():
    state = SceneState(book_characters=[ConfirmedCharacter("a", "甲"),
                                       ConfirmedCharacter("b", "乙")])
    registry = SpeakerRegistry(state)
    first = registry.register_temp_speaker(temp_ref="new1", first_quote_id="q1",
                                           canonical_name="甲", character_id="a")
    second = registry.register_temp_speaker(temp_ref="new1", first_quote_id="q2",
                                            canonical_name="乙", character_id="b")
    assert second is not first
    assert first.character_id == "a"
    assert registry.resolve("new1") is second
