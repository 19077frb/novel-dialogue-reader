"""Authored repair-policy cases; no real novel, provider or user's database."""

from copy import deepcopy

import pytest
from test_roster_repair import TEXT, original, person

from ndr.llm.isolated_roster_repair import (
    compile_isolated_roster_repair,
    prepare_isolated_roster_repair,
)


def prepare(people):
    return prepare_isolated_roster_repair(
        {"schema_version": "1.1", "characters": people}, original=original(),
        chapter_start=0, chapter_end=len(TEXT), allowed_character_ids={"old"},
        source_ref="primary-run",
    )


def compile(plan, groups, source="repair-run", **changes):
    return compile_isolated_roster_repair(plan, {"schema_version": "roster-repair-1", "repairs": groups},
                                         original=original(), source_ref=source, **changes)


def group(indices, *people):
    return {"indices": indices, "characters": list(people)}


def test_unsupported_auxiliary_description_does_not_reject_valid_identity():
    plan = prepare([person(line="L999"), person("c2", "陆欣", "L3")])
    fixed = person(description="不是逐事实说明")
    fixed["facts"].append({"kind": "description", "value": "打招呼", "evidence_refs": ["L999"]})
    raw = group([1], fixed)
    saved = deepcopy(raw)
    output, facts, remaining, diagnostics = compile(plan, [raw])
    assert raw == saved and not remaining.original_plan.groups
    assert [p.name for p in output.characters] == ["林舟", "陆欣"]
    assert output.characters[0].description == ""
    assert diagnostics["discarded_auxiliary_facts"] == diagnostics["discarded_descriptions"] == 1
    assert facts["c1"][0].source_ref == "repair-run"
    assert facts["c2"][0].source_ref == "primary-run"


def test_independent_success_survives_and_next_round_only_repairs_remaining_group():
    plan = prepare([person("a", line="L999"), person("b", "陆欣", "L999")])
    initial_fingerprint = plan.fingerprint()
    output, facts, pending, diagnostic = compile(plan, [
        group([1], person("a", line="L999")), group([2], person("b", "陆欣", "L3")),
    ], source="first-repair")
    assert [p.name for p in output.characters] == ["陆欣"]
    assert facts["b"][0].source_ref == "first-repair"
    assert diagnostic["unresolved_groups"] == [[1]]
    assert diagnostic["successful_groups"] == [[2]]
    assert pending.task_payload()["groups"][0]["indices"] == [1]
    assert len(pending.task_payload()["groups"]) == 1
    assert plan.fingerprint() == initial_fingerprint != pending.fingerprint()
    output, facts, done, _ = compile(pending, [group([1], person("a"))], source="second-repair")
    assert [p.name for p in output.characters] == ["林舟", "陆欣"]
    assert facts["a"][0].source_ref == "second-repair"
    assert facts["b"][0].source_ref == "first-repair"
    assert not done.original_plan.groups
    with pytest.raises(ValueError, match="依赖组"):
        compile(pending, [group([1], person("a")), group([2], person("b", "陆欣", "L3"))])
    with pytest.raises(ValueError, match="独立"):
        compile(pending, [group([1], person("a"))], source="first-repair")


def test_duplicate_dependency_cannot_keep_only_one_valid_member():
    plan = prepare([person("duplicate"), person("duplicate"), person("retained", "陆欣", "L3")])
    output, _, pending, diagnostics = compile(plan, [
        group([1, 2], person("a"), person("b", line="L999")),
    ])
    assert [p.temp_ref for p in output.characters] == ["retained"]
    assert diagnostics["successful_groups"] == []
    assert pending.original_plan.groups == ((1, 2),)


def test_whole_roster_cross_group_conflict_remains_strict():
    plan = prepare([person(), person("bad", "陆欣", "L999")])
    with pytest.raises(ValueError, match="引用重复"):
        compile(plan, [group([2], person("c1", "陆欣", "L3"))])


def test_multiple_repaired_people_in_one_block_keep_sources_and_order_next_round():
    plan = prepare([person("bad", line="L999"), person("later", line="L999")])
    _, _, pending, _ = compile(plan, [
        group([1], person("a"), person("b", "陆欣", "L3")),
        group([2], person("later", line="L999")),
    ], source="first-repair")
    output, facts, _, _ = compile(pending, [group([2], person("later"))], source="second-repair")
    assert [p.temp_ref for p in output.characters] == ["a", "b", "later"]
    assert {f.source_ref for p in ("a", "b") for f in facts[p]} == {"first-repair"}
    assert facts["later"][0].source_ref == "second-repair"


@pytest.mark.parametrize("damage", ["unknown_id", "future_name", "pronoun", "bad_pov"])
def test_main_identity_damage_is_not_salvaged_as_auxiliary_information(damage):
    plan = prepare([person(line="L999"), person("retained", "陆欣", "L3")])
    fixed = person()
    if damage == "unknown_id":
        fixed["character_id"] = "not-provided"
    elif damage == "future_name":
        fixed["facts"][0]["evidence_refs"] = ["L999"]
    elif damage == "pronoun":
        fixed["facts"].append({"kind": "alias", "value": "我", "evidence_refs": ["L2"]})
    else:
        fixed["pov_candidate"] = True
    output, _, pending, diagnostic = compile(plan, [group([1], fixed)])
    assert [p.temp_ref for p in output.characters] == ["retained"]
    assert diagnostic["successful_groups"] == [] and pending.groups == ((1,),)


@pytest.mark.parametrize("indices", [[True], ["1"], [1.0], [1, 1], [2]])
def test_exact_dependency_group_coverage_and_strict_indices_remain_required(indices):
    plan = prepare([person(line="L999")])
    with pytest.raises(ValueError):
        compile(plan, [group(indices, person())])


def test_changed_source_and_extra_schema_fields_do_not_partially_write():
    plan = prepare([person(line="L999")])
    payload = {"schema_version": "roster-repair-1", "repairs": [group([1], person())]}
    for source in (original(version="other"), original(TEXT + "后文姓名。\n")):
        with pytest.raises(ValueError, match="原文快照"):
            compile_isolated_roster_repair(plan, payload, original=source, source_ref="repair-run")
    with pytest.raises(ValueError):
        compile_isolated_roster_repair(plan, {**payload, "retained_characters": [person("other")]},
                                      original=original(), source_ref="repair-run")


def test_valid_or_empty_roster_needs_no_groups_and_multiple_calls_keep_facts_unaccepted():
    for people in ([], [person()]):
        plan = prepare(people)
        output, facts, next_plan, diagnostics = compile(plan, [])
        assert not next_plan.groups and len(output.characters) == len(people)
        assert diagnostics["successful_groups"] == []
        assert all(not fact.accepted and fact.source_ref == "primary-run"
                   for values in facts.values() for fact in values)
