import json

from ndr.characters.auto_merge import _messages, _name_candidates


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
    assert "一次返回全部" in messages[0]["content"]
    assert "不是合并结论" in messages[0]["content"]
    payload = json.loads(messages[1]["content"])
    assert payload["characters"] == entries
    assert len(payload["possible_name_matches"]) == 2


def test_merge_hints_deduplicate_equivalent_alias_sets():
    entries = [
        {"character_id": key, "name": "悠太", "aliases": ["浅村悠太", "哥哥"]}
        for key in ["a", "b", "c"]
    ]
    assert len(_name_candidates(entries)) == 1
