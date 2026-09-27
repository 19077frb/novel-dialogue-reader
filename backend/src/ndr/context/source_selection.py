"""证据选择（DEVELOPMENT.md 4.3 与 PLAN 7.1 的保留层级）。

保留顺序（从高到低）：

1. **目标对白**：必留，绝不截断。
2. **目标之间的 Gap**：必留（PLAN 7.1「完整保留较短 Gap」，维持问答连续）。
3. **说话状态 / 已锁定结果**：预算内优先保留（受 ``state_tokens`` 限制）。
4. **两端少量重叠**：可选，按完整段落边界对齐。
5. **外层 Gap**：可选，默认保守保留；激进压缩默认关闭。

证据范围受阅读模式与 ``visible_horizon_cp`` 限制：``initial`` 只用 horizon 以内的原文，
``reread`` 可用整个版本。被裁掉的片段都写入省略记录（位置 + 原因），
不把不相邻的句子伪装成紧邻句子。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..domain.enums import ReadingMode
from .budget import (
    DEFAULT_ESTIMATOR,
    BudgetItemKind,
    BudgetLedger,
    BudgetPolicy,
    TokenEstimator,
)

HORIZON_OMIT_REASON = "beyond_visible_horizon"
BUDGET_OMIT_REASON = "budget_exhausted"


@dataclass(frozen=True)
class QuoteView:
    quote_id: str
    start_cp: int
    end_cp: int
    nesting_depth: int = 0


@dataclass(frozen=True)
class GapView:
    gap_id: str
    start_cp: int
    end_cp: int
    left_quote_id: str | None = None
    right_quote_id: str | None = None


@dataclass(frozen=True)
class ParagraphView:
    """用于把两端重叠对齐到完整段落（也保证片段的 node_id 可回溯）。"""

    node_id: str
    start_cp: int
    end_cp: int


@dataclass(frozen=True)
class ContextFragment:
    fragment_id: str
    kind: BudgetItemKind
    start_cp: int
    end_cp: int
    text: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "fragment_id": self.fragment_id,
            "kind": self.kind.value,
            "start_cp": self.start_cp,
            "end_cp": self.end_cp,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class OmittedRecord:
    fragment_id: str
    kind: str
    start_cp: int
    end_cp: int
    reason: str
    tokens: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "fragment_id": self.fragment_id,
            "kind": self.kind,
            "start_cp": self.start_cp,
            "end_cp": self.end_cp,
            "reason": self.reason,
            "tokens": self.tokens,
        }


@dataclass
class SelectionResult:
    fragments: list[ContextFragment] = field(default_factory=list)
    omitted: list[OmittedRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ledger: BudgetLedger = field(default_factory=BudgetLedger)
    reading_mode: ReadingMode = ReadingMode.INITIAL
    horizon_cp: int | None = None

    @property
    def fragment_ids(self) -> list[str]:
        return [fragment.fragment_id for fragment in self.fragments]

    def as_dict(self) -> dict[str, Any]:
        return {
            "fragments": [fragment.as_dict() for fragment in self.fragments],
            "omitted": [record.as_dict() for record in self.omitted],
            "warnings": list(self.warnings),
            "budget": self.ledger.summary(),
            "reading_mode": self.reading_mode.value,
            "visible_horizon_cp": self.horizon_cp,
        }


def _overlap_range(
    paragraphs: list[ParagraphView],
    *,
    anchor_cp: int,
    limit_tokens: int,
    estimator: TokenEstimator,
    text: str,
    direction: str,
) -> tuple[int, int] | None:
    """从锚点向外取若干完整段落，直到接近 overlap_tokens 上限。"""

    if limit_tokens <= 0:
        return None
    if direction == "before":
        candidates = [item for item in paragraphs if item.end_cp <= anchor_cp]
        candidates.sort(key=lambda item: item.end_cp, reverse=True)
    else:
        candidates = [item for item in paragraphs if item.start_cp >= anchor_cp]
        candidates.sort(key=lambda item: item.start_cp)

    total = 0
    chosen: list[ParagraphView] = []
    for paragraph in candidates:
        # 不在这里过滤 horizon：交给 _add 统一判断并写入省略记录（reason=beyond_visible_horizon）。
        snippet = text[paragraph.start_cp : paragraph.end_cp]
        cost = estimator.estimate(snippet)
        if total + cost > limit_tokens and chosen:
            break
        chosen.append(paragraph)
        total += cost
        if total >= limit_tokens:
            break
    if not chosen:
        return None
    start = min(item.start_cp for item in chosen)
    end = max(item.end_cp for item in chosen)
    return start, end


def select_evidence(
    *,
    canonical_text: str,
    quotes: list[QuoteView],
    gaps: list[GapView],
    target_quote_ids: list[str],
    policy: BudgetPolicy,
    reading_mode: ReadingMode = ReadingMode.INITIAL,
    visible_horizon_cp: int | None = None,
    paragraphs: list[ParagraphView] | None = None,
    scene_state: str | None = None,
    locked_summary: str | None = None,
    overlap_before: bool = False,
    overlap_after: bool = True,
    estimator: TokenEstimator | None = None,
) -> SelectionResult:
    """为一个窗口选择证据；返回片段、省略记录、警告与预算账本。"""

    estimator = estimator or DEFAULT_ESTIMATOR
    ledger = BudgetLedger(policy=policy, estimator=estimator)
    result = SelectionResult(ledger=ledger, reading_mode=reading_mode)
    horizon_cp = visible_horizon_cp if reading_mode is ReadingMode.INITIAL else None
    result.horizon_cp = horizon_cp

    quote_by_id = {quote.quote_id: quote for quote in quotes}
    targets = [quote_by_id[quote_id] for quote_id in target_quote_ids if quote_id in quote_by_id]
    missing = [quote_id for quote_id in target_quote_ids if quote_id not in quote_by_id]
    for quote_id in missing:
        result.warnings.append(f"unknown_target:{quote_id}")
    if not targets:
        return result
    targets.sort(key=lambda quote: quote.start_cp)

    first_start, last_end = targets[0].start_cp, targets[-1].end_cp
    paragraphs = paragraphs or []

    def _horizon_blocked(start_cp: int, end_cp: int) -> bool:
        return horizon_cp is not None and end_cp > horizon_cp

    def _add(
        *,
        fragment_id: str,
        kind: BudgetItemKind,
        start_cp: int,
        end_cp: int,
        reason: str,
        optional: bool,
    ) -> None:
        text = canonical_text[start_cp:end_cp]
        if _horizon_blocked(start_cp, end_cp):
            if optional:
                record = OmittedRecord(
                    fragment_id=fragment_id,
                    kind=kind.value,
                    start_cp=start_cp,
                    end_cp=end_cp,
                    reason=HORIZON_OMIT_REASON,
                    tokens=estimator.estimate(text),
                )
                result.omitted.append(record)
                return
            # 必留片段却被 horizon 挡住：如实告警并按 horizon 截到可见范围，
            # 绝不把 horizon 之后的原文送进模型。
            result.warnings.append(f"required_evidence_beyond_horizon:{fragment_id}")
            end_cp = horizon_cp or end_cp
            if end_cp <= start_cp:
                result.omitted.append(
                    OmittedRecord(
                        fragment_id=fragment_id,
                        kind=kind.value,
                        start_cp=start_cp,
                        end_cp=end_cp,
                        reason=HORIZON_OMIT_REASON,
                        tokens=0,
                    )
                )
                return
            text = canonical_text[start_cp:end_cp]

        item = ledger.register(
            item_id=fragment_id, kind=kind, text=text, reason=reason
        )
        if ledger.try_include(item, allow_optional_overflow=not optional):
            result.fragments.append(
                ContextFragment(
                    fragment_id=fragment_id,
                    kind=kind,
                    start_cp=start_cp,
                    end_cp=end_cp,
                    text=text,
                    reason=reason,
                )
            )
        else:
            result.omitted.append(
                OmittedRecord(
                    fragment_id=fragment_id,
                    kind=kind.value,
                    start_cp=start_cp,
                    end_cp=end_cp,
                    reason=item.dropped_reason or BUDGET_OMIT_REASON,
                    tokens=item.tokens,
                )
            )

    # 1) 目标对白（必留）
    for quote in targets:
        _add(
            fragment_id=quote.quote_id,
            kind=BudgetItemKind.TARGET_QUOTE,
            start_cp=quote.start_cp,
            end_cp=quote.end_cp,
            reason="target_quote",
            optional=False,
        )

    # 2) 目标之间的 Gap（必留）
    inner_gaps = [
        gap
        for gap in gaps
        if gap.start_cp >= first_start and gap.end_cp <= last_end and gap.end_cp > gap.start_cp
    ]
    for gap in sorted(inner_gaps, key=lambda item: item.start_cp):
        _add(
            fragment_id=gap.gap_id,
            kind=BudgetItemKind.INNER_GAP,
            start_cp=gap.start_cp,
            end_cp=gap.end_cp,
            reason="gap_between_targets",
            optional=False,
        )

    # 3) 场景状态与已锁定结果（可选，但仍计入预算）
    if scene_state:
        # 场景状态按 state_tokens 截断（它本身就是摘要，不是原文片段，允许截断）。
        state_text = scene_state[: max(0, policy.state_tokens)] if policy.state_tokens else ""
        item = ledger.register(
            item_id="state:scene",
            kind=BudgetItemKind.STATE,
            text=state_text,
            reason="scene_state",
        )
        if ledger.try_include(item):
            result.fragments.append(
                ContextFragment(
                    fragment_id="state:scene",
                    kind=BudgetItemKind.STATE,
                    start_cp=0,
                    end_cp=0,
                    text=state_text,
                    reason="scene_state",
                )
            )
        else:
            result.omitted.append(
                OmittedRecord(
                    fragment_id="state:scene",
                    kind=BudgetItemKind.STATE.value,
                    start_cp=0,
                    end_cp=0,
                    reason=item.dropped_reason or BUDGET_OMIT_REASON,
                    tokens=item.tokens,
                )
            )
    if locked_summary:
        item = ledger.register(
            item_id="state:locked",
            kind=BudgetItemKind.STATE,
            text=locked_summary,
            reason="locked_results",
        )
        if ledger.try_include(item):
            result.fragments.append(
                ContextFragment(
                    fragment_id="state:locked",
                    kind=BudgetItemKind.STATE,
                    start_cp=0,
                    end_cp=0,
                    text=locked_summary,
                    reason="locked_results",
                )
            )
        else:
            result.omitted.append(
                OmittedRecord(
                    fragment_id="state:locked",
                    kind=BudgetItemKind.STATE.value,
                    start_cp=0,
                    end_cp=0,
                    reason=item.dropped_reason or BUDGET_OMIT_REASON,
                    tokens=item.tokens,
                )
            )

    # 4) 外层 Gap（可选）：紧邻目标区间前后的叙述
    outer_gaps = [
        gap
        for gap in gaps
        if gap.end_cp <= first_start or gap.start_cp >= last_end
    ]
    before = [gap for gap in outer_gaps if gap.end_cp <= first_start]
    after = [gap for gap in outer_gaps if gap.start_cp >= last_end]
    for gap in sorted(before, key=lambda item: item.start_cp, reverse=True)[:1]:
        _add(
            fragment_id=gap.gap_id,
            kind=BudgetItemKind.OUTER_GAP,
            start_cp=gap.start_cp,
            end_cp=gap.end_cp,
            reason="gap_before_targets",
            optional=True,
        )
    for gap in sorted(after, key=lambda item: item.start_cp)[:1]:
        _add(
            fragment_id=gap.gap_id,
            kind=BudgetItemKind.OUTER_GAP,
            start_cp=gap.start_cp,
            end_cp=gap.end_cp,
            reason="gap_after_targets",
            optional=True,
        )

    # 5) 两端重叠（可选，按完整段落对齐）
    if overlap_before:
        overlap = _overlap_range(
            paragraphs,
            anchor_cp=first_start,
            limit_tokens=policy.overlap_tokens,
            estimator=estimator,
            text=canonical_text,
            direction="before",
        )
        if overlap is not None:
            _add(
                fragment_id=f"overlap:{overlap[0]}-{overlap[1]}",
                kind=BudgetItemKind.OVERLAP,
                start_cp=overlap[0],
                end_cp=overlap[1],
                reason="overlap_before",
                optional=True,
            )
    if overlap_after:
        overlap = _overlap_range(
            paragraphs,
            anchor_cp=last_end,
            limit_tokens=policy.overlap_tokens,
            estimator=estimator,
            text=canonical_text,
            direction="after",
        )
        if overlap is not None:
            _add(
                fragment_id=f"overlap:{overlap[0]}-{overlap[1]}",
                kind=BudgetItemKind.OVERLAP,
                start_cp=overlap[0],
                end_cp=overlap[1],
                reason="overlap_after",
                optional=True,
            )

    result.fragments.sort(key=lambda fragment: (fragment.start_cp, fragment.fragment_id))
    return result
