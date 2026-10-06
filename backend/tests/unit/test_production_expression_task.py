"""Server-bound profiles and reveal histories on original synthetic fixtures."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from ndr.characters.input_view import project_identity_state
from ndr.characters.visibility import identity_presentation_history
from ndr.domain.enums import ReadingMode
from ndr.llm.expression_compiler import compile_expression_output
from ndr.llm.expression_task import build_production_expression_task
from ndr.llm.validation import LabelingTargets, parse_and_validate
from ndr.scenes.runner import _validate_expression_task, run_window
from ndr.scenes.state import SceneState
from ndr.storage.models import BookCharacter


def fixture():
    version = SimpleNamespace(id="v", canonical_sha256="a" * 64, canonical_length_cp=100)
    def fact(kind, value, cp):
        return {"kind": kind, "value": value, "visible_from_cp": cp,
                "canonical_sha256": version.canonical_sha256, "source": "user",
                "source_ref": "original-fixture", "accepted": True}
    records = [fact("designation", "少女", 0), fact("alias", "旧称呼", 1),
               {**fact("profile_update", [], 2), "field": "aliases"},
               {**fact("profile_update", "林舟", 4), "field": "name"},
               fact("description", "旧说明", 0),
               {**fact("profile_update", "新说明", 4), "field": "description"},
               fact("name", "未来姓名", 90)]
    person = BookCharacter(id="c", book_version_id="v", canonical_name="未来姓名",
        description="未来说明", aliases_json='["未来别名"]', identity_facts_json=json.dumps(records),
        source="USER", user_confirmed=True, presentation_history_json="[]")
    window = SimpleNamespace(target_quote_ids=("q",), fragments=[
        SimpleNamespace(fragment_id="e", kind=SimpleNamespace(value="overlap"),
                        start_cp=0, end_cp=5, text="少女说：\n"),
        SimpleNamespace(fragment_id="q", kind=SimpleNamespace(value="target_quote"),
                        start_cp=5, end_cp=8, text="「好」"),
    ])
    state = SceneState()
    project_identity_state(state, [person], version, reading_mode=ReadingMode.INITIAL, horizon=8)
    return person, version, window, state


def test_effective_manual_fields_are_not_fabricated_literal_facts():
    _, _, window, state = fixture()
    task = build_production_expression_task(window, state)
    candidate = task.candidates[0]
    assert (candidate.name, candidate.aliases, candidate.description) == ("林舟", (), "新说明")
    assert task.identity_facts == ()
    assert "未来姓名" not in json.dumps(task.messages(), ensure_ascii=False)
    _validate_expression_task(task, window, state, None)
    result = compile_expression_output({"labels": [{"q": "Q1", "kind": "thought",
        "character": "C1", "basis": "direct", "evidence": ["E1"]}]}, task)
    assert result.output.new_speakers[0].character_id == "c"
    assert result.output.new_speakers[0].description == "新说明"


def test_stale_profile_or_horizon_is_rejected_before_dispatch():
    _, _, window, state = fixture()
    task = build_production_expression_task(window, state)
    with pytest.raises(ValueError, match="effective profile"):
        replace(task, candidates=(replace(task.candidates[0], name="未来姓名"),)).validate_effective_profiles()
    with pytest.raises(ValueError, match="visibility view"):
        _validate_expression_task(replace(task, visible_horizon_cp=100), window, state, None)
    restored = SceneState.from_snapshot(state.snapshot())
    with pytest.raises(ValueError, match="server-projected"):
        build_production_expression_task(window, restored)


def test_legacy_invocation_clears_previous_production_exemptions():
    _, _, window, state = fixture()
    state.production_expression_task = build_production_expression_task(window, state)
    # The precondition fails before any provider or database access. Even this
    # failed legacy invocation cannot leave production exemptions attached.
    with pytest.raises(ValueError, match="explicit expression task"):
        asyncio.run(run_window(None, adapter=None, window=window, state=state,
            book_version_id="v", quote_positions={"q": (5, 8)}, owner_approvals={"q": True}))
    assert state.production_expression_task is None


def test_display_history_keeps_effective_updates_and_future_reveal_times():
    person, version, _, _ = fixture()
    history = identity_presentation_history(person, version)
    assert [(r["cp"], r["name"], r["description"]) for r in history] == [
        (0, "少女", "旧说明"), (4, "林舟", "新说明"), (90, "未来姓名", "新说明"),
    ]


def test_known_blank_name_does_not_exempt_new_or_unprovided_identities():
    person, version, window, state = fixture()
    person.identity_facts_json = "[]"
    person.presentation_history_json = "[]"
    project_identity_state(state, [person], version, reading_mode=ReadingMode.INITIAL, horizon=8)
    task = build_production_expression_task(window, state)
    compiled = compile_expression_output({"labels": [{"q": "Q1", "kind": "speech",
        "character": "C1", "basis": "style_only", "evidence": ["E1"]}]}, task)
    payload = compiled.output.model_dump(mode="json")
    assert payload["new_speakers"][0]["name"] is None
    targets = LabelingTargets(quote_ids=("q",), scene_refs=(task.scene_ref,),
        character_ids=("c",), evidence_ids=("q", "e"), require_display_names=True,
        known_declaration_ids=("c",))
    assert parse_and_validate(payload, targets, expected_schema_version="1.1").ok
    for invalid_id in (None, "unprovided"):
        changed = json.loads(json.dumps(payload))
        changed["new_speakers"][0]["character_id"] = invalid_id
        report = parse_and_validate(changed, targets, expected_schema_version="1.1")
        assert not report.ok and "missing_speaker_name" in report.error_codes


def test_same_designation_keeps_distinct_stable_identity_bindings():
    person, version, window, state = fixture()
    other = BookCharacter(id="c2", book_version_id="v", canonical_name=person.canonical_name,
        identity_facts_json=person.identity_facts_json, source="USER", user_confirmed=True,
        aliases_json=person.aliases_json, description=person.description)
    project_identity_state(state, [other, person], version, reading_mode=ReadingMode.INITIAL, horizon=8)
    task = build_production_expression_task(window, state)
    assert [c.name for c in task.candidates] == ["林舟", "林舟"]
    assert [c.character_id for c in task.candidates] == ["c", "c2"]
    for ref, identity in (("C1", "c"), ("C2", "c2")):
        result = compile_expression_output({"labels": [{"q": "Q1", "kind": "speech",
            "character": ref, "basis": "style_only", "evidence": ["E1"]}]}, task)
        assert result.output.new_speakers[0].character_id == identity


@pytest.mark.parametrize("accepted", [True, False])
def test_display_history_gates_transferred_facts_on_merge_reveal(accepted):
    person, version, _, _ = fixture()
    person.identity_facts_json = json.dumps([{"kind": "name", "value": "源姓名",
        "visible_from_cp": 0, "canonical_sha256": version.canonical_sha256,
        "source": "user", "source_ref": "fixture", "accepted": True,
        "identity_links": [{"source_id": "source", "target_id": person.id,
            "visible_from_cp": 40, "source": "user", "source_ref": "merge",
            "accepted": accepted}]}])
    history = identity_presentation_history(person, version)
    assert history[0]["name"] == ""
    assert len(history) == (2 if accepted else 1)
    if accepted:
        assert history[-1]["cp"] == 40 and history[-1]["name"] == "源姓名"
