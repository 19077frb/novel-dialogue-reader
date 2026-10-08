import json

import pytest

from ndr.characters.auto_merge import (
    MergeOutput,
    _confirmation_source,
    _messages,
    _name_candidates,
    _restore_plan_references,
    _validate_plan,
    _validated_groups,
)
from ndr.characters.merge_diagnostics import MergePlanError, saved_model_groups


@pytest.mark.parametrize("entry,expected", [
    ({"user_confirmed": False}, "model"),
    ({"user_confirmed": True}, "legacy"),
    ({"user_confirmed": True, "name_locked": True}, "manual"),
    ({"confirmation_source": "automatic"}, "automatic"),
    ({"confirmation_source": "imported", "user_confirmed": True}, "imported"),
])
def test_old_merge_snapshots_do_not_invent_human_confirmation(entry, expected):
    assert _confirmation_source(entry) == expected


def test_merge_hints_cover_multiple_identities_without_auto_acceptance():
    entries = [
        {"character_id": "a", "name": "悠太", "aliases": ["浅村悠太"]},
        {"character_id": "b", "name": "浅村悠太（主人公）", "aliases": []},
        {"character_id": "c", "name": "沙季", "aliases": ["绫濑沙季"]},
        {"character_id": "d", "name": "绫濑沙季", "aliases": []},
        {"character_id": "e", "name": "男生", "aliases": []},
        {"character_id": "f", "name": "男生", "aliases": []},
    ]
    assert {tuple(hint["character_ids"]) for hint in _name_candidates(entries)} == {
        ("a", "b"),
        ("c", "d"),
    }
    messages = _messages(entries)
    assert "merged_description" in messages[0]["content"]
    assert "不得逐段拼接" in messages[0]["content"]
    assert "若事实有冲突" in messages[0]["content"]
    assert "一次返回全部" in messages[0]["content"]
    assert "不是合并结论" in messages[0]["content"]
    payload = json.loads(messages[1]["content"])
    assert [row["character_id"] for row in payload["characters"]] == [f"C{i}" for i in range(1, 7)]
    assert [row["name"] for row in payload["characters"]] == [row["name"] for row in entries]
    assert entries[0]["character_id"] == "a"
    assert len(payload["possible_name_matches"]) == 2
    assert payload["possible_name_matches"][0]["character_ids"] in [["C1", "C2"], ["C3", "C4"]]


def test_merge_hints_deduplicate_equivalent_alias_sets():
    entries = [
        {"character_id": key, "name": "悠太", "aliases": ["浅村悠太", "哥哥"]}
        for key in ["a", "b", "c"]
    ]
    assert len(_name_candidates(entries)) == 1


def _group(target="C1", sources=None):
    return {"target_id": target, "source_ids": sources or ["C2"], "confidence": 0.99,
            "reason": "同一人物", "merged_description": "整理后的人物说明"}


def test_short_refs_restore_only_exact_known_ids():
    entries = [{"character_id": "uuid-a", "name": "甲", "kind": "book"},
               {"character_id": "speaker:uuid-b", "name": "乙", "kind": "speaker"}]
    output = _restore_plan_references(MergeOutput.model_validate({"groups": [_group()]}), entries)
    assert output.groups[0].target_id == "uuid-a"
    assert output.groups[0].source_ids == ["speaker:uuid-b"]
    assert _validate_plan(output, entries)
    bad = _restore_plan_references(MergeOutput.model_validate({"groups": [_group("C01")]}), entries)
    with pytest.raises(MergePlanError) as error:
        _validate_plan(bad, entries)
    assert error.value.issues[0]["code"] == "unknown_character"
    assert error.value.issues[0]["character_ref"] == "C01"


@pytest.mark.parametrize("groups,code", [
    ([_group(sources=["C2", "C2"])], "duplicate_source"),
    ([_group(sources=["C1"])], "target_in_sources"),
    ([_group(), _group("C3", ["C2"])], "overlapping_groups"),
    ([_group(), _group("C2", ["C3"])], "dependent_groups"),
    ([_group("C500")], "unknown_character"),
])
def test_plan_conflicts_report_specific_groups_without_repair(groups, code):
    entries = [{"character_id": f"uuid-{i}", "name": f"人物{i}", "kind": "book"}
               for i in range(1, 4)]
    output = _restore_plan_references(MergeOutput.model_validate({"groups": groups}), entries)
    with pytest.raises(MergePlanError) as error:
        _validate_plan(output, entries)
    assert any(issue["code"] == code for issue in error.value.issues)
    assert all(issue["group_index"] for issue in error.value.issues)
    assert "未执行合并" in str(error.value)


def test_saved_plan_is_bounded_and_does_not_retain_provider_metadata():
    group = _group()
    group.update(api_key="secret", reason="依据\nauthorization: Bearer secret",
                 merged_description="甲" * 5000)
    saved = saved_model_groups({"groups": [group], "_usage": {"secret": "credential"}})
    text = json.dumps(saved, ensure_ascii=False)
    assert "secret" not in text and "credential" not in text
    assert "[已脱敏]" in text
    assert saved["model_groups_truncated"]
    assert len(saved["model_groups"][0]["merged_description"]) == 4096
    assert saved["model_groups"][0]["extra_fields"] == ["[已脱敏]"]


def _entries(count=8):
    return [{"character_id": f"uuid-{i}", "name": f"人物{i}", "kind": "book"}
            for i in range(1, count + 1)]


@pytest.mark.parametrize("same_name", ["人物1", " 人物1 "])
def test_same_name_is_no_rename_but_keeps_merge(same_name):
    group = {**_group(), "preferred_name": same_name}
    output, skipped, issues = _validated_groups({"groups": [group]}, _entries())
    assert output.groups[0].preferred_name is None
    assert output.groups[0].source_ids == ["uuid-2"]
    assert skipped == 0 and issues == []


@pytest.mark.parametrize("bad", [
    {**_group("C3", ["C4"]), "preferred_name": "未提供姓名"},
    {**_group("C3", ["C4"]), "merged_description": " "},
    {**_group("C3", ["C4"]), "confidence": 2},
    {**_group("C3", ["C4"]), "extra": "secret"},
    _group("C999", ["C4"]),
    {**_group("C3", ["C4"]), "source_ids": [], "preferred_name": "人物3"},
    "invalid",
])
def test_invalid_independent_group_does_not_hide_valid_groups(bad):
    output, skipped, issues = _validated_groups(
        {"groups": [_group(), bad, _group("C5", ["C6"])]}, _entries(),
    )
    assert [group.target_id for group in output.groups] == ["uuid-1", "uuid-5"]
    assert skipped == 1
    assert all(issue["group_index"] == 2 for issue in issues)
    assert "secret" not in json.dumps(issues)


@pytest.mark.parametrize("malformed", [False, True])
def test_every_conflicting_group_is_excluded_including_malformed(malformed):
    dependent = _group("C2", ["C3"])
    if malformed:
        dependent["merged_description"] = ""
    output, skipped, issues = _validated_groups({"groups": [
        _group(), dependent, _group("C3", ["C4"]), _group("C5", ["C6"]),
    ]}, _entries())
    assert [group.target_id for group in output.groups] == ["uuid-5"]
    assert skipped == 3
    assert {issue["group_index"] for issue in issues} == {1, 2, 3}


def test_diagnostic_limit_does_not_limit_conflict_exclusion():
    groups = [_group()] * 60 + [_group("C5", ["C6"])]
    output, skipped, issues = _validated_groups({"groups": groups}, _entries())
    assert [group.target_id for group in output.groups] == ["uuid-5"]
    assert skipped == 60 and len(issues) == 50


def test_all_invalid_groups_fail_and_empty_plan_is_valid():
    with pytest.raises(MergePlanError):
        _validated_groups({"groups": [_group(), _group("C2", ["C3"])]}, _entries())
    output, skipped, issues = _validated_groups({"groups": []}, _entries())
    assert output.groups == [] and skipped == 0 and issues == []
