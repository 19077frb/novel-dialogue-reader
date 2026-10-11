"""Non-person text is not an unknown person; actual quotes retain owner checks."""
import pytest

from ndr.domain.enums import ReviewReason
from ndr.llm.expression_contract import ExpressionQuoteLabel
from ndr.llm.nonperson_policy import (
    NONPERSON_POLICY,
    NONPERSON_POLICY_LEGACY,
    NONPERSON_POLICY_VERSION,
    nonperson_policy,
)
from ndr.llm.prompts.labeling import SYSTEM_PROMPT
from ndr.scenes.acceptance import decide_acceptance


@pytest.mark.parametrize(("kind", "reason"), [
    ("speech", ReviewReason.UNKNOWN_SPEAKER),
    ("thought", ReviewReason.UNKNOWN_SPEAKER),
    ("quotation", ReviewReason.UNKNOWN_QUOTE_SOURCE),
    ("unknown", ReviewReason.UNKNOWN_QUOTE_KIND),
    ("other", None),
])
def test_nonperson_and_unknown_are_different(kind, reason):
    row = {"quote_id": "q1", "scene_ref": "s1", "kind": kind}
    if kind in {"speech", "thought", "quotation"}:
        row.update(assignment="UNKNOWN", basis="INSUFFICIENT")
    result = decide_acceptance(ExpressionQuoteLabel.model_validate(row))
    assert result.review_reason == reason
    assert result.needs_review == (reason is not None)


def test_shared_policy_preserves_real_expression_and_short_text_boundaries():
    assert NONPERSON_POLICY in SYSTEM_PROMPT
    assert "人物来源不明不等于other" in NONPERSON_POLICY
    assert "只有省略号/破折号" in NONPERSON_POLICY


def test_a_known_person_quote_stays_accepted():
    label = ExpressionQuoteLabel.model_validate({
        "quote_id": "q1", "scene_ref": "s1", "kind": "quotation",
        "assignment": "EXISTING", "speaker_ref": "S1", "basis": "COREFERENCE",
        "evidence_refs": ["q2"],
    })
    assert not decide_acceptance(label).needs_review


def test_classification_guidance_is_versioned_not_a_blanket_unknown_quote_filter():
    assert nonperson_policy(None) == NONPERSON_POLICY_LEGACY
    assert nonperson_policy(NONPERSON_POLICY_VERSION) == NONPERSON_POLICY
    assert "具体人物设想的话判quotation" in NONPERSON_POLICY
    assert "泛指社会观念" in NONPERSON_POLICY
    with pytest.raises(ValueError):
        nonperson_policy("unrecognized")


def test_absent_policy_version_preserves_projected_task_fingerprint_and_messages():
    from dataclasses import dataclass, replace

    from ndr.evaluation.compact import CompactTask
    from ndr.evaluation.expression_owner import ExplicitOwnerProtocol
    from ndr.llm.expression_task import ProjectedCompactTask

    @dataclass(frozen=True)
    class LegacyProjectedTask(CompactTask):
        effective_profiles: tuple[dict, ...] = ()
        auxiliary_protocol: str | None = None
        identity_prompt_version: str | None = None

    args = (("Q1",), {"Q1": "quote1"},
            ({"ref": "Q1", "kind": "target_quote", "text": "「术语」"},), (), {})
    legacy, current = LegacyProjectedTask(*args), ProjectedCompactTask(*args)
    assert current.fingerprint() == legacy.fingerprint()
    assert ExplicitOwnerProtocol(current).system == ExplicitOwnerProtocol(legacy).system
    newer = replace(current, nonperson_policy_version=NONPERSON_POLICY_VERSION)
    assert newer.fingerprint() != current.fingerprint()
    assert NONPERSON_POLICY in ExplicitOwnerProtocol(newer).system
