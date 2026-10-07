"""Non-person text is not an unknown person; actual quotes retain owner checks."""
import pytest

from ndr.domain.enums import ReviewReason
from ndr.llm.expression_contract import ExpressionQuoteLabel
from ndr.llm.nonperson_policy import NONPERSON_POLICY
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
