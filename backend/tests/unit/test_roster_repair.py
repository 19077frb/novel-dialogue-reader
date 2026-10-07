import hashlib
from copy import deepcopy

import pytest

from ndr.characters.facts import OriginalIdentitySnapshot
from ndr.llm.roster_repair import compile_roster_repair, prepare_roster_repair
from ndr.llm.sourced_roster import IsolatedRosterFailure, compile_isolated_sourced_roster

TEXT = "第一章\n林舟说：「你好。」\n陆欣说：「再见。」\n门卫看着他们。\n"


def original(text=TEXT, version="v"):
    return OriginalIdentitySnapshot(version, hashlib.sha256(text.encode()).hexdigest(), text)


def person(ref="c1", name="林舟", line="L2", **changes):
    return {"temp_ref": ref, "name": name, "evidence_refs": [line],
            "facts": [{"kind": "name", "value": name, "evidence_refs": [line]}], **changes}


def plan_for(people):
    return prepare_roster_repair(
        {"schema_version": "1.1", "characters": people}, original=original(),
        chapter_start=0, chapter_end=len(TEXT), allowed_character_ids={"old"},
        source_ref="primary-run",
    )


def repair(plan, replacements, **kwargs):
    return compile_roster_repair(
        plan, {"schema_version": "roster-repair-1", "repairs": replacements},
        **{"original": original(), "source_ref": "repair-run", **kwargs},
    )


def test_valid_identity_immutable_and_sources_and_reveal_times_remain_independent():
    good = person()
    bad = person("c2", "陆欣", "L999")
    initial = [good, bad]
    plan = plan_for(initial)
    initial[0]["name"] = "被篡改的原始输入"
    task = plan.task_payload()
    assert task["groups"][0]["indices"] == [2]
    task["retained_characters"][0]["name"] = "被篡改的提示对象"
    fixed = person("c2", "陆欣", "L3")
    output, facts = repair(plan, [{"indices": [2], "characters": [fixed]}])
    assert [p.name for p in output.characters] == ["林舟", "陆欣"]
    assert {f.source_ref for f in facts["c1"]} == {"primary-run"}
    assert {f.source_ref for f in facts["c2"]} == {"repair-run"}
    assert facts["c1"][0].visible_from_cp < facts["c2"][0].visible_from_cp
    assert all(not f.accepted for items in facts.values() for f in items)


def test_all_invalid_still_has_repairable_diagnostics_but_is_rejected_by_old_caller():
    bad = person(name="不存在的名字", line="L999")
    args = {"original": original(), "chapter_start": 0, "chapter_end": len(TEXT),
            "allowed_character_ids": {"old"}, "source_ref": "primary-run"}
    with pytest.raises(IsolatedRosterFailure) as raised:
        compile_isolated_sourced_roster({"schema_version": "1.1", "characters": [bad]}, **args)
    assert isinstance(raised.value, ValueError)
    assert raised.value.diagnostics["isolated_indices"] == [1]
    plan = plan_for([bad])
    output, _ = repair(plan, [{"indices": [1], "characters": [person()]}])
    assert output.characters[0].name == "林舟"


def test_repair_does_not_move_early_identity_after_retained_later_people():
    plan = plan_for([person(line="L999"), person("c2", "陆欣", "L3")])
    output, _ = repair(plan, [{"indices": [1], "characters": [person()]}])
    assert [p.temp_ref for p in output.characters] == ["c1", "c2"]


def test_transitive_duplicate_dependencies_must_be_repaired_together():
    people = [person("a"), person("a", character_id="old"),
              person("b", character_id="old"), person("independent", "陆欣", "L3")]
    plan = plan_for(people)
    assert plan.groups == ((1, 2, 3),)
    output, facts = repair(plan, [{"indices": [3, 1, 2], "characters": [person("a")]}])
    assert {p.temp_ref for p in output.characters} == {"independent", "a"}
    assert facts["independent"][0].source_ref == "primary-run"
    with pytest.raises(ValueError, match="依赖组"):
        repair(plan, [{"indices": [1, 2], "characters": [person("a")]},
                      {"indices": [3], "characters": [person("b")]}])


@pytest.mark.parametrize("indices", [[1], [3], [2, 2], [True], ["2"], [2.0]])
def test_unknown_retained_duplicate_or_coerced_indices_rejected(indices):
    plan = plan_for([person(), person("c2", line="L999")])
    with pytest.raises(ValueError):
        repair(plan, [{"indices": indices, "characters": [person("c2")]}])


def test_no_groups_may_be_lost_duplicated_or_erased():
    plan = plan_for([person("a", line="L999"), None])
    cases = [[], [{"indices": [1], "characters": [person("a")]}],
             [{"indices": [1], "characters": [person("a")]},
              {"indices": [1], "characters": [person("b")]}],
             [{"indices": [1], "characters": []},
              {"indices": [2], "characters": [person("b")]}]]
    for replacements in cases:
        with pytest.raises(ValueError):
            repair(plan, replacements)


@pytest.mark.parametrize("damage", ["unknown_id", "duplicate_ref", "duplicate_id",
                                   "future_ref", "bad_name", "bad_pov"])
def test_final_whole_roster_compilation_cannot_be_bypassed(damage):
    good = person(character_id="old")
    fixed = person("c2", "陆欣", "L3")
    if damage == "unknown_id":
        fixed["character_id"] = "not-sent"
    elif damage == "duplicate_ref":
        fixed["temp_ref"] = "c1"
    elif damage == "duplicate_id":
        fixed["character_id"] = "old"
    elif damage == "future_ref":
        fixed["facts"][0]["evidence_refs"] = ["L999"]
    elif damage == "bad_name":
        fixed["name"] = "无依据人物"
    else:
        fixed["pov_candidate"] = True
    plan = plan_for([good, person("c2", line="L999")])
    with pytest.raises(ValueError):
        repair(plan, [{"indices": [2], "characters": [fixed]}])


@pytest.mark.parametrize("changes", [{"source_ref": ""}, {"source_ref": "primary-run"},
                                   {"original": original(version="other")},
                                   {"original": original(TEXT + "后文姓名。\n")}])
def test_changed_original_or_missing_independent_receipt_rejected(changes):
    plan = plan_for([None])
    with pytest.raises(ValueError):
        repair(plan, [{"indices": [1], "characters": [person()]}], **changes)


def test_valid_or_truly_empty_proposal_needs_no_repair_targets():
    for people in ([], [person()]):
        plan = plan_for(people)
        assert plan.groups == () and plan.task_payload()["groups"] == []
        output, facts = repair(plan, [])
        assert len(output.characters) == len(people)
        assert all(f.source_ref == "primary-run" for rows in facts.values() for f in rows)


def test_target_indices_not_truncated_with_human_diagnostic_details():
    plan = plan_for([person(), *([None] * 101)])
    task = plan.task_payload()
    assert len(task["groups"]) == 101 and task["groups"][-1]["indices"] == [102]
    output, _ = repair(plan, [{"indices": [i], "characters": [person(f"c{i}")]} for i in range(2, 103)])
    assert len(output.characters) == 102


def test_schema_or_extra_fields_cannot_instruct_retained_updates():
    plan = plan_for([None])
    payload = {"schema_version": "roster-repair-1", "repairs": [
        {"indices": [1], "characters": [person()]},
    ]}
    for change in ({"schema_version": "legacy"}, {"retained_characters": [person("other")]}):
        with pytest.raises(ValueError):
            compile_roster_repair(plan, {**deepcopy(payload), **change},
                                  original=original(), source_ref="repair-run")
