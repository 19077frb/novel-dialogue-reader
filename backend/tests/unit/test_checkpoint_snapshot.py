import json

from ndr.scenes.state import ConfirmedCharacter, SceneState


def test_projected_checkpoint_omits_rebuildable_facts_without_copying_them(monkeypatch):
    state = SceneState(
        confirmed_characters=[ConfirmedCharacter(
            character_id="c", canonical_name="林舟", relations=({"to": "other"},),
            identity_records=({"description": "synthetic" * 10000},),
        )], projected_identity_input=True,
    )
    full = state.snapshot()
    def no_deepcopy(_):
        raise AssertionError("checkpoint copied rebuildable identity facts")
    monkeypatch.setattr("ndr.scenes.state.deepcopy", no_deepcopy)
    compact = state.checkpoint_snapshot()
    assert "identity_records" not in compact["confirmed_characters"][0]
    assert "relations" not in compact["confirmed_characters"][0]
    assert compact["confirmed_characters"][0]["character_id"] == "c"
    assert len(json.dumps(compact)) < len(json.dumps(full)) / 10
    restored = SceneState.from_snapshot(compact)
    assert restored.confirmed_characters[0].character_id == "c"


def test_legacy_checkpoint_keeps_complete_character_state():
    state = SceneState(confirmed_characters=[ConfirmedCharacter(
        character_id="c", canonical_name="林舟", identity_records=({"fact": "synthetic"},),
    )])
    assert state.checkpoint_snapshot() == state.snapshot()
    assert state.checkpoint_snapshot()["confirmed_characters"][0]["identity_records"]
