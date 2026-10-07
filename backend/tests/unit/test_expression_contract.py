"""Original offline cases for explicitly selected 1.1 domain outputs."""

from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from ndr.domain.enums import AnnotationStatus, QuoteKind
from ndr.llm.expression_contract import (
    ExpressionLlmOutput,
    ExpressionQuoteLabel,
    expression_output_json_schema,
)
from ndr.llm.schemas import QuoteLabel
from ndr.llm.validation import LabelingTargets, parse_and_validate
from ndr.scenes.acceptance import decide_acceptance

TARGETS = LabelingTargets(quote_ids=("q1",), speaker_refs=("S1",), evidence_ids=("q1", "e1"))


def label(kind="thought", **updates):
    return {
        "quote_id": "q1",
        "scene_ref": "scene_current",
        "kind": kind,
        "assignment": "EXISTING",
        "speaker_ref": "S1",
        "basis": "DIRECT",
        "evidence_refs": ["e1"],
        **updates,
    }


def validate(row, **updates):
    return parse_and_validate(
        {"schema_version": "1.1", "labels": [row], **updates},
        TARGETS,
        expected_schema_version="1.1",
    )


@pytest.mark.parametrize("kind", ["speech", "thought", "quotation"])
def test_owner_preserves_kind_and_requires_evidence(kind):
    result = validate(label(kind))
    assert result.ok, result.messages
    row = result.accepted_labels[0]
    assert row.kind is QuoteKind(kind) and row.speaker_ref == "S1"
    assert decide_acceptance(row).status is AnnotationStatus.ACCEPTED
    weak = validate(label(kind, evidence_refs=["q1"]))
    assert weak.ok
    assert decide_acceptance(weak.accepted_labels[0]).status is AnnotationStatus.PROVISIONAL


@pytest.mark.parametrize("kind", ["speech", "thought", "quotation"])
def test_unknown_has_no_guessed_person(kind):
    result = validate(
        label(kind, assignment="UNKNOWN", speaker_ref=None, basis="INSUFFICIENT", evidence_refs=[])
    )
    assert result.ok
    assert decide_acceptance(result.accepted_labels[0]).status is AnnotationStatus.UNKNOWN


@pytest.mark.parametrize(
    "updates",
    [
        {"speaker_ref": "S1"},
        {"speaker_name": "林舟"},
        {"basis": "DIRECT"},
        {"evidence_refs": ["e1"]},
    ],
)
def test_unknown_field_conflicts_are_rejected(updates):
    row = label(assignment="UNKNOWN", speaker_ref=None, basis="INSUFFICIENT", evidence_refs=[])
    row.update(updates)
    assert not validate(row).ok


@pytest.mark.parametrize("kind", ["group", "other", "unknown"])
def test_unowned_kinds_cannot_declare_one_person(kind):
    assert not validate(label(kind)).ok
    assert validate(label(kind, assignment=None, speaker_ref=None, basis=None)).ok


@pytest.mark.parametrize("kind", ["thought", "quotation"])
def test_legacy_contract_is_not_silently_upgraded(kind):
    row = label(kind)
    with pytest.raises(ValidationError):
        QuoteLabel.model_validate(row)
    assert ExpressionQuoteLabel.model_validate(row).kind is QuoteKind(kind)
    payload = {"schema_version": "1.1", "labels": [row]}
    assert not parse_and_validate(payload, TARGETS).ok
    assert not parse_and_validate(
        {**payload, "schema_version": "1.0"}, TARGETS, expected_schema_version="1.1"
    ).ok
    historical = label(kind, assignment=None, speaker_ref=None, basis=None)
    report = parse_and_validate({"schema_version": "1.0", "labels": [historical]}, TARGETS)
    assert report.ok and report.accepted_labels[0].speaker_ref is None
    typed = ExpressionLlmOutput.model_validate(payload)
    assert not parse_and_validate(typed, TARGETS).ok
    assert parse_and_validate(typed, TARGETS, expected_schema_version="1.1").ok


@pytest.mark.parametrize(
    "updates,code",
    [
        ({"speaker_ref": "missing"}, "unknown_speaker"),
        ({"evidence_refs": ["future"]}, "unknown_evidence"),
        ({"scene_ref": "unprovided"}, "unknown_scene"),
        ({"quote_id": "missing"}, "unknown_quote"),
    ],
)
def test_owner_types_use_existing_reference_guards(updates, code):
    assert code in validate(label(**updates)).error_codes


def test_coverage_and_duplicate_guards():
    payload = {"schema_version": "1.1", "labels": []}
    assert (
        "missing_targets"
        in parse_and_validate(payload, TARGETS, expected_schema_version="1.1").error_codes
    )
    payload["labels"] = [label(), deepcopy(label())]
    assert (
        "duplicate_label"
        in parse_and_validate(payload, TARGETS, expected_schema_version="1.1").error_codes
    )


@pytest.mark.parametrize("kind", ["thought", "quotation"])
def test_cross_scene_existing_person_is_not_reused(kind):
    targets = LabelingTargets(quote_ids=("q1",), gap_ids=("g1",), speaker_refs=("S1",))
    payload = {
        "schema_version": "1.1",
        "labels": [label(kind, scene_ref="scene_2")],
        "gap_decisions": [{"gap_id": "g1", "decision": "BREAK"}],
        "scene_updates": [
            {"temp_ref": "scene_2", "after_gap_id": "g1", "starts_at_quote_id": "q1"}
        ],
    }
    report = parse_and_validate(payload, targets, expected_schema_version="1.1")
    assert "old_scene_speaker" in report.error_codes


@pytest.mark.parametrize(
    "row,valid",
    [
        (label("speech"), True),
        (label("thought"), True),
        (label("quotation"), True),
        (
            label(assignment="UNKNOWN", speaker_ref=None, basis="INSUFFICIENT", evidence_refs=[]),
            True,
        ),
        (label(assignment="UNKNOWN", speaker_ref=None, basis="DIRECT", evidence_refs=[]), False),
        (label(assignment="UNKNOWN", speaker_ref=None, basis="INSUFFICIENT"), False),
        (label(speaker_ref=""), False),
        (label(assignment=None), False),
        (label(basis="INSUFFICIENT"), False),
        (label("group"), False),
        (label("other", assignment=None, speaker_ref=None, basis=None), True),
    ],
)
def test_json_schema_matches_runtime_owner_relationships(row, valid):
    schema = expression_output_json_schema()
    Draft202012Validator.check_schema(schema)
    payload = {"schema_version": "1.1", "labels": [row]}
    assert Draft202012Validator(schema).is_valid(payload) is valid
    assert validate(row).ok is valid


@pytest.mark.parametrize("kind", ["speech", "thought", "quotation"])
def test_new_contract_cannot_create_an_unnamed_person(kind):
    row = label(kind, assignment="NEW", speaker_ref="new1")
    # Legacy structural repair can fill a missing declaration, but 1.1 must not
    # persist its unnamed placeholder as a valid discovered identity.
    assert "missing_speaker_name" in validate(row).error_codes
    declaration = {
        "temp_ref": "new1",
        "scene_ref": "scene_current",
        "first_quote_id": "q1",
        "description": "原文中的店员",
    }
    assert "missing_speaker_name" in validate(row, new_speakers=[declaration]).error_codes
    declaration["name"] = "店员"
    assert validate(row, new_speakers=[declaration]).ok
