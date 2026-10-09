"""Original auxiliary-isolation fixtures, not semantic quality claims."""

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict, replace

import pytest
from pydantic import ValidationError

from ndr.domain.enums import Assignment, QuoteKind
from ndr.evaluation import compact
from ndr.evaluation.compact import Candidate, CompactTask
from ndr.evaluation.owner_constraints import ConstrainedOwnerProtocol
from ndr.llm.errors import InvalidModelOutput
from ndr.llm.expression_compiler import compile_expression_output
from ndr.llm.expression_diagnostics import DIAGNOSTICS_VERSION, compile_expression_diagnostics
from ndr.llm.expression_review import snapshot
from ndr.llm.expression_task import ProjectedCompactTask


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


def production_task(version=DIAGNOSTICS_VERSION):
    base = task()
    return ProjectedCompactTask(
        **{field: getattr(base, field) for field in base.__dataclass_fields__},
        effective_profiles=tuple(
            {
                "character_id": c.character_id,
                "canonical_name": c.name,
                "aliases": list(c.aliases),
                "description": c.description,
            }
            for c in base.candidates
        ),
        auxiliary_protocol=version,
    )


@pytest.mark.parametrize("invalid", [[], {}, 7, None])
def test_malformed_main_target_stays_a_validation_failure(invalid):
    raw = payload()
    raw["labels"][0]["q"] = invalid
    with pytest.raises(ValueError, match="string target"):
        compile_expression_output(raw, production_task())


@pytest.mark.parametrize("kind", ["speech", "thought", "quotation"])
@pytest.mark.parametrize("dependent", [False, True])
def test_production_dispatch_normalizes_review_and_downgrades_only_dependents(kind, dependent):
    raw = payload(kind)
    raw["labels"][0].update(addressee="C999", owner_depends_on_addressee=dependent)
    before = deepcopy(raw)
    projected = production_task()
    compiled = compile_expression_output(
        raw, projected, owner_approvals={f"q{i}": True for i in range(1, 4)}
    )
    assert raw == before
    assert compiled.output.labels[0].kind is QuoteKind(kind)
    assert compiled.output.labels[0].assignment is (
        Assignment.UNKNOWN if dependent else Assignment.NEW
    )
    assert dict(compiled.owner_approvals)["q1"] is (not dependent)
    assert all(
        row.speaker_ref and row.assignment is not Assignment.UNKNOWN
        for row in compiled.output.labels[1:]
    )
    assert "addressee" not in compiled.normalized_payload_json
    assert compiled.auxiliary_warnings
    reviewed = snapshot(raw, projected, call_ref="primary")
    assert "addressee" not in json.dumps(reviewed["primary_payload"])
    assert reviewed["auxiliary_warnings"] == compiled.auxiliary_warnings
    changed = deepcopy(raw)
    changed["labels"][0]["addressee"] = "C998"
    assert compile_expression_output(changed, projected).fingerprint() != compiled.fingerprint()


def test_production_legacy_fingerprint_and_schema_remain_exactly_compatible():
    old = production_task(None)
    fields = asdict(old)
    fields.pop("auxiliary_protocol")
    fields.pop("identity_prompt_version")  # Absent from the historical task dataclass.
    original = {
        "protocol": compact.PROTOCOL_VERSION,
        "prompt": compact.PROMPT_VERSION,
        "compiler": compact.COMPILER_VERSION,
        "task": fields,
    }
    assert (
        old.fingerprint()
        == hashlib.sha256(
            json.dumps(original, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
    )
    new = replace(old, auxiliary_protocol=DIAGNOSTICS_VERSION)
    assert new.fingerprint() != old.fingerprint()
    assert (
        "addressee" not in ConstrainedOwnerProtocol(old).schema["$defs"]["OwnerLabel"]["properties"]
    )
    assert "addressee" in ConstrainedOwnerProtocol(new).schema["$defs"]["OwnerLabel"]["properties"]
    with pytest.raises(ValueError, match="Unsupported auxiliary"):
        compile_expression_output(payload(), replace(old, auxiliary_protocol="invalid"))


def test_isolation_cannot_hide_an_illegal_owner_approval_upgrade():
    raw = payload()
    raw["labels"][0].update(character=None, basis="insufficient", evidence=[], addressee="C999")
    with pytest.raises(ValueError, match="cannot upgrade"):
        compile_expression_output(
            raw, production_task(), owner_approvals={f"q{i}": True for i in range(1, 4)}
        )
