import hashlib
from copy import deepcopy

import pytest

from ndr.characters.facts import OriginalIdentitySnapshot
from ndr.llm.sourced_roster import (
    compile_isolated_sourced_roster,
    compile_sourced_roster,
    original_lines,
)

TEXT = "前章\n第一章\n门卫在门边。\n林舟说：「你好。」\n陆欣称他为小舟。\n林舟是学生会长。\n"
START = TEXT.index("第一章")


def proposal():
    return {"schema_version": "1.1", "characters": [{
        "temp_ref": "c1", "name": "林舟", "aliases": ["小舟"], "description": "",
        "evidence_refs": ["L3"], "pov_candidate": True, "pov_evidence_refs": ["L3"],
        "facts": [{"kind": "name", "value": "林舟", "evidence_refs": ["L3"]},
                  {"kind": "alias", "value": "小舟", "evidence_refs": ["L4"]},
                  {"kind": "relation", "value": "学生会长", "evidence_refs": ["L5"]}],
    }]}


@pytest.mark.parametrize("description", ["", "改述但与事实文字不同"])
def test_description_falls_back_to_one_valid_fact_not_concatenation(description):
    payload = proposal()
    person = payload["characters"][0]
    person["description"] = description
    person["facts"].extend([
        {"kind": "description", "value": "学生会长", "evidence_refs": ["L5"]},
        {"kind": "description", "value": "被陆欣称为小舟的学生会长", "evidence_refs": ["L4", "L5"]},
    ])
    output, _, _ = compile_isolated(payload)
    assert output.characters[0].description == "被陆欣称为小舟的学生会长"


def test_verified_full_name_promoted_but_bad_name_evidence_not_salvaged():
    payload = proposal()
    person = payload["characters"][0]
    person["name"] = "林"
    person["facts"].append({"kind": "name", "value": "林", "evidence_refs": ["L3"]})
    # A one-letter fragment is not automatically treated as a full-name link.
    output, _, _ = compile_isolated(payload)
    assert output.characters[0].name == "林"
    person["facts"][0]["evidence_refs"] = ["L999"]
    with pytest.raises(ValueError):
        compile_isolated(payload)


def test_full_name_comes_from_same_identity_valid_name_fact_not_alias_order():
    text = "陈小舟走来，小舟挥手。"
    payload = {"schema_version": "1.1", "characters": [{
        "temp_ref": "c1", "name": "小舟", "description": "", "evidence_refs": ["L1"],
        "facts": [{"kind": "name", "value": "陈小舟", "evidence_refs": ["L1"]},
                  {"kind": "name", "value": "小舟", "evidence_refs": ["L1"]}],
    }]}
    output, _, _ = compile_isolated_sourced_roster(payload,
        original=OriginalIdentitySnapshot("v", hashlib.sha256(text.encode()).hexdigest(), text),
        chapter_start=0, chapter_end=len(text), allowed_character_ids=set(), source_ref="run1")
    person = output.characters[0]
    assert person.name == person.real_name == "陈小舟" and person.aliases == ["小舟"]


def compile_proposal(payload=None, **changes):
    args = {"original": OriginalIdentitySnapshot("v", hashlib.sha256(TEXT.encode()).hexdigest(), TEXT),
            "chapter_start": START, "chapter_end": len(TEXT),
            "allowed_character_ids": {"old-person"}, "source_ref": "run1"}
    return compile_sourced_roster(proposal() if payload is None else payload, **{**args, **changes})


def test_actual_chapter_lines_map_to_whole_original_not_zero_or_chapter_end():
    output, facts = compile_proposal()
    assert output.characters[0].name == "林舟"
    rows = facts["c1"]
    lines = original_lines(TEXT, START, len(TEXT))
    assert rows[0].evidence_spans == (lines["L3"][:2],)
    assert rows[0].visible_from_cp < rows[1].visible_from_cp < rows[2].visible_from_cp
    assert all(row.source == "model" and not row.accepted for row in rows)


def test_existing_identity_association_gates_all_new_facts_until_its_evidence():
    payload = proposal()
    payload["characters"][0].update(character_id="old-person", evidence_refs=["L5"])
    _, facts = compile_proposal(payload)
    assert all(f.visible_from_cp == original_lines(TEXT, START, len(TEXT))["L5"][1]
               for f in facts["c1"])


def test_pronoun_is_not_a_named_or_anonymous_identity():
    payload = {"schema_version": "1.1", "characters": [{"temp_ref": "c1", "name": "我",
        "evidence_refs": ["L3"], "facts": [
            {"kind": "designation", "value": "我", "evidence_refs": ["L3"]}],
    }]}
    with pytest.raises(ValueError, match="人称代词"):
        compile_proposal(payload)


@pytest.mark.parametrize("change", ["version", "unknown_line", "literal", "alias", "pov",
                                   "duplicate", "identity", "description", "name", "fact"])
def test_invalid_proof_or_identity_blocks_are_never_compiled(change):
    payload = deepcopy(proposal())
    person = payload["characters"][0]
    if change == "version":
        payload["schema_version"] = "1.0"
    elif change == "unknown_line":
        person["facts"][0]["evidence_refs"] = ["L999"]
    elif change == "literal":
        person["facts"][0]["evidence_refs"] = ["L2"]
    elif change == "alias":
        person["aliases"].append("学生会长")
    elif change == "pov":
        person["pov_evidence_refs"] = []
    elif change == "duplicate":
        payload["characters"].append(deepcopy(person))
    elif change == "identity":
        person["character_id"] = "not-sent"
    elif change == "description":
        person["description"] = "没有依据的说明"
    elif change == "name":
        person["name"] = "另一个人"
    else:
        person["facts"].append(deepcopy(person["facts"][0]))
    with pytest.raises(ValueError):
        compile_proposal(payload)


def compile_isolated(payload):
    return compile_isolated_sourced_roster(
        payload, original=OriginalIdentitySnapshot("v", hashlib.sha256(TEXT.encode()).hexdigest(), TEXT),
        chapter_start=START, chapter_end=len(TEXT), allowed_character_ids={"old-person"},
        source_ref="run1",
    )


def test_bad_auxiliary_fact_does_not_discard_supported_identity():
    payload = proposal()
    person = payload["characters"][0]
    person["description"] = "无效说明"
    person["facts"].extend([
        {"kind": "description", "value": "无效说明", "evidence_refs": ["L999"]},
        {"kind": "relation", "value": "朋友", "evidence_refs": []},
    ])
    output, facts, diagnostic = compile_isolated(payload)
    assert output.characters[0].description == ""
    assert len(facts["c1"]) == 3
    assert diagnostic["discarded_auxiliary_facts"] == 2
    assert diagnostic["discarded_descriptions"] == 1
    assert diagnostic["isolated_characters"] == 0
    with pytest.raises(ValueError):  # Previous frozen compiler remains strict.
        compile_proposal(payload)


@pytest.mark.parametrize("damage", ["name", "association", "pov", "unknown_fact", "structure"])
def test_damaged_identity_block_cannot_destroy_independent_person(damage):
    payload = proposal()
    bad = deepcopy(payload["characters"][0])
    bad["temp_ref"] = "c2"
    if damage == "name":
        bad["name"] = "不存在的名字"
    elif damage == "association":
        bad.update(character_id="old-person", evidence_refs=["L999"])
    elif damage == "pov":
        bad["pov_evidence_refs"] = []
    elif damage == "unknown_fact":
        bad["facts"].append({"kind": "mystery", "value": "未知", "evidence_refs": ["L3"]})
    else:
        bad = "not a person block"
    payload["characters"].append(bad)
    output, facts, diagnostic = compile_isolated(payload)
    assert [p.temp_ref for p in output.characters] == ["c1"]
    assert set(facts) == {"c1"}
    assert diagnostic["isolated_characters"] == 1


@pytest.mark.parametrize("key", ["temp_ref", "character_id"])
def test_repeated_dependencies_quarantine_all_related_blocks(key):
    payload = proposal()
    two = deepcopy(payload["characters"][0])
    two["temp_ref"] = "c2"
    if key == "temp_ref":
        two["temp_ref"] = "c1"
    else:
        two["character_id"] = payload["characters"][0]["character_id"] = "old-person"
    three = deepcopy(proposal()["characters"][0])
    three["temp_ref"] = "c3"
    payload["characters"].extend([two, three])
    output, facts, diagnostic = compile_isolated(payload)
    assert [p.temp_ref for p in output.characters] == ["c3"]
    assert set(facts) == {"c3"}
    assert diagnostic["isolated_characters"] == 2


def test_no_valid_identity_is_failure_but_genuine_empty_list_is_preserved():
    payload = proposal()
    payload["characters"][0]["name"] = "无依据人物"
    payload["characters"][0]["facts"].extend([
        {"kind": "description", "value": "坏说明", "evidence_refs": ["L999"]},
    ] * 101)
    with pytest.raises(ValueError, match="姓名或代称缺少自己的事实依据"):
        compile_isolated(payload)
    output, facts, diagnostic = compile_isolated({"schema_version": "1.1", "characters": []})
    assert not output.characters and not facts and diagnostic["isolated_characters"] == 0
    with pytest.raises(ValueError):
        compile_isolated({"schema_version": "1.1", "characters": [], "unknown_top": True})


def test_diagnostic_details_are_bounded_without_losing_total_count():
    payload = proposal()
    payload["characters"].extend([None] * 101)
    output, _, diagnostic = compile_isolated(payload)
    assert len(output.characters) == 1
    assert diagnostic["isolated_characters"] == 101 and len(diagnostic["details"]) == 100


def test_valid_isolated_result_matches_strict_proof_and_does_not_hide_size_errors():
    strict_output, strict_facts = compile_proposal()
    output, facts, diagnostic = compile_isolated(proposal())
    assert output == strict_output and facts == strict_facts
    assert not diagnostic["details"] and diagnostic["isolated_characters"] == 0
    payload = proposal()
    payload["characters"][0]["facts"] = [
        {"kind": "description", "value": "无效说明", "evidence_refs": ["L999"]},
    ] * 257
    with pytest.raises(ValueError, match="事实数量超出范围"):
        compile_isolated(payload)


@pytest.mark.parametrize("description", [None, 0, False, "说明" * 300])
def test_malformed_optional_description_is_not_an_identity_failure(description):
    payload = proposal()
    payload["characters"][0]["description"] = description
    output, facts, diagnostic = compile_isolated(payload)
    assert len(output.characters) == 1 and output.characters[0].description == ""
    assert len(facts["c1"]) == 3
    assert diagnostic["discarded_descriptions"] == 1
    assert diagnostic["isolated_characters"] == 0
