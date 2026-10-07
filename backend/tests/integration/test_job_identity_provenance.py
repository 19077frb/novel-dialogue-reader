import json
from dataclasses import replace

import pytest

from ndr.domain.enums import CharacterSource
from ndr.jobs.scheduler import _apply_confirmed_roster, _cache_key_for
from ndr.scenes.runner import _messages_for
from ndr.scenes.state import ConfirmedCharacter, SceneState, SpeakerSlot
from ndr.storage.models import BookCharacter, BookVersion, Job
from ndr.storage.transactions import transaction


def task_parameters(messages):
    return json.loads(messages[1]["content"].split("任务参数（JSON）：\n", 1)[1].split("\n\n", 1)[0])


@pytest.mark.parametrize(
    "source,confirmed,confirmation",
    [(CharacterSource.MODEL, False, "automatic"), (CharacterSource.USER, True, "manual"),
     (CharacterSource.USER, False, "legacy"), (CharacterSource.MODEL, True, "manual")],
)
def test_actual_roster_loading_preserves_independent_provenance_columns(
    migrated_client, source, confirmed, confirmation,
):
    client = migrated_client
    data = client.post("/api/books/import", files={
        "file": ("source.txt", "第一章\n「你好。」".encode(), "text/plain"),
    }).json()["data"]
    base = f"/api/books/{data['book_id']}"
    chapter = client.get(f"{base}/chapters").json()["data"][0]
    response = client.put(f"{base}/chapters/{chapter['id']}/character-roster", json={
        "candidates": [{"temp_ref": "p1", "canonical_name": "小舟", "description": "同学"}],
        "pov_temp_ref": "p1", "expected_version": 1,
    })
    assert response.status_code == 200, response.text
    person_id = response.json()["data"]["pov_character_id"]
    with transaction(client.app.state.session_factory) as session:
        person = session.get(BookCharacter, person_id)
        person.source, person.user_confirmed, person.confirmation_source = source, confirmed, confirmation
        version = session.get(BookVersion, data["book_version_id"])
        state = SceneState()
        ok, error = _apply_confirmed_roster(
            session, Job(range_json=json.dumps({"chapter_id": chapter["id"]})), version, state,
        )
        assert ok and error is None
        for item in [*state.confirmed_characters, *state.book_characters]:
            assert (item.source, item.user_confirmed, item.confirmation_source) == (
                source.value, confirmed, confirmation,
            )


def test_old_checkpoint_does_not_invent_manual_confirmation():
    restored = SceneState.from_snapshot({"confirmed_characters": [{
        "character_id": "c1", "canonical_name": "小舟",
    }]})
    person = restored.confirmed_characters[0]
    assert person.source == person.confirmation_source == "unknown"
    assert person.user_confirmed is None
    assert SceneState.from_snapshot(restored.snapshot()).confirmed_characters == [person]


@pytest.mark.parametrize("reading_mode", ["initial", "reread"])
def test_prompt_carries_same_provenance_in_roster_pov_and_scene(reading_mode):
    from types import SimpleNamespace

    from ndr.domain.enums import ReadingMode
    from ndr.storage.cache import fingerprint

    person = ConfirmedCharacter(
        "c1", "小舟", source="MODEL", user_confirmed=False, confirmation_source="automatic",
    )
    state = SceneState(confirmed_characters=[person], book_characters=[person], pov_character_id="c1",
                       participants=[SpeakerSlot("S1", "q1", character_id="c1")])
    window = SimpleNamespace(target_quote_ids=(), fragments=(), fragment_ids=(),
                             reading_mode=ReadingMode(reading_mode), policy_version="test-policy",
                             dependency_hash="test-dependency", visible_horizon_cp=0)
    job = Job(range_json=json.dumps({"reading_mode": reading_mode}))
    version = SimpleNamespace(id="book-version")
    before_key = _cache_key_for(job=job, version=version, window=window, snapshot=None, state=state)
    messages = _messages_for(window=window, state=state, locked_summary=None)
    params = task_parameters(messages)
    expected = {"source": "MODEL", "user_confirmed": False, "confirmation_source": "automatic"}
    for row in [*params["confirmed_chapter_characters"], *params["known_book_characters"],
                params["pov_character"]]:
        assert {key: row[key] for key in expected} == expected
    assert params["existing_speakers"][0]["identity_provenance"] == expected
    restored = SceneState.from_snapshot(state.snapshot())
    assert restored.confirmed_characters == [person]
    state.confirmed_characters = [replace(person, user_confirmed=True, confirmation_source="manual")]
    changed = _messages_for(window=window, state=state, locked_summary=None)
    assert fingerprint(messages) != fingerprint(changed)
    after_key = _cache_key_for(job=job, version=version, window=window, snapshot=None, state=state)
    assert before_key != after_key
    assert "model/automatic 不等于人工核实" in messages[0]["content"]
