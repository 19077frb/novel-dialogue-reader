import json

from ndr.characters.visibility import capture, initialize, visible_value
from ndr.storage.models import BookCharacter, SpeakerGroup


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
