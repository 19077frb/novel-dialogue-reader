"""单元测试：场景状态、接受策略、匿名分组与身份修订策略。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ndr.domain.enums import (
    AnnotationStatus,
    Assignment,
    GapDecision,
    IdentityOperation,
    QuoteKind,
    ReviewReason,
    SceneStatus,
    SpeakerBasis,
)
from ndr.llm.schemas import QuoteLabel
from ndr.scenes.acceptance import compute_visible_from_cp, decide_acceptance
from ndr.scenes.state import ConfirmedCharacter, SceneState
from ndr.speakers.groups import SpeakerRegistry
from ndr.speakers.revisions import (
    IdentityProposalView,
    evaluate_identity_proposal,
)


def _speech_label(**overrides) -> QuoteLabel:
    payload = {
        "quote_id": "q1",
        "scene_ref": "scene_current",
        "kind": "speech",
        "assignment": "EXISTING",
        "speaker_ref": "S1",
        "basis": "DIRECT",
        "evidence_refs": [],
    }
    payload.update(overrides)
    return QuoteLabel.model_validate(payload)


# ---------- 场景状态 ----------


def test_update_does_not_close_scene() -> None:
    state = SceneState(scene_id="sc1", participants=[])
    registry = SpeakerRegistry(state)
    registry.register_temp_speaker(temp_ref="new1", first_quote_id="q1")

    transition = state.apply_gap(
        decision=GapDecision.UPDATE,
        gap_id="g1",
        next_quote_id="q2",
        next_quote_start_cp=50,
        position_cp=40,
    )

    assert transition.closed_scene is False
    assert transition.opened_scene is False
    assert state.scene_id == "sc1"  # UPDATE 不切场景
    assert state.status is SceneStatus.OPEN
    assert [slot.display_label for slot in state.participants] == ["S1"]  # 参与者保留


def test_break_closes_scene_and_starts_a_new_one() -> None:
    state = SceneState(scene_id="sc1", start_cp=0)
    registry = SpeakerRegistry(state)
    registry.register_temp_speaker(temp_ref="new1", first_quote_id="q1")

    transition = state.apply_gap(
        decision=GapDecision.BREAK,
        gap_id="g9",
        next_quote_id="q5",
        next_quote_start_cp=200,
        position_cp=180,
    )

    assert transition.closed_scene is True and transition.opened_scene is True
    assert state.scene_id is None  # 新场景待落库
    assert state.start_cp == 200
    assert state.end_cp is None
    assert state.status is SceneStatus.OPEN
    assert state.participants == []  # 新场景重新编号
    assert state.recent_turns == []
    assert state.last_quote_id == "q5"
    assert state.version == 2


def test_confirmed_character_memory_survives_scene_break() -> None:
    state = SceneState(scene_id="sc1")
    SpeakerRegistry(state).register_temp_speaker(
        temp_ref="new1",
        first_quote_id="q1",
        canonical_name="绫濑沙季",
        description="义妹",
    )
    state.apply_gap(
        decision=GapDecision.BREAK,
        gap_id="g1",
        next_quote_id="q2",
        next_quote_start_cp=100,
        position_cp=90,
    )
    assert state.participants == []
    assert state.known_characters == {"绫濑沙季": "义妹"}


def test_confirmed_roster_is_catalog_and_active_speakers_keep_unique_labels() -> None:
    characters = [
        ConfirmedCharacter(f"c{index}", name, description=f"user-confirmed-{index}")
        for index, name in enumerate(
            ("Yuta", "Saki", "Maaya", "Maru"),
            start=1,
        )
    ]
    state = SceneState(confirmed_characters=characters)
    state.sync_confirmed_participants()

    # A chapter roster does not mean that everybody is present in every scene.
    assert state.participants == []

    for index, character in enumerate(characters, start=1):
        slot = state.add_speaker(
            first_quote_id=f"q{index}",
            canonical_name=character.canonical_name,
            description="model-description",
        )
        assert slot.character_id == character.character_id

    assert [slot.display_label for slot in state.participants] == ["S1", "S2", "S3", "S4"]
    assert len({slot.display_label for slot in state.participants}) == 4

    # Reconcile duplicate labels and restore user-confirmed identity metadata.
    state.participants[1].display_label = "S1"
    state.participants[1].description = "wrong-model-description"
    state.sync_confirmed_participants()
    assert [slot.display_label for slot in state.participants] == ["S1", "S2", "S3", "S4"]
    assert state.participants[1].description == "user-confirmed-2"

    state.apply_gap(
        decision=GapDecision.BREAK,
        gap_id="g1",
        next_quote_id="q5",
        next_quote_start_cp=100,
        position_cp=90,
    )
    assert state.participants == []


def test_uncertain_keeps_scene_open_with_pending_boundary() -> None:
    state = SceneState(scene_id="sc1")
    registry = SpeakerRegistry(state)
    registry.register_temp_speaker(temp_ref="new1", first_quote_id="q1")

    transition = state.apply_gap(
        decision=GapDecision.UNCERTAIN,
        gap_id="g3",
        next_quote_id=None,
        next_quote_start_cp=None,
        position_cp=100,
    )

    assert transition.pending_boundary is True
    assert state.status is SceneStatus.PENDING_BOUNDARY
    assert state.scene_id == "sc1"
    assert state.participants  # 沉默不是离场
    assert state.unresolved == ["g3"]


def test_snapshot_round_trip_preserves_state() -> None:
    state = SceneState(scene_id="sc1", start_cp=10, last_quote_id="q3")
    registry = SpeakerRegistry(state)
    slot = registry.register_temp_speaker(
        temp_ref="new1", first_quote_id="q1", description="义妹", canonical_name="绫濑沙季"
    )
    slot.group_id = "g-1"
    state.remember_turn(quote_id="q1", slot=slot)
    state.unresolved.append("g7")

    restored = SceneState.from_snapshot(state.snapshot())

    assert restored.scene_id == "sc1"
    assert restored.start_cp == 10
    assert restored.last_quote_id == "q3"
    assert restored.unresolved == ["g7"]
    assert [item.display_label for item in restored.participants] == ["S1"]
    assert restored.participants[0].group_id == "g-1"
    assert restored.participants[0].description == "义妹"
    assert restored.participants[0].canonical_name == "绫濑沙季"
    assert restored.known_characters == {"绫濑沙季": "义妹"}
    assert restored.recent_turns == [
        {"quote_id": "q1", "speaker_ref": "S1", "speaker_name": "绫濑沙季"}
    ]
    assert "最近已确认轮次=S1:绫濑沙季" in restored.prompt_state()
    assert restored.label_map()["new1"] == "S1"
    assert restored.snapshot()["state_version"] == "scene-state-5"


def test_prompt_state_is_bounded() -> None:
    state = SceneState(scene_id="sc1")
    registry = SpeakerRegistry(state)
    for index in range(5):
        registry.register_temp_speaker(
            temp_ref=f"new{index}", first_quote_id=f"q{index}", description="很长的说明" * 5
        )
    text = state.prompt_state(max_chars=80)
    assert len(text) <= 80
    assert "S1" in text


def test_labels_follow_first_speech_order_and_reuse_gaps() -> None:
    state = SceneState()
    registry = SpeakerRegistry(state)
    first = registry.register_temp_speaker(temp_ref="new1", first_quote_id="q1")
    second = registry.register_temp_speaker(temp_ref="new2", first_quote_id="q2")

    assert (first.display_label, second.display_label) == ("S1", "S2")
    state.participants.remove(first)
    third = registry.register_temp_speaker(temp_ref="new3", first_quote_id="q3")
    assert third.display_label == "S1"  # 编号只在场景内有意义，空出的编号可复用
    assert registry.label_for("new2") == "S2"
    # 同一 temp_ref 重复声明不会创建第二个分组
    assert registry.register_temp_speaker(temp_ref="new2", first_quote_id="q2") is second


# ---------- 接受策略 ----------


def test_direct_speech_requires_independent_evidence() -> None:
    decision = decide_acceptance(_speech_label(evidence_refs=["p1"]))
    assert decision.status is AnnotationStatus.ACCEPTED
    assert decision.needs_review is False

    for evidence in ([], ["q1"]):
        unverified = decide_acceptance(_speech_label(evidence_refs=evidence))
        assert unverified.status is AnnotationStatus.PROVISIONAL
        assert unverified.needs_review is True
        assert unverified.reason == "unverified_direct_basis"


def test_linked_speech_is_accepted_but_style_and_empty_evidence_need_review() -> None:
    style = decide_acceptance(_speech_label(basis="STYLE_ONLY"))
    assert style.status is AnnotationStatus.PROVISIONAL
    assert style.needs_review is True
    assert style.review_reason is ReviewReason.LOW_CONFIDENCE

    for basis in ("COREFERENCE", "RESPONSE_LINK"):
        linked = decide_acceptance(_speech_label(basis=basis, evidence_refs=["q2"]))
        assert linked.status is AnnotationStatus.ACCEPTED
        assert linked.needs_review is False

        unverified = decide_acceptance(_speech_label(basis=basis, evidence_refs=[]))
        assert unverified.status is AnnotationStatus.PROVISIONAL
        assert unverified.needs_review is True


def test_unknown_and_insufficient_never_create_people() -> None:
    unknown = decide_acceptance(
        _speech_label(assignment="UNKNOWN", speaker_ref=None, basis="INSUFFICIENT")
    )
    assert unknown.status is AnnotationStatus.UNKNOWN
    assert unknown.needs_review is True
    assert unknown.review_reason is ReviewReason.UNKNOWN_SPEAKER


def test_non_speech_types_are_accepted_without_speaker() -> None:
    for kind in ("thought", "quotation", "group"):
        label = QuoteLabel.model_validate(
            {
                "quote_id": "q9",
                "scene_ref": "scene_current",
                "kind": kind,
                "assignment": None,
                "speaker_ref": None,
                "basis": None,
                "evidence_refs": [],
            }
        )
        decision = decide_acceptance(label)
        assert decision.status is AnnotationStatus.ACCEPTED
        assert decision.reason == "non_speech_type"


def test_unknown_kind_is_not_counted_as_accepted() -> None:
    label = QuoteLabel.model_validate(
        {
            "quote_id": "q9",
            "scene_ref": "scene_current",
            "kind": "unknown",
            "assignment": None,
            "speaker_ref": None,
            "basis": None,
            "evidence_refs": [],
        }
    )
    decision = decide_acceptance(label)
    assert decision.status is AnnotationStatus.UNKNOWN
    assert decision.needs_review is True


def test_request_aliases_are_cleared_between_windows() -> None:
    state = SceneState()
    first = SpeakerRegistry(state).register_temp_speaker(temp_ref="new1", first_quote_id="q1")
    first.group_id = "stable-1"
    state.clear_request_aliases()
    second = SpeakerRegistry(state).register_temp_speaker(temp_ref="new1", first_quote_id="q2")
    assert second is not first
    assert first.temp_ref is None
    assert second.first_quote_id == "q2"


def test_visible_from_uses_latest_evidence_position() -> None:
    assert compute_visible_from_cp(evidence_positions=[10, 55, 30], fallback_cp=5) == 55
    assert compute_visible_from_cp(evidence_positions=[], fallback_cp=42) == 42


# ---------- 身份修订策略 ----------


def _proposal(**overrides) -> IdentityProposalView:
    payload = {
        "operation": IdentityOperation.MERGE,
        "input_refs": ("S1", "S2"),
        "output_refs": ("S1",),
        "evidence_refs": ("p9",),
        "basis": SpeakerBasis.DIRECT,
        "visible_from_cp": 900,
    }
    payload.update(overrides)
    return IdentityProposalView(**payload)


def test_merge_applies_only_with_explicit_evidence() -> None:
    assert evaluate_identity_proposal(_proposal(), touches_locked=False).applied is True
    assert (
        evaluate_identity_proposal(_proposal(basis=SpeakerBasis.STYLE_ONLY), touches_locked=False).applied
        is False
    )
    assert evaluate_identity_proposal(_proposal(basis=None), touches_locked=False).applied is False


def test_merge_deferred_when_touching_locked_or_on_cold_start_coreference() -> None:
    locked = evaluate_identity_proposal(_proposal(), touches_locked=True)
    assert locked.applied is False
    assert locked.reason == "touches_user_locked"
    assert locked.needs_review is True

    coref = evaluate_identity_proposal(
        _proposal(basis=SpeakerBasis.COREFERENCE), touches_locked=False, cold_start=True
    )
    assert coref.applied is False
    assert coref.needs_review is True


def test_split_arity_is_enforced() -> None:
    bad = evaluate_identity_proposal(
        _proposal(operation=IdentityOperation.SPLIT, input_refs=("S1",), output_refs=("S1",)),
        touches_locked=False,
    )
    assert bad.applied is False
    assert bad.reason == "split_needs_two_outputs"

    good = evaluate_identity_proposal(
        _proposal(operation=IdentityOperation.SPLIT, input_refs=("S1",), output_refs=("S1", "S2")),
        touches_locked=False,
    )
    assert good.applied is True


@pytest.mark.parametrize("kind", [QuoteKind.SPEECH, QuoteKind.THOUGHT])
def test_schema_enforces_assignment_rules(kind: QuoteKind) -> None:
    if kind is QuoteKind.SPEECH:
        with pytest.raises(ValidationError):
            QuoteLabel.model_validate(
                {
                    "quote_id": "q1",
                    "scene_ref": "scene_current",
                    "kind": "speech",
                    "assignment": None,
                    "speaker_ref": None,
                    "basis": "DIRECT",
                }
            )
    else:
        # 非 speech 带 assignment 必须被拒绝：类型判断不能污染普通人物分组
        with pytest.raises(ValidationError):
            QuoteLabel.model_validate(
                {
                    "quote_id": "q1",
                    "scene_ref": "scene_current",
                    "kind": "thought",
                    "assignment": Assignment.UNKNOWN,
                    "speaker_ref": None,
                    "basis": None,
                }
            )
        label = QuoteLabel.model_validate(
            {
                "quote_id": "q1",
                "scene_ref": "scene_current",
                "kind": "thought",
                "assignment": None,
                "speaker_ref": None,
                "basis": None,
            }
        )
        assert label.assignment is None
