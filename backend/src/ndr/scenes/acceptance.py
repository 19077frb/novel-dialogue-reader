"""接受策略与可见时点（DEVELOPMENT.md 4.5 第 4～5 步）。

冷启动采用保守策略（校准前始终按冷启动处理，参数留给 T16）：

- 直接且无冲突的归属（`basis=DIRECT`）→ `ACCEPTED`。
- 指代/承接（`COREFERENCE`/`RESPONSE_LINK`）与风格单一依据（`STYLE_ONLY`）
  → `PROVISIONAL` 并记录待确认。
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

ACCEPTANCE_POLICY_VERSION = "acceptance-1"


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

    if label.kind is not QuoteKind.SPEECH:
        # 类型判断（心声、引用、集体声音…）不带说话人，接受它不会影响人物色彩。
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
        return AcceptanceDecision(status=ACCEPTED, reason="direct_evidence")
    # COREFERENCE / RESPONSE_LINK：校准前保持暂定并记录
    if cold_start:
        return AcceptanceDecision(
            status=PROVISIONAL,
            reason=f"cold_start_{basis.value.lower()}",
            needs_review=True,
            review_reason=ReviewReason.AMBIGUOUS_SPEAKER,
        )
    return AcceptanceDecision(status=ACCEPTED, reason=f"calibrated_{basis.value.lower()}")


def compute_visible_from_cp(*, evidence_positions: list[int], fallback_cp: int) -> int:
    """可见时点 = 证据中最靠后的位置（没有证据时退回到本窗口起点）。

    后文才出现的身份证据因此不会提前生效——初读 projection（T15）据此避免提前同色。
    """

    if not evidence_positions:
        return fallback_cp
    return max(evidence_positions)


def is_direct_identity_evidence(*, basis: SpeakerBasis | None) -> bool:
    """身份合并/拆分是否具备“直接证据”（决定能否自动应用，见 T09 的修订策略）。"""

    return basis in {SpeakerBasis.DIRECT, SpeakerBasis.COREFERENCE}
