"""身份修订（merge/split）的接受策略。

保守规则：

- 自动应用只限**明确证据**（`DIRECT` / `COREFERENCE`）且**不触碰任何人工锁定**的分组；
- 其余情况只记录提议，进入待确认队列，绝不悄悄改写历史；
- 可见时点取证据中最靠后的位置：后文才揭示的身份不会在初读时提前生效。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.enums import IdentityOperation, SpeakerBasis


@dataclass(frozen=True)
class IdentityProposalView:
    operation: IdentityOperation
    input_refs: tuple[str, ...]
    output_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...] = ()
    basis: SpeakerBasis | None = None
    visible_from_cp: int | None = None


@dataclass(frozen=True)
class IdentityDecision:
    applied: bool
    reason: str
    needs_review: bool = False


EXPLICIT_BASES = {SpeakerBasis.DIRECT, SpeakerBasis.COREFERENCE}


def evaluate_identity_proposal(
    proposal: IdentityProposalView,
    *,
    touches_locked: bool,
    cold_start: bool = True,
) -> IdentityDecision:
    """决定是否自动应用一次身份合并/拆分。"""

    if touches_locked:
        return IdentityDecision(
            applied=False, reason="touches_user_locked", needs_review=True
        )
    if proposal.basis is None:
        return IdentityDecision(applied=False, reason="missing_basis", needs_review=True)
    if proposal.basis not in EXPLICIT_BASES:
        return IdentityDecision(
            applied=False,
            reason=f"evidence_too_weak:{proposal.basis.value}",
            needs_review=True,
        )
    if proposal.operation is IdentityOperation.MERGE and len(proposal.input_refs) < 2:
        return IdentityDecision(applied=False, reason="merge_needs_two_inputs", needs_review=True)
    if proposal.operation is IdentityOperation.SPLIT and len(proposal.output_refs) < 2:
        return IdentityDecision(applied=False, reason="split_needs_two_outputs", needs_review=True)
    if cold_start and proposal.basis is SpeakerBasis.COREFERENCE:
        # 冷启动阶段连指代推断也先记录待确认，避免把两个声音过早合并。
        return IdentityDecision(applied=False, reason="cold_start_coreference", needs_review=True)
    return IdentityDecision(applied=True, reason=f"explicit_{proposal.basis.value.lower()}")
