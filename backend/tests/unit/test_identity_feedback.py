"""Whole-block feedback contracts on authored text, not model quality scores."""

import json
from copy import deepcopy
from dataclasses import replace

import pytest

from ndr.evaluation.compact import Candidate, CompactTask
from ndr.llm.errors import InvalidModelOutput
from ndr.llm.identity_feedback import (
    FEEDBACK_VERSION,
    build_identity_feedback_messages,
    compile_identity_feedback,
    prepare_identity_feedback,
)


def fixture():
    task = CompactTask(
        ("Q1", "Q2"),
        {"E1": "e", "Q1": "q1", "Q2": "q2"},
        (
            {
                "ref": "E1",
                "kind": "overlap",
                "start_cp": 0,
                "end_cp": 12,
                "text": "门卫摘下帽子，原来是林舟。他说：",
            },
            {
                "ref": "Q1",
                "kind": "target_quote",
                "start_cp": 12,
                "end_cp": 16,
                "text": "「你好。」",
            },
            {
                "ref": "Q2",
                "kind": "target_quote",
                "start_cp": 16,
                "end_cp": 20,
                "text": "「请进。」",
            },
        ),
        (Candidate("C1", "person1", "林舟"), Candidate("C2", "person2", "许晴")),
        pov_ref="C2",
    )
    primary = {
        "labels": [
            {"q": q, "kind": "speech", "character": "N1", "basis": "direct", "evidence": ["E1"]}
            for q in task.quote_ids
        ],
        "new_characters": [
            {"ref": "N1", "name": "门卫", "description": "门口说话的门卫", "evidence": ["E1"]}
        ],
        "breaks": [],
    }
    proposed = deepcopy(primary)
    for row in proposed["labels"]:
        row["character"] = "C1"
    proposed["new_characters"] = []
    feedback = {
        "schema_version": FEEDBACK_VERSION,
        "proposal": proposed,
        "issues": [
            {
                "kind": "incorrect_association",
                "targets": ["Q1", "Q2"],
                "character": "C1",
                "evidence": ["E1"],
                "reason": "摘帽后的直接身份揭示，而非仅同名。",
            }
        ],
    }
    return task, primary, feedback


def compile_case(task, primary, feedback, **plan_options):
    plan = prepare_identity_feedback(task, primary, primary_source="first", **plan_options)
    return compile_identity_feedback(plan, feedback, task, source_ref="feedback-call")


def test_complete_anonymous_dependency_can_link_to_existing_identity_without_database_merge():
    task, primary, feedback = fixture()
    original = deepcopy(primary)
    plan = prepare_identity_feedback(task, primary, primary_source="first")
    result = compile_identity_feedback(plan, feedback, task, source_ref="feedback-call")
    assert {r["character"] for r in json.loads(result.proposal_json)["labels"]} == {"C1"}
    assert json.loads(result.issues_json)[0]["targets"] == ["Q1", "Q2"]
    assert result.pov_candidate is None and result.plan_fingerprint == plan.fingerprint()
    assert primary == original and task.pov_ref == "C2"
    primary["labels"][0]["character"] = "C2"
    assert json.loads(plan.original_json) == original
    assert result.fingerprint()


def test_omitted_identity_requires_a_source_supported_new_declaration():
    task, primary, feedback = fixture()
    for row in primary["labels"]:
        row.update(character=None, basis="insufficient", evidence=[])
    proposed = deepcopy(feedback["proposal"])
    for row in proposed["labels"]:
        row["character"] = "N2"
    proposed["new_characters"] = [
        {"ref": "N2", "name": "门卫", "description": "正在开口的门卫", "evidence": ["E1"]}
    ]
    primary["new_characters"] = []
    feedback["proposal"] = proposed
    feedback["issues"][0].update(kind="omitted_identity", character="N2")
    assert (
        json.loads(compile_case(task, primary, feedback).proposal_json)["new_characters"][0]["ref"]
        == "N2"
    )


def test_pov_feedback_is_only_a_candidate_and_does_not_rewrite_roster_or_expressions():
    task, primary, feedback = fixture()
    feedback["proposal"] = deepcopy(primary)
    feedback["issues"][0].update(kind="incorrect_pov", character="C1")
    result = compile_case(task, primary, feedback)
    assert result.pov_candidate == "C1"
    assert json.loads(result.proposal_json) == primary
    assert task.pov_ref == "C2"


@pytest.mark.parametrize(
    "change",
    [
        {"targets": ["Q1"]},
        {"targets": ["Q1", "Q1"]},
        {"targets": ["missing"]},
        {"character": "C99"},
        {"character": "C2"},
        {"evidence": ["missing"]},
        {"evidence": ["Q1", "Q2"]},
        {"evidence": ["E1", "E1"]},
        {"evidence": ["E1：门卫摘下帽子"]},
    ],
)
def test_invalid_feedback_identity_or_incomplete_dependency_is_rejected(change):
    task, primary, feedback = fixture()
    feedback["issues"][0].update(change)
    with pytest.raises(ValueError):
        compile_case(task, primary, feedback)


@pytest.mark.parametrize("locked", [("q1",), ("q2",), ("q1", "q2")])
def test_locked_member_protects_whole_identity_block(locked):
    task, primary, feedback = fixture()
    with pytest.raises(ValueError, match="locked"):
        compile_case(task, primary, feedback, locked_targets=locked)


@pytest.mark.parametrize("generation", [1, -1, False, None])
def test_feedback_generation_is_bounded(generation):
    task, primary, _ = fixture()
    with pytest.raises(ValueError, match="one generation"):
        prepare_identity_feedback(task, primary, primary_source="first", generation=generation)


def test_source_binding_scope_and_full_proposal_cannot_be_bypassed():
    task, primary, feedback = fixture()
    plan = prepare_identity_feedback(task, primary, primary_source="first")
    for source in ("first", "", None):
        with pytest.raises(ValueError, match="independent"):
            compile_identity_feedback(plan, feedback, task, source_ref=source)
    with pytest.raises(ValueError, match="task changed"):
        compile_identity_feedback(plan, feedback, replace(task, pov_ref="C1"), source_ref="second")
    feedback["proposal"]["labels"].pop()
    with pytest.raises((ValueError, InvalidModelOutput)):
        compile_identity_feedback(plan, feedback, task, source_ref="second")


def test_feedback_cannot_change_types_or_hide_its_main_dependency():
    task, primary, feedback = fixture()
    feedback["proposal"]["labels"][0]["kind"] = "thought"
    with pytest.raises(ValueError, match="types"):
        compile_case(task, primary, feedback)
    feedback["proposal"] = deepcopy(primary)
    feedback["issues"][0].update(kind="incorrect_pov", character="C1")
    feedback["proposal"]["labels"][0]["basis"] = "coreference"
    with pytest.raises(ValueError, match="unrelated"):
        compile_case(task, primary, feedback)


def test_actual_feedback_request_preserves_raw_context_and_distinguishes_issue_schema():
    task, primary, _ = fixture()
    plan = prepare_identity_feedback(task, primary, primary_source="first", locked_targets=("q1",))
    messages = build_identity_feedback_messages(plan, task)
    assert json.loads(messages[-1]["content"])["locked_targets"] == ["Q1"]
    assert json.loads(messages[-1]["content"])["primary_proposal"] == primary
    assert json.loads(messages[1]["content"])["context"] == list(task.context)
    schema = json.loads(messages[0]["content"].split("\n")[-1])
    issue = schema["$defs"]["IdentityIssue"]["properties"]
    assert issue["targets"]["items"]["enum"] == ["Q1", "Q2"]
    assert issue["evidence"]["items"]["enum"] == ["E1", "Q1", "Q2"]


@pytest.mark.parametrize("ref,kind,text", [("G1", "outer_gap", "后来"), ("EB", "overlap", " ")])
def test_blank_and_boundary_evidence_cannot_support_identity_feedback(ref, kind, text):
    task, primary, feedback = fixture()
    task = replace(
        task,
        references={**task.references, ref: "extra"},
        context=(
            *task.context,
            {"ref": ref, "kind": kind, "start_cp": 20, "end_cp": 22, "text": text},
        ),
    )
    feedback["issues"][0]["evidence"] = [ref]
    with pytest.raises(ValueError, match="external evidence"):
        compile_case(task, primary, feedback)
    plan = prepare_identity_feedback(task, primary, primary_source="first")
    schema = json.loads(build_identity_feedback_messages(plan, task)[0]["content"].split("\n")[-1])
    assert ref not in schema["$defs"]["IdentityIssue"]["properties"]["evidence"]["items"]["enum"]


def test_pov_candidate_cannot_be_used_to_hide_modified_anonymous_description():
    task, primary, feedback = fixture()
    feedback["issues"][0].update(kind="incorrect_pov", character="C1")
    feedback["proposal"] = deepcopy(primary)
    feedback["proposal"]["new_characters"][0]["description"] = "另一个身份的说明"
    with pytest.raises(ValueError, match="unrelated identity"):
        compile_case(task, primary, feedback)


def test_duplicate_issues_or_missing_new_identity_are_not_silently_salvaged():
    task, primary, feedback = fixture()
    feedback["issues"].append(deepcopy(feedback["issues"][0]))
    with pytest.raises(ValueError, match="Overlapping"):
        compile_case(task, primary, feedback)
    feedback["issues"].pop()
    feedback["issues"][0].update(kind="omitted_identity", character="C1")
    with pytest.raises(ValueError, match="newly declared"):
        compile_case(task, primary, feedback)


def test_empty_scope_cannot_build_empty_schema_enumerations():
    task, _, _ = fixture()
    with pytest.raises(ValueError, match="expression targets"):
        prepare_identity_feedback(
            replace(task, quote_ids=()), {"labels": []}, primary_source="first"
        )
