"""Authored fixtures: local warnings do not upgrade evidence or invent identities."""

from copy import deepcopy

import pytest

from ndr.domain.enums import AnnotationStatus, Assignment
from ndr.evaluation.compact import Candidate, CompactTask
from ndr.llm.errors import InvalidModelOutput
from ndr.llm.expression_compiler import compile_expression_output
from ndr.llm.expression_recovery import recover_expression_output


def task():
    return CompactTask(
        ("Q1", "Q2", "Q3"),
        {"Q1": "quote1", "G1": "gap1", "E1": "proof", "Q2": "quote2", "Q3": "quote3"},
        (
            {"ref": "Q1", "kind": "target_quote", "text": "「来吧」"},
            {"ref": "G1", "kind": "inner_gap", "text": " "},
            {"ref": "E1", "kind": "overlap", "text": "林舟说："},
            {"ref": "Q2", "kind": "target_quote", "text": "「好啊」"},
            {"ref": "Q3", "kind": "target_quote", "text": "「再见」"},
        ),
        (Candidate("C1", "person", "林舟", existing_ref="S1"),), {"G1": "Q2"},
    )


def payload():
    return {"labels": [
        {"q": q, "kind": "speech", "character": "C1", "basis": "direct", "evidence": ["E1"]}
        for q in task().quote_ids
    ]}


@pytest.mark.parametrize("proof", ["G1", "B1", "UNSENT", "Q1"])
@pytest.mark.parametrize("kind", ["speech", "thought", "quotation"])
def test_bad_proof_keeps_candidate_as_unapproved_and_other_rows_intact(proof, kind):
    raw = payload()
    raw["labels"][0].update(kind=kind, evidence=[proof])
    frozen = deepcopy(raw)
    with pytest.raises(InvalidModelOutput):
        compile_expression_output(raw, task())
    result = recover_expression_output(raw, task())
    first, *rest = result.compilation.output.labels
    assert first.kind.value == kind
    assert first.speaker_ref and first.evidence_refs == ["quote1"]
    assert result.compilation.acceptance_ceilings == {"quote1": AnnotationStatus.PROVISIONAL}
    assert set(result.warnings) == {"quote1"}
    assert all(row.evidence_refs == ["proof"] for row in rest)
    assert raw == frozen


@pytest.mark.parametrize("change", [
    {"character": "C99"}, {"character": "N1"}, {"kind": []},
    {"character": "C1", "basis": "insufficient"}, {"character": None},
])
def test_invalid_identity_or_shape_never_creates_an_identity(change):
    raw = payload()
    raw["labels"][0].update(change)
    result = recover_expression_output(raw, task())
    first = result.compilation.output.labels[0]
    if change.get("character") == "C1":
        assert first.speaker_ref == "S1"
        assert result.compilation.acceptance_ceilings == {"quote1": AnnotationStatus.PROVISIONAL}
    else:
        assert not first.speaker_ref
        assert first.assignment in (None, Assignment.UNKNOWN)
    assert set(result.warnings) == {"quote1"}


@pytest.mark.parametrize("duplicate", [False, True])
def test_missing_and_duplicate_target_are_unknown_not_guessed(duplicate):
    raw = payload()
    if duplicate:
        raw["labels"].append(deepcopy(raw["labels"][0]))
    else:
        raw["labels"].pop(0)
    result = recover_expression_output(raw, task())
    assert result.compilation.output.labels[0].kind.value == "unknown"
    assert set(result.warnings) == {"quote1"}


def test_bad_new_identity_is_unknown_and_dependent_rows_are_not_accepted():
    raw = payload()
    raw["new_characters"] = [
        {"ref": "N1", "name": "门卫", "description": "门口的工作人员", "evidence": ["G1"]}
    ]
    raw["labels"][0]["character"] = "N1"
    raw["labels"][1].update(basis="coreference", evidence=["Q1"])
    result = recover_expression_output(raw, task())
    assert set(result.warnings) == {"quote1", "quote2"}
    assert result.compilation.output.labels[0].assignment is Assignment.UNKNOWN
    assert result.compilation.output.labels[1].speaker_ref
    assert result.compilation.output.labels[2].evidence_refs == ["proof"]
    assert all(person.character_id == "person" for person in result.compilation.output.new_speakers)


@pytest.mark.parametrize("change", [
    {"breaks": ["UNSENT"]}, {"breaks": ["B1", "B1"]}, {"labels": []},
    {"unexpected": "value"}, {"needs_context": ["Q99"]},
])
def test_global_structure_is_still_rejected(change):
    raw = payload()
    raw["labels"][0]["evidence"] = ["G1"]
    raw.update(change)
    with pytest.raises(InvalidModelOutput):
        recover_expression_output(raw, task())


def test_unsent_target_is_not_silently_removed():
    raw = payload()
    raw["labels"][0]["q"] = "Q99"
    with pytest.raises(InvalidModelOutput):
        recover_expression_output(raw, task())


def test_undeclared_auxiliary_fields_are_local_warnings_not_silently_accepted():
    raw = payload()
    raw["labels"][0]["addressee"] = "C1"
    result = recover_expression_output(raw, task())
    assert set(result.warnings) == {"quote1"}
    assert result.compilation.acceptance_ceilings == {"quote1": AnnotationStatus.PROVISIONAL}
