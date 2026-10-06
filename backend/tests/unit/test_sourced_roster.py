import hashlib
from copy import deepcopy

import pytest

from ndr.characters.facts import OriginalIdentitySnapshot
from ndr.llm.sourced_roster import compile_sourced_roster, original_lines

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
