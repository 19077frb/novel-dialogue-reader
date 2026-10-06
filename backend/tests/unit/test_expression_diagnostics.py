"""Original auxiliary-isolation fixtures, not semantic quality claims."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from ndr.domain.enums import Assignment, QuoteKind
from ndr.evaluation.compact import Candidate, CompactTask
from ndr.llm.errors import InvalidModelOutput
from ndr.llm.expression_compiler import compile_expression_output
from ndr.llm.expression_diagnostics import compile_expression_diagnostics


def task():
    return CompactTask(
        ("Q1", "Q2", "Q3"),
        {"Q1": "q1", "Q2": "q2", "Q3": "q3", "G1": "g", "E1": "e"},
        tuple(
            {"ref": q, "text": text, "start_cp": i * 8, "end_cp": i * 8 + len(text)}
            for i, (q, text) in enumerate(
                (
                    ("Q1", "「好」"),
                    ("Q2", "「来」"),
                    ("Q3", "「是」"),
                    ("G1", "\n\n"),
                    ("E1", "阮青说。"),
                )
            )
        ),
        (Candidate("C1", "person", "阮青"), Candidate("C2", "other", "程墨")),
    )


def payload(kind="speech"):
    return {
        "labels": [
            {"q": q, "kind": kind, "character": "C1", "basis": "direct", "evidence": ["E1"]}
            for q in ("Q1", "Q2", "Q3")
        ]
    }


@pytest.mark.parametrize("kind", ["speech", "thought", "quotation"])
@pytest.mark.parametrize("proof", [["not-sent"], ["G1"], ["Q1", "Q1"], "Q1", []])
def test_independent_bad_auxiliary_preserves_complete_main_result(kind, proof):
    raw = payload(kind)
    expected = compile_expression_output(raw, task())
    raw["labels"][0].update(addressee="C999", addressee_evidence=proof)
    before = deepcopy(raw)
    result = compile_expression_diagnostics(raw, task())
    assert raw == before and result.compilation.output_json == expected.output_json
    assert not result.quarantined_targets and not result.diagnostics[0]["valid"]
    assert all(r.kind is QuoteKind(kind) for r in result.compilation.output.labels)
    copied = result.diagnostics
    copied.clear()
    assert result.diagnostics
    with pytest.raises(ValidationError):
        compile_expression_output(raw, task())  # Legacy stays strict.


def test_dependency_closure_holds_related_quote_but_preserves_independent_target():
    raw = payload()
    raw["labels"][0].update(addressee="C999", owner_depends_on_addressee=True)
    raw["labels"][1].update(basis="response_link", evidence=["Q1"])
    result = compile_expression_diagnostics(raw, task())
    assert result.quarantined_targets == ("Q1", "Q2")
    assert [r.assignment for r in result.compilation.output.labels] == [
        Assignment.UNKNOWN,
        Assignment.UNKNOWN,
        Assignment.NEW,
    ]
    assert result.compilation.output.new_speakers[0].first_quote_id == "q3"


def test_shared_new_identity_is_an_atomic_dependency_block():
    raw = payload()
    for row in raw["labels"][:2]:
        row["character"] = "N1"
    raw["new_characters"] = [
        {"ref": "N1", "name": "门卫", "description": "门口的门卫", "evidence": ["E1"]}
    ]
    raw["labels"][0].update(addressee="C999", owner_depends_on_addressee=True)
    result = compile_expression_diagnostics(raw, task())
    assert result.quarantined_targets == ("Q1", "Q2")
    assert len(result.compilation.output.new_speakers) == 1
    assert result.compilation.output.new_speakers[0].character_id == "person"


@pytest.mark.parametrize(
    "change", ["identity", "evidence", "duplicate", "missing", "extra", "dependency"]
)
def test_main_defects_or_invalid_dependency_cannot_hide_as_diagnostics(change):
    raw = payload()
    raw["labels"][0].update(addressee="C999", owner_depends_on_addressee=True)
    if change == "identity":
        raw["labels"][0]["character"] = "C404"
    elif change == "evidence":
        raw["labels"][0]["evidence"] = ["not-sent"]
    elif change == "duplicate":
        raw["labels"].append(deepcopy(raw["labels"][0]))
    elif change == "missing":
        raw["labels"].pop()
    elif change == "extra":
        raw["labels"][0]["invented_main_field"] = "bad"
    else:
        raw["labels"][0]["owner_depends_on_addressee"] = "false"
    with pytest.raises((ValueError, ValidationError, InvalidModelOutput)):
        compile_expression_diagnostics(raw, task())


def test_valid_auxiliary_fingerprint_and_result_are_explicit_and_deterministic():
    raw = payload()
    plain = compile_expression_diagnostics(raw, task())
    raw["labels"][0].update(
        addressee="C2", addressee_evidence=["Q1"], owner_depends_on_addressee=True
    )
    result = compile_expression_diagnostics(raw, task())
    assert result.diagnostics[0]["valid"] and not result.quarantined_targets
    assert result.compilation.output_json == plain.compilation.output_json
    assert result.fingerprint() != plain.fingerprint()
    assert result.fingerprint() == compile_expression_diagnostics(raw, task()).fingerprint()


@pytest.mark.parametrize("proof", [{"not": "a list"}, ["Q1"] * 65, [None], [42]])
def test_malformed_auxiliary_stays_bounded_and_does_not_bypass_primary(proof):
    raw = payload()
    expected = compile_expression_output(raw, task())
    raw["labels"][0].update(addressee="C2", addressee_evidence=proof)
    result = compile_expression_diagnostics(raw, task())
    assert result.compilation.output_json == expected.output_json
    assert result.diagnostics == [
        {
            "q": "Q1",
            "valid": False,
            "errors": ["invalid_auxiliary_evidence_list"],
            "owner_depends_on_addressee": False,
        }
    ]


def test_unowned_auxiliary_is_invalid_without_assigning_a_narrator():
    raw = payload()
    raw["labels"][0] = {"q": "Q1", "kind": "other", "addressee": "C2", "addressee_evidence": ["Q1"]}
    result = compile_expression_diagnostics(raw, task())
    assert result.diagnostics[0]["errors"] == ["auxiliary_on_unowned_expression"]
    assert result.compilation.output.labels[0].assignment is None
    assert result.compilation.output.labels[0].speaker_ref is None
    assert result.compilation.output.labels[0].kind is QuoteKind.OTHER
    assert not result.quarantined_targets


def test_json_input_and_dict_input_preserve_the_same_fingerprint():
    import json

    raw = payload()
    raw["labels"][0].update(addressee="C999", owner_depends_on_addressee=True)
    first = compile_expression_diagnostics(raw, task())
    second = compile_expression_diagnostics(json.dumps(raw), task())
    assert first.fingerprint() == second.fingerprint()
    assert first.compilation.output_json == second.compilation.output_json
