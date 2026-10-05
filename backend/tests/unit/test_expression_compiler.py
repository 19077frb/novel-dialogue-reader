"""Original fixtures for the short-to-domain boundary, not model quality."""

from copy import deepcopy
from dataclasses import replace

import pytest

from ndr.domain.enums import AnnotationStatus, Assignment, QuoteKind
from ndr.evaluation.compact import Candidate, CompactTask
from ndr.evaluation.owner_constraints import ConstrainedOwnerProtocol
from ndr.llm.errors import InvalidModelOutput
from ndr.llm.expression_compiler import compile_expression_output


def task():
    return CompactTask(
        ("Q1", "Q2"),
        {"Q1": "q1", "G1": "g1", "E1": "e1", "Q2": "q2"},
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


def payload(kind="speech", character="C1", basis="direct"):
    return {
        "labels": [
            {
                "q": q,
                "kind": kind,
                "character": character,
                "basis": basis if character else "insufficient",
                "evidence": ["E1"] if character else [],
            }
            for q in ("Q1", "Q2")
        ]
    }


@pytest.mark.parametrize("kind", ["speech", "thought", "quotation"])
def test_complete_domain_compilation_preserves_kind_and_creation_order(kind):
    current = task()
    raw = payload(kind)
    before = deepcopy(raw)
    compiled = compile_expression_output(raw, current)
    output = compiled.output
    assert output.schema_version == "1.1" and raw == before
    assert [r.kind for r in output.labels] == [QuoteKind(kind)] * 2
    assert [r.assignment for r in output.labels] == [Assignment.NEW, Assignment.EXISTING]
    assert output.labels[0].speaker_ref == output.labels[1].speaker_ref
    assert output.new_speakers[0].character_id == "person"
    assert not compiled.acceptance_ceilings
    output.labels[0].speaker_ref = "modified-copy"
    assert compiled.output.labels[0].speaker_ref != "modified-copy"


@pytest.mark.parametrize("kind", ["speech", "thought", "quotation"])
def test_unknown_keeps_no_person_and_no_approval_upgrade(kind):
    raw = payload(kind, None)
    output = compile_expression_output(raw, task()).output
    assert not output.new_speakers and not any(r.speaker_ref for r in output.labels)
    assert all(r.assignment is Assignment.UNKNOWN for r in output.labels)
    with pytest.raises(ValueError, match="cannot upgrade"):
        compile_expression_output(raw, task(), owner_approvals={"q1": True, "q2": True})


def test_programme_approval_keeps_original_basis_and_changes_fingerprint():
    raw = payload()
    approved = compile_expression_output(raw, task())
    held = compile_expression_output(raw, task(), owner_approvals={"q1": False, "q2": True})
    assert held.acceptance_ceilings == {"q1": AnnotationStatus.PROVISIONAL}
    assert approved.output_json == held.output_json
    assert approved.fingerprint() != held.fingerprint()


@pytest.mark.parametrize(
    "approvals",
    [{}, {"q1": False}, {"q1": 0, "q2": True}, {"q1": False, "q2": True, "outside": False}],
)
def test_approval_is_an_explicit_complete_programme_table(approvals):
    with pytest.raises(ValueError, match="Complete explicit"):
        compile_expression_output(payload(), task(), owner_approvals=approvals)


def test_weak_owner_cannot_be_promoted_by_the_caller():
    raw = payload(basis="style_only")
    compiled = compile_expression_output(raw, task())
    assert set(compiled.acceptance_ceilings) == {"q1", "q2"}
    with pytest.raises(ValueError, match="cannot upgrade"):
        compile_expression_output(raw, task(), owner_approvals={"q1": True, "q2": False})


def test_new_anonymous_identity_is_programme_numbered_and_keeps_description():
    current = replace(task(), candidates=(), pov_ref=None)
    raw = payload("thought", "N1")
    raw["new_characters"] = [
        {"ref": "N1", "name": "林舟", "description": "原文中的说话者", "evidence": ["E1"]}
    ]
    output = compile_expression_output(raw, current).output
    assert len(output.new_speakers) == 1
    assert output.new_speakers[0].character_id is None
    assert output.new_speakers[0].name == "林舟"
    assert output.new_speakers[0].description == "原文中的说话者"


def test_break_redeclares_same_identity_in_a_new_scene():
    current = replace(task(), candidates=(replace(task().candidates[0], existing_ref="S1"),))
    boundary = next(iter(ConstrainedOwnerProtocol(current).guard.guard.view["gap_next_quote"]))
    raw = payload("quotation")
    raw["breaks"] = [boundary]
    output = compile_expression_output(raw, current).output
    assert output.labels[0].assignment is Assignment.EXISTING
    assert output.labels[0].speaker_ref == "S1"
    assert output.labels[1].assignment is Assignment.NEW
    assert output.labels[1].scene_ref != output.labels[0].scene_ref


@pytest.mark.parametrize("evidence", ["G1", "UNSENT"])
def test_invalid_evidence_rejects_the_entire_block(evidence):
    raw = payload()
    raw["labels"][0]["evidence"] = [evidence]
    with pytest.raises(InvalidModelOutput):
        compile_expression_output(raw, task())


@pytest.mark.parametrize(
    "field,value", [("name", "未来姓名"), ("aliases", ("未来别名",)), ("description", "未来描述")]
)
def test_initial_fields_must_match_visible_facts(field, value):
    facts = ({"candidate": "C1", "kind": "name", "value": "林舟", "visible_from_cp": 12},)
    initial = replace(task(), reading_mode="initial", visible_horizon_cp=16, identity_facts=facts)
    assert compile_expression_output(payload(), initial).output.schema_version == "1.1"
    changed = replace(initial, candidates=(replace(initial.candidates[0], **{field: value}),))
    with pytest.raises(ValueError, match="Initial candidate"):
        compile_expression_output(payload(), changed)
