"""接受策略与可见时点。

接受策略按证据种类区分：

- `basis=DIRECT` 仍需至少一个候选自身之外的证据；只有自引或无证据时进入 `PROVISIONAL`。
- 指代/承接（`COREFERENCE`/`RESPONSE_LINK`）带有可回溯引用时接受，空证据进入待确认。
- 风格单一依据（`STYLE_ONLY`）始终进入待确认。
- 证据不足（`INSUFFICIENT`）或 `assignment=UNKNOWN` → `UNKNOWN`，**绝不因此新建人物分组**。
- 非 speech 的类型判断（心声/引用/集体声音…）可以接受，但按契约不带 speaker_ref，
  因此不会污染普通人物色彩。

可见时点由**后端**按证据位置计算：模型自报的 visible_from 不可信
（schema 也禁止出现该字段）。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.enums import (
    AnnotationStatus,
    Assignment,
    QuoteKind,
    ReviewReason,
    SpeakerBasis,
)
from ..llm.schemas import QuoteLabel

ACCEPTANCE_POLICY_VERSION = "acceptance-4"


@dataclass(frozen=True)
class AcceptanceDecision:
    status: AnnotationStatus
    reason: str
    needs_review: bool = False
    review_reason: ReviewReason | None = None


ACCEPTED = AnnotationStatus.ACCEPTED
PROVISIONAL = AnnotationStatus.PROVISIONAL
UNKNOWN = AnnotationStatus.UNKNOWN


def decide_acceptance(label: QuoteLabel, *, cold_start: bool = True) -> AcceptanceDecision:
    """按证据类型与冷启动策略决定标注状态。"""

    if label.kind is QuoteKind.UNKNOWN:
        return AcceptanceDecision(
            status=UNKNOWN,
            reason="unknown_quote_kind",
            needs_review=True,
            review_reason=ReviewReason.LOW_CONFIDENCE,
        )
    if label.kind is not QuoteKind.SPEECH:
        # 已明确的心声、引用、集体声音等不带说话人，不污染普通人物色彩。
        return AcceptanceDecision(status=ACCEPTED, reason="non_speech_type")

    basis = label.basis or SpeakerBasis.INSUFFICIENT
    if label.assignment is Assignment.UNKNOWN or basis is SpeakerBasis.INSUFFICIENT:
        return AcceptanceDecision(
            status=UNKNOWN,
            reason="insufficient_evidence",
            needs_review=True,
            review_reason=ReviewReason.UNKNOWN_SPEAKER,
        )
    if basis is SpeakerBasis.STYLE_ONLY:
        return AcceptanceDecision(
            status=PROVISIONAL,
            reason="style_only_basis",
            needs_review=True,
            review_reason=ReviewReason.LOW_CONFIDENCE,
        )
    if basis is SpeakerBasis.DIRECT:
        # 只把候选自身列为证据无法验证说话人；先进入确认队列，避免模型自报 DIRECT 即全绿。
        if not label.evidence_refs or set(label.evidence_refs) <= {label.quote_id}:
            return AcceptanceDecision(
                status=PROVISIONAL,
                reason="unverified_direct_basis",
                needs_review=True,
                review_reason=ReviewReason.LOW_CONFIDENCE,
            )
        return AcceptanceDecision(status=ACCEPTED, reason="direct_evidence")
    if basis in {SpeakerBasis.COREFERENCE, SpeakerBasis.RESPONSE_LINK}:
        # 指代与问答承接是轻小说中最常见的省略主语证据。模型必须至少给出一个
        # 可回溯引用；空证据仍进入确认队列，避免把纯轮流猜测自动接受。
        if label.evidence_refs:
            return AcceptanceDecision(
                status=ACCEPTED,
                reason=f"linked_{basis.value.lower()}",
            )
        return AcceptanceDecision(
            status=PROVISIONAL,
            reason=f"unverified_{basis.value.lower()}",
            needs_review=True,
            review_reason=ReviewReason.AMBIGUOUS_SPEAKER,
        )
    return AcceptanceDecision(
        status=PROVISIONAL,
        reason=f"unsupported_{basis.value.lower()}",
        needs_review=True,
        review_reason=ReviewReason.AMBIGUOUS_SPEAKER,
    )


def compute_visible_from_cp(*, evidence_positions: list[int], fallback_cp: int) -> int:
    """可见时点 = 证据中最靠后的位置（没有证据时退回到本窗口起点）。

    后文才出现的身份证据因此不会提前生效——初读 projection据此避免提前同色。
    """

    if not evidence_positions:
        return fallback_cp
    return max(evidence_positions)


def is_direct_identity_evidence(*, basis: SpeakerBasis | None) -> bool:
    """身份合并/拆分是否具备“直接证据”。"""

    return basis in {SpeakerBasis.DIRECT, SpeakerBasis.COREFERENCE}
