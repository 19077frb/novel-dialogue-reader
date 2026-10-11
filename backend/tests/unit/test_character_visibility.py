import json
from types import SimpleNamespace

import pytest

from ndr.characters.visibility import capture, initialize, visible_value
from ndr.storage.models import BookCharacter, SpeakerGroup


@pytest.mark.parametrize("merge_accepted", [True, False])
def test_ledger_history_parses_once_and_matches_visible_profiles(monkeypatch, merge_accepted):
    from ndr.characters import facts
    from ndr.characters.visibility import identity_presentation_history

    version = SimpleNamespace(id="v", canonical_sha256="a" * 64, canonical_length_cp=100)
    records = [{"kind": "description", "value": f"说明{i}", "visible_from_cp": i,
                "canonical_sha256": version.canonical_sha256, "source": "user",
                "source_ref": "edit", "accepted": True} for i in range(1, 20)]
    records.append({"kind": "name", "value": "林舟", "visible_from_cp": 3,
                    "canonical_sha256": version.canonical_sha256, "source": "user",
                    "source_ref": "edit", "accepted": True, "identity_links": [{
                        "source_id": "old", "target_id": "c", "visible_from_cp": 50,
                        "source": "user", "source_ref": "merge", "accepted": merge_accepted}]})
    character = BookCharacter(id="c", book_version_id="v", identity_facts_json=json.dumps(records))
    original = facts.read_identity_records
    calls = []

    def counted(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(facts, "read_identity_records", counted)
    rows = identity_presentation_history(character, version)
    assert len(calls) == 1
    for row in rows:
        profile = facts.visible_identity_profile(character, version, horizon=row["cp"])
        assert row["name"] == (profile["name"] or "")
        assert row["description"] == profile["description"]
    assert rows[-1]["name"] == ("林舟" if merge_accepted else "")
    # No cross-call cache: edits are visible immediately.
    character.identity_facts_json = "[]"
    character.presentation_history_json = '[{"cp":0,"name":"已修改"}]'
    assert identity_presentation_history(character, version)[0]["name"] == "已修改"


def test_history_uses_chapter_boundary_and_preserves_current_reread():
    character = BookCharacter(id="person", canonical_name="女神", description="早期说明")
    capture(character, 4)
    character.canonical_name = "阿库娅"
    character.description = "后文身份"
    capture(character, 20)
    group = SpeakerGroup(id="group", character_id=character.id)
    initialize(group, 2, character)
    fallback = {"identity": "character:person", "private_identity": "group:group",
                "name": "阿库娅", "description": "最终说明"}
    early = visible_value(group.presentation_history_json, 10, fallback=fallback)
    assert early["name"] == "女神" and early["description"] == "早期说明"
    before = visible_value(group.presentation_history_json, 3, fallback=fallback)
    assert before["name"] == "未确认说话人" and before["description"] == ""
    assert before["identity"] == "group:group"
    assert visible_value(group.presentation_history_json, 20, fallback=fallback)["name"] == "阿库娅"
    assert visible_value(group.presentation_history_json, None, fallback=fallback) == fallback
    capture(character, 50)
    assert len(json.loads(character.presentation_history_json)) == 2


def test_earlier_effective_position_does_not_erase_future_history():
    raw = json.dumps([
        {"cp": 0, "name": "少女", "description": "", "identity": "old"},
        {"cp": 20, "name": "阿库娅", "description": "新身份", "identity": "new"},
        {"cp": 10, "name": "女神", "description": "初始身份", "identity": "old"},
    ])
    assert visible_value(raw, 15, fallback={})["name"] == "女神"
    assert visible_value(raw, 25, fallback={})["name"] == "阿库娅"
