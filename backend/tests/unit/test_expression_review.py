"""Original expression fixtures; compiler legality is not semantic accuracy."""

from dataclasses import replace

import pytest

from ndr.domain.enums import AnnotationStatus
from ndr.evaluation.compact import Candidate, CompactTask
from ndr.llm.expression_review import (
    agreed_challenges,
    compile_decisions,
    reconcile,
    review_effects,
    snapshot,
    verify_payload,
)


def task():
    return CompactTask(
        ("Q1",),
        {"Q1": "q", "E1": "e"},
        (
            {"ref": "E1", "kind": "overlap", "start_cp": 0, "end_cp": 4, "text": "林舟说："},
            {"ref": "Q1", "kind": "target_quote", "start_cp": 4, "end_cp": 7, "text": "「好」"},
        ),
        (Candidate("C1", "a", "林舟"), Candidate("C2", "b", "周遥"), Candidate("C3", "c", "江雨")),
    )


def proposal(person="C1", kind="speech", *, basis="direct", scope="base"):
    payload = {
        "labels": [
            {
                "q": "Q1",
                "kind": kind,
                "character": person,
                "basis": basis if person else "insufficient",
                "evidence": ["E1"] if person else [],
            }
        ]
    }
    return snapshot(payload, task(), call_ref=scope)["decisions"]


def check(verdict="support_challenger", **changes):
    return {
        "checks": [
            {
                "q": "Q1",
                "verdict": verdict,
                "evidence": ["E1"],
                "contradiction_evidence": ["E1"],
                "reason": "原文证据核查",
                **changes,
            }
        ]
    }


@pytest.mark.parametrize("first_kind", ["speech", "thought", "quotation"])
@pytest.mark.parametrize("second_kind", ["speech", "thought", "quotation"])
def test_same_owner_survives_expression_type_disagreement(first_kind, second_kind):
    first, second = proposal(kind=first_kind), proposal(kind=second_kind, scope="review")
    resolved, reasons = reconcile(first, second)
    compiled = compile_decisions(task(), resolved)
    assert compiled.output.labels[0].kind.value == second_kind
    assert compiled.output.new_speakers[0].character_id == "a"
    assert reasons["q"] == "identity_agreement"


@pytest.mark.parametrize("kind", ["speech", "thought", "quotation"])
def test_supported_third_can_introduce_different_owner_or_recover_agreed_unknown(kind):
    third = proposal("C3", kind, scope="third")
    for first, second in (
        (proposal(), proposal("C2", scope="review")),
        (proposal(None), proposal(None, scope="review")),
    ):
        resolved, _ = reconcile(first, second, adjudicated=third)
        compiled = compile_decisions(task(), resolved)
        assert compiled.output.new_speakers[0].character_id == "c"
        assert compiled.output.labels[0].kind.value == kind


def test_weak_review_cannot_erase_existing_supported_basis():
    first = proposal()
    resolved, _ = reconcile(first, proposal(basis="style_only", scope="review"))
    assert resolved == first


@pytest.mark.parametrize("verdict", ["support_challenger", "support_consensus", "undecidable"])
def test_verified_agreement_challenge_binds_full_proposals_and_keeps_approval(verdict):
    first, second, third = proposal(), proposal(scope="review"), proposal("C3", scope="third")
    assert agreed_challenges(first, second, third) == ("q",)
    pending, _ = reconcile(first, second, adjudicated=third)
    assert compile_decisions(task(), pending).acceptance_ceilings == {
        "q": AnnotationStatus.PROVISIONAL
    }
    verified = verify_payload(
        check(verdict), task(), first, second, third, requested=("q",), verifier_ref="verifier"
    )
    resolved, _ = reconcile(
        first, second, adjudicated=third, verified=verified, task_fingerprint=task().fingerprint()
    )
    compiled = compile_decisions(task(), resolved)
    assert compiled.output.new_speakers[0].character_id == (
        "c" if verdict == "support_challenger" else "a"
    )
    assert bool(compiled.acceptance_ceilings) is (verdict == "undecidable")
    changed = {"q": replace(third["q"], evidence=("q",))}
    with pytest.raises(ValueError, match="proposals|eligible"):
        reconcile(
            first,
            second,
            adjudicated=changed,
            verified=verified,
            task_fingerprint=task().fingerprint(),
        )
    with pytest.raises(ValueError, match="task"):
        reconcile(first, second, adjudicated=third, verified=verified, task_fingerprint="different")


@pytest.mark.parametrize(
    "change",
    [
        {"contradiction_evidence": ["Q1"]},
        {"contradiction_evidence": []},
        {"evidence": ["missing"]},
        {"evidence": ["E1", "E1"]},
        {"q": "E1"},
    ],
)
def test_invalid_challenge_evidence_or_target_is_rejected(change):
    with pytest.raises(ValueError):
        verify_payload(
            check(**change),
            task(),
            proposal(),
            proposal(scope="review"),
            proposal("C3", scope="third"),
            requested=("q",),
            verifier_ref="verifier",
        )


def test_verifier_cannot_reuse_proposal_scope_and_locks_always_win():
    first, second, third = proposal(), proposal(scope="review"), proposal("C3", scope="third")
    for scope in ("base", "review", "third", ""):
        with pytest.raises(ValueError, match="Independent"):
            verify_payload(
                check(), task(), first, second, third, requested=("q",), verifier_ref=scope
            )
    resolved, reasons = reconcile(first, second, adjudicated=third, locked=first)
    assert resolved == first and reasons["q"] == "user_locked"
    assert reconcile(first, {}, adjudicated=third)[0] == first


def test_full_compile_never_promotes_pending_or_accepts_invalid_identity():
    first = proposal()
    pending = {"q": replace(first["q"], admissible=False)}
    assert compile_decisions(task(), pending).acceptance_ceilings == {
        "q": AnnotationStatus.PROVISIONAL
    }
    with pytest.raises(ValueError, match="identity"):
        compile_decisions(task(), {"q": replace(first["q"], character_id="missing")})
    with pytest.raises(ValueError, match="Complete"):
        compile_decisions(task(), {})
    with pytest.raises(KeyError):
        compile_decisions(task(), {"q": replace(first["q"], evidence=("not_sent",))})


@pytest.mark.parametrize("ref", ["G1", "EB"])
def test_challenge_cannot_use_boundaries_or_blank_evidence(ref):
    original = task()
    altered = replace(
        original,
        references={**original.references, "G1": "g", "EB": "blank"},
        context=(
            *original.context,
            {"ref": "G1", "kind": "outer_gap", "start_cp": 7, "end_cp": 8, "text": "响"},
            {"ref": "EB", "kind": "overlap", "start_cp": 8, "end_cp": 9, "text": " "},
        ),
        gap_next_quote={"G1": None},
    )
    with pytest.raises(ValueError, match="boundary|blank"):
        verify_payload(
            check(evidence=[ref]),
            altered,
            proposal(),
            proposal(scope="review"),
            proposal("C3", scope="third"),
            requested=("q",),
            verifier_ref="verifier",
        )


def test_mutated_verified_record_cannot_change_pending_approval_or_basis():
    first, second, third = proposal(), proposal(scope="review"), proposal("C3", scope="third")
    verified = verify_payload(
        check("undecidable"),
        task(),
        first,
        second,
        third,
        requested=("q",),
        verifier_ref="verifier",
    )
    broken = {
        "q": replace(verified["q"], decision=replace(verified["q"].decision, admissible=True))
    }
    with pytest.raises(ValueError, match="decision changed"):
        reconcile(
            first, second, adjudicated=third, verified=broken, task_fingerprint=task().fingerprint()
        )


def test_anonymous_scopes_do_not_establish_same_identity_from_same_name():
    payload = {
        "labels": [
            {"q": "Q1", "kind": "speech", "character": "N1", "basis": "direct", "evidence": ["E1"]}
        ],
        "new_characters": [
            {"ref": "N1", "name": "同学", "description": "路过的同学", "evidence": ["E1"]}
        ],
    }
    first = snapshot(payload, task(), call_ref="first")
    second = snapshot(payload, task(), call_ref="second")
    resolved, reasons = reconcile(first["decisions"], second["decisions"])
    assert resolved["q"].character_id is None and resolved["q"].anonymous_ref is None
    assert reasons["q"] == "unresolved_conflict"


def test_paired_effects_keep_identity_and_type_independent():
    base, changed = proposal(), proposal(kind="quotation", scope="review")
    report = review_effects({"q": "a"}, base, changed, type_gold={"q": "speech"})
    assert report["identity_harmed"] == report["identity_corrected"] == 0
    assert report["type_harmed"] == 1
    pending = {"q": replace(base["q"], admissible=False)}
    report = review_effects({"q": "a"}, base, pending)
    assert report["new_unknown"] == report["identity_harmed"] == 1
    assert report["new_wrong"] == 0
    report = review_effects({"q": "a"}, proposal("C2"), base)
    assert report["identity_corrected"] == 1


def test_initial_compile_rejects_future_identity_fields():
    with pytest.raises(ValueError, match="Future identity"):
        future = replace(
            task(),
            reading_mode="initial",
            visible_horizon_cp=7,
            candidates=(replace(task().candidates[0], visible_from_cp=9),),
        )
        compile_decisions(future, proposal())


@pytest.mark.parametrize("kind", ["group", "other", "unknown"])
def test_unowned_kinds_do_not_acquire_narrator_or_owner_fields(kind):
    result = snapshot({"labels": [{"q": "Q1", "kind": kind}]}, task(), call_ref="base")
    compiled = compile_decisions(task(), result["decisions"])
    assert compiled.output.labels[0].kind.value == kind
    assert compiled.output.labels[0].speaker_ref is None
    with pytest.raises(ValueError, match="Unowned"):
        compile_decisions(task(), {"q": replace(result["decisions"]["q"], character_id="a")})


def test_malformed_approvals_cannot_be_coerced_to_true():
    with pytest.raises(ValueError, match="approval"):
        compile_decisions(task(), {"q": replace(proposal()["q"], admissible=1)})


def test_restoring_snapshot_keeps_explicit_pending_approval_and_rejects_upgrade():
    payload = {
        "labels": [
            {"q": "Q1", "kind": "thought", "character": "C1", "basis": "direct", "evidence": ["E1"]}
        ]
    }
    restored = snapshot(payload, task(), call_ref="restored", owner_approvals={"q": False})
    assert not restored["decisions"]["q"].admissible
    assert compile_decisions(task(), restored["decisions"]).acceptance_ceilings == {
        "q": AnnotationStatus.PROVISIONAL
    }
    payload["labels"][0]["basis"] = "style_only"
    with pytest.raises(ValueError, match="upgrade"):
        snapshot(payload, task(), call_ref="restored", owner_approvals={"q": True})
