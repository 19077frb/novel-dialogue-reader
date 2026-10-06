"""Identity fields and restored memory share one initial-reading boundary."""

import json
from types import SimpleNamespace

import pytest

from ndr.characters.input_view import project_identity_state
from ndr.domain.enums import ReadingMode
from ndr.scenes.state import ConfirmedCharacter, SceneState, SpeakerSlot
from ndr.storage.models import BookCharacter


def _person(records=None, history=None):
    return BookCharacter(id="c1", book_version_id="v1", canonical_name="未来姓名",
                         aliases_json='["未来别名"]', description="未来说明", source="MODEL",
                         user_confirmed=False, identity_facts_json=json.dumps(records or []),
                         presentation_history_json=json.dumps(history or []))


def _fact(kind, value, cp):
    return {"kind": kind, "value": value, "visible_from_cp": cp, "canonical_sha256": "a" * 64,
            "source": "user", "source_ref": "manual", "accepted": True}


@pytest.mark.parametrize("mode", [ReadingMode.INITIAL, ReadingMode.REREAD])
def test_projection_filters_every_field_and_restored_memory_without_mutating_person(mode):
    person = _person([_fact("designation", "少女", 10), _fact("name", "未来姓名", 90),
                      _fact("alias", "未来别名", 90), _fact("description", "未来说明", 90),
                      _fact("relation", "已知关系", 12), _fact("relation", "未来关系", 90)])
    version = SimpleNamespace(id="v1", canonical_sha256="a" * 64, canonical_length_cp=100)
    state = SceneState(confirmed_characters=[ConfirmedCharacter("c1", "未来姓名")],
                       participants=[SpeakerSlot("S1", "q1", character_id="c1",
                                                 canonical_name="未来姓名", description="未来说明"),
                                     SpeakerSlot("S2", "q2", canonical_name="无证据名字")],
                       known_characters={"未来姓名": "未来说明"},
                       recent_turns=[{"speaker_ref": "S1", "speaker_name": "未来姓名"}])
    project_identity_state(state, [person], version, reading_mode=mode,
                           horizon=30 if mode is ReadingMode.INITIAL else 100)
    rendered = json.dumps(state.snapshot(), ensure_ascii=False)
    assert "无证据名字" not in rendered
    if mode is ReadingMode.INITIAL:
        assert all(value not in rendered for value in ("未来姓名", "未来别名", "未来说明"))
        assert state.confirmed_characters[0].canonical_name == "少女"
        assert "未来关系" not in rendered and "已知关系" in rendered
        assert state.confirmed_characters[0].aliases == ()
    else:
        assert all(value in rendered for value in ("未来姓名", "未来别名", "未来说明"))
        assert "未来关系" in rendered and "已知关系" in rendered
    assert state.participants[0].character_id == "c1"
    assert person.canonical_name == "未来姓名" and person.description == "未来说明"


def test_legacy_history_does_not_invent_alias_history_or_fall_back_to_future_fields():
    person = _person(history=[{"cp": 10, "name": "少女", "description": "旧说明"},
                              {"cp": 90, "name": "未来姓名", "description": "未来说明"}])
    version = SimpleNamespace(id="v1", canonical_sha256="a" * 64, canonical_length_cp=100)
    state = SceneState()
    visible = project_identity_state(state, [person], version, reading_mode=ReadingMode.INITIAL,
                                     horizon=30)["c1"]
    assert visible.canonical_name == "少女" and visible.description == "旧说明"
    assert visible.aliases == ()
    hidden = project_identity_state(state, [person], version, reading_mode=ReadingMode.INITIAL,
                                    horizon=0)["c1"]
    assert hidden.canonical_name == "" and hidden.description == ""
    assert hidden.character_id == "c1"


def test_visible_alias_reset_and_merge_gate_apply_to_model_input():
    records = [_fact("designation", "少女", 10), _fact("alias", "旧别名", 12),
               {"kind": "profile_update", "field": "aliases", "value": [],
                "visible_from_cp": 20, "canonical_sha256": "a" * 64,
                "source": "user", "source_ref": "reset", "accepted": True},
               {**_fact("name", "合并后的名字", 10), "identity_links": [{
                   "source_id": "old", "target_id": "c1", "visible_from_cp": 80,
                   "source": "user", "source_ref": "merge", "accepted": True}]}]
    version = SimpleNamespace(id="v1", canonical_sha256="a" * 64, canonical_length_cp=100)
    projected = project_identity_state(SceneState(), [_person(records)], version,
                                        reading_mode=ReadingMode.INITIAL, horizon=30)["c1"]
    assert projected.canonical_name == "少女" and projected.aliases == ()
    assert all(r["value"] != "旧别名" and r["value"] != "合并后的名字"
               for r in projected.identity_records)


@pytest.mark.parametrize("accepted", [True, False])
def test_identity_provenance_preserves_model_source_and_transfer_reveal(accepted):
    record = {**_fact("name", "林舟", 4), "source": "model", "source_ref": "model-call",
              "evidence_spans": [[0, 4]], "identity_links": [{
                  "source_id": "old", "target_id": "c1", "visible_from_cp": 80,
                  "source": "user", "source_ref": "merge", "accepted": accepted}]}
    person = _person([record])
    version = SimpleNamespace(id="v1", canonical_sha256="a" * 64, canonical_length_cp=100)
    for horizon in (30, 100):
        state = SceneState()
        projected = project_identity_state(state, [person], version,
                                           reading_mode=ReadingMode.INITIAL, horizon=horizon)["c1"]
        if accepted and horizon == 100:
            assert projected.identity_records == ({"kind": "name", "value": "林舟",
                "source": "model", "source_ref": "model-call", "visible_from_cp": 80,
                "evidence_spans": [[0, 4]]},)
            state.confirmed_characters = [projected]
            restored = SceneState.from_snapshot(state.snapshot()).confirmed_characters[0]
            assert restored.identity_records == projected.identity_records
            exported = projected.as_dict()
            exported["identity_records"][0]["evidence_spans"][0][1] = 90
            assert projected.identity_records[0]["evidence_spans"] == [[0, 4]]
        else:
            assert projected.identity_records == ()
            assert "identity_records" not in projected.as_dict()


def test_legacy_metadata_does_not_acquire_fabricated_sources():
    person = _person()
    version = SimpleNamespace(id="v1", canonical_sha256="a" * 64, canonical_length_cp=100)
    projected = project_identity_state(SceneState(), [person], version,
                                       reading_mode=ReadingMode.REREAD, horizon=100)["c1"]
    assert projected.canonical_name == "未来姓名"
    assert projected.identity_records == () and "identity_records" not in projected.as_dict()


def test_same_role_identities_are_not_collapsed_into_name_only_prompt_memory():
    first = _person(history=[{"cp": 0, "name": "女同学", "description": "左边的人"}])
    second = _person(history=[{"cp": 0, "name": "女同学", "description": "右边的人"}])
    second.id = "c2"
    state = SceneState(confirmed_characters=[ConfirmedCharacter("c1", "女同学"),
                                            ConfirmedCharacter("c2", "女同学")])
    version = SimpleNamespace(id="v1", canonical_sha256="a" * 64, canonical_length_cp=100)
    project_identity_state(state, [first, second], version,
                           reading_mode=ReadingMode.INITIAL, horizon=30)
    state.sync_confirmed_participants()
    assert state.known_characters == {}
    assert {p.character_id for p in state.identity_characters} == {"c1", "c2"}
    assert {p.description for p in state.identity_characters} == {"左边的人", "右边的人"}


@pytest.mark.parametrize("bad", ['{}', '[{"kind":"bad"}]'])
def test_invalid_ledger_is_not_replaced_with_current_metadata(bad):
    person = _person()
    person.identity_facts_json = bad
    version = SimpleNamespace(id="v1", canonical_sha256="a" * 64, canonical_length_cp=100)
    with pytest.raises(ValueError):
        project_identity_state(SceneState(), [person], version,
                               reading_mode=ReadingMode.INITIAL, horizon=30)
