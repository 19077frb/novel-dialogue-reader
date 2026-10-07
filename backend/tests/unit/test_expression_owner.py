"""Original fixtures for the opt-in expression-owner compiler, not model quality."""

from copy import deepcopy
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from ndr.evaluation.compact import Candidate, CompactTask
from ndr.evaluation.evidence import EvidenceIndex, EvidencePerson, IdentityFact
from ndr.evaluation.expression_owner import ExplicitOwnerProtocol
from ndr.evaluation.owner_constraints import ConstrainedOwnerProtocol
from ndr.evaluation.owner_scoring import score_owners
from ndr.llm.errors import InvalidModelOutput


def task():
    return CompactTask(
        ("Q1", "Q2"),
        {"Q1": "quote:0:4", "G1": "gap:4:7", "E1": "evidence:7:12", "Q2": "quote:12:16"},
        (
            {"ref": "Q1", "kind": "target_quote", "text": "「来吧」", "start_cp": 0, "end_cp": 4},
            {"ref": "G1", "kind": "inner_gap", "text": "\n\n　", "start_cp": 4, "end_cp": 7},
            {"ref": "E1", "kind": "overlap", "text": "林舟说：\n", "start_cp": 7, "end_cp": 12},
            {"ref": "Q2", "kind": "target_quote", "text": "「好啊」", "start_cp": 12, "end_cp": 16},
        ),
        (Candidate("C1", "person", "林舟"),),
        {"G1": "Q2"},
        pov_ref="C1",
    )


def payload(kind="speech", character="C1"):
    return {
        "labels": [
            {
                "q": q,
                "kind": kind,
                "character": character,
                "basis": "direct" if character else "insufficient",
                "evidence": ["E1"] if character else [],
            }
            for q in ("Q1", "Q2")
        ]
    }


@pytest.mark.parametrize("kind", ["speech", "thought", "quotation"])
def test_kind_does_not_erase_validated_owner_or_penalise_score(kind):
    data = payload(kind)
    before = deepcopy(data)
    output = ExplicitOwnerProtocol(task()).compile(data)
    assert data == before
    assert output["production_submission_allowed"] is False
    assert [r["kind"] for r in output["rows"]] == [kind, kind]
    assert all(r["character_id"] == "person" and r["admissible"] for r in output["rows"])
    scored = score_owners({"quote:0:4": "person", "quote:12:16": "person"}, output["rows"])
    assert scored["identity_scores"]["correct"] == 2
    assert scored["type_is_not_identity_gate"] is True


@pytest.mark.parametrize("kind", ["speech", "thought", "quotation"])
def test_unknown_owner_is_not_filled_from_pov(kind):
    rows = ExplicitOwnerProtocol(task()).compile(payload(kind, None))["rows"]
    assert all(r["character_id"] is None and not r["admissible"] for r in rows)
    assert (
        score_owners({"quote:0:4": "person", "quote:12:16": "person"}, rows)["identity_scores"][
            "unknown"
        ]
        == 2
    )


@pytest.mark.parametrize("evidence", ["G1", "UNSENT"])
def test_blank_or_unsent_evidence_rejects_complete_proposal(evidence):
    data = payload()
    data["labels"][0]["evidence"] = [evidence]
    with pytest.raises(InvalidModelOutput):
        ExplicitOwnerProtocol(task()).compile(data)


def test_incomplete_duplicate_and_diagnostic_extras_are_not_silently_accepted():
    protocol = ExplicitOwnerProtocol(task())
    missing = payload()
    missing["labels"].pop()
    duplicate = payload()
    duplicate["labels"][1] = deepcopy(duplicate["labels"][0])
    for data in (missing, duplicate):
        with pytest.raises(InvalidModelOutput):
            protocol.compile(data)
    extras = payload()
    extras["labels"][0]["addressee"] = "C1"
    with pytest.raises(ValidationError):
        protocol.compile(extras)


def test_wrong_unsupported_and_missing_owners_remain_in_denominator():
    expected = {"a": "person", "b": "person", "c": "person"}
    rows = [
        {"quote_id": "a", "kind": "thought", "character_id": "wrong", "admissible": True},
        {"quote_id": "b", "kind": "quotation", "character_id": "person", "admissible": False},
    ]
    result = score_owners(expected, rows)["identity_scores"]
    assert (result["correct"], result["incorrect"], result["unknown"], result["missing"]) == (
        0,
        1,
        1,
        1,
    )
    with pytest.raises(ValueError):
        score_owners(expected, [*rows, rows[0]])


@pytest.mark.parametrize("kind", ["speech", "thought", "quotation"])
def test_constrained_schema_and_compiler_preserve_unknown_and_known_owners(kind):
    protocol = ConstrainedOwnerProtocol(task())
    validator = Draft202012Validator(protocol.schema)
    for character in (None, "C1"):
        data = payload(kind, character)
        before = deepcopy(data)
        validator.validate(data)
        result = protocol.compile(data)
        assert result["protocol_version"] == "explicit-owner-null-field-constraints-2"
        assert result["production_submission_allowed"] is False
        assert all(
            row["character_id"] == ("person" if character else None) for row in result["rows"]
        )
        assert data == before
    for changes in ({"basis": "direct"}, {"evidence": ["E1"]}, {"character": ""}):
        data = payload(kind, None)
        data["labels"][0].update(changes)
        assert list(validator.iter_errors(data))
        with pytest.raises(InvalidModelOutput):
            protocol.compile(data)


def test_constrained_protocol_does_not_mutate_v1_or_reuse_its_fingerprint():
    original = ExplicitOwnerProtocol(task())
    before = (deepcopy(original.schema), original.messages(), original.fingerprint())
    constrained = ConstrainedOwnerProtocol(task())
    assert (original.schema, original.messages(), original.fingerprint()) == before
    fresh = ExplicitOwnerProtocol(task())
    assert (fresh.schema, fresh.messages(), fresh.fingerprint()) == before
    assert constrained.fingerprint() != original.fingerprint()
    assert "allOf" not in original.schema["$defs"]["OwnerLabel"]
    data = payload()
    data["labels"][0]["evidence"] = ["UNSENT"]
    with pytest.raises(InvalidModelOutput):
        constrained.compile(data)


@pytest.mark.parametrize("kind", ["speech", "thought", "quotation"])
def test_initial_owner_view_filters_all_later_identity_facts(kind):
    visible = "门口的女店员招呼顾客。\n「请进。」\n\n"
    later = "女店员名叫许晴，昵称小晴，认识店长江遥。\n"
    text = visible + later
    first_line_end = visible.index("\n") + 1
    person = EvidencePerson(
        "person",
        (
            IdentityFact("女店员", "designation", first_line_end, ((0, first_line_end),)),
            IdentityFact("许晴", "name", len(text), ((len(visible), len(text)),)),
            IdentityFact("小晴", "alias", len(text), ((len(visible), len(text)),)),
            IdentityFact("认识店长江遥", "relation", len(text), ((len(visible), len(text)),)),
            IdentityFact(
                "在门口工作的许晴", "description", len(text), ((len(visible), len(text)),)
            ),
        ),
    )
    index = EvidenceIndex(text, (person,))
    span = ((visible.index("「"), visible.index("」") + 1),)
    initial_task = index.task(span, margin=0, reading_mode="initial", horizon=len(visible))
    reread_task = index.task(span, margin=0, reading_mode="reread")
    initial = ConstrainedOwnerProtocol(initial_task)
    reread = ConstrainedOwnerProtocol(reread_task)
    initial_message = str(initial.messages())
    for future in ("许晴", "小晴", "江遥", later.strip()):
        assert future not in initial_message
        assert future in str(reread.messages())
    assert initial_task.candidates[0].name == "女店员"
    assert initial_task.candidates[0].aliases == ()
    assert initial_task.candidates[0].description == ""
    assert {fact["kind"] for fact in initial_task.identity_facts} == {"designation"}
    assert "认识店长江遥" not in reread_task.candidates[0].aliases
    assert initial.fingerprint() != reread.fingerprint()
    proof = next(row["ref"] for row in initial_task.context if row["kind"] == "overlap")
    result = initial.compile(
        {
            "labels": [
                {
                    "q": "Q1",
                    "kind": kind,
                    "character": "C1",
                    "basis": "direct",
                    "evidence": [proof],
                }
            ]
        }
    )
    assert result["rows"][0]["character_id"] == "person"
    assert result["rows"][0]["kind"] == kind
    with pytest.raises(ValueError, match="Future identity in initial-reading candidates"):
        replace(
            initial_task,
            candidates=(replace(initial_task.candidates[0], visible_from_cp=len(text)),),
        )
    with pytest.raises(ValueError, match="Future identity fact in initial-reading context"):
        replace(initial_task, identity_facts=reread_task.identity_facts)
