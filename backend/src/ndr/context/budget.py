"""token 估算与预算账本（DEVELOPMENT.md 4.3 与 PLAN 7.3）。

要点：

- **统一估算口径**：适配器与窗口构建共用 :func:`estimate_tokens`
  （CJK 约 1 token/字、其它约 1/4；标为低置信度，接入真实分词器前不作为计费依据）。
- **补入任何片段都计入预算**：每条片段都在账本里留一条记录（含 token 数与理由），
  窗口的正文用量就是这些记录的和，不允许“顺手加进去但不计数”。
- 预算先扣掉提示与输出预留；正文预算用尽后只能丢可选片段，绝不截断目标发言。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


def _is_cjk_like(char: str) -> bool:
    """CJK 文字、假名、谚文、CJK 标点与全角字符：中文文本里大约 1 字 ≈ 1 token。"""

    code = ord(char)
    return (
        0x3400 <= code <= 0x9FFF
        or 0x3040 <= code <= 0x30FF
        or 0x3000 <= code <= 0x303F
        or 0xAC00 <= code <= 0xD7AF
        or 0xFF00 <= code <= 0xFFEF
        or 0x20000 <= code <= 0x2FA1F
    )


@dataclass(frozen=True)
class TokenEstimator:
    """启发式 token 估算（依据与置信度都写清楚，避免被误当成真实计费）。"""

    method: str = "heuristic-cjk"
    confidence: str = "low"

    def estimate(self, text: str) -> int:
        if not text:
            return 0
        cjk = sum(1 for char in text if _is_cjk_like(char))
        other = max(0, len(text) - cjk)
        return int(cjk + (other + 3) // 4)

    def as_dict(self) -> dict[str, str]:
        return {"method": self.method, "confidence": self.confidence}


DEFAULT_ESTIMATOR = TokenEstimator()


def estimate_tokens(text: str, estimator: TokenEstimator | None = None) -> int:
    return (estimator or DEFAULT_ESTIMATOR).estimate(text)


class BudgetItemKind(StrEnum):
    TARGET_QUOTE = "target_quote"
    INNER_GAP = "inner_gap"
    OUTER_GAP = "outer_gap"
    OVERLAP = "overlap"
    STATE = "state"


# 丢弃顺序：先丢可选上下文（重叠 → 状态 → 外层 Gap），目标与内部 Gap 永不丢
DROP_PRIORITY: tuple[BudgetItemKind, ...] = (
    BudgetItemKind.OVERLAP,
    BudgetItemKind.STATE,
    BudgetItemKind.OUTER_GAP,
)

REQUIRED_KINDS: frozenset[BudgetItemKind] = frozenset(
    {BudgetItemKind.TARGET_QUOTE, BudgetItemKind.INNER_GAP}
)


@dataclass(frozen=True)
class BudgetPolicy:
    """PLAN 7.3 的初始实验配置（默认保守，Gap 压缩关闭）。"""

    context_tokens: int = 2500
    overlap_tokens: int = 200
    state_tokens: int = 300
    prompt_reserve_tokens: int = 900
    output_reserve_tokens: int = 800
    gap_compression: bool = False  # 默认关闭；T17 依据真实对比实验才开启

    def as_key(self) -> dict[str, Any]:
        """参与依赖哈希/缓存键的字段（改动会影响缓存复用）。"""

        return {
            "context_tokens": self.context_tokens,
            "overlap_tokens": self.overlap_tokens,
            "state_tokens": self.state_tokens,
            "prompt_reserve_tokens": self.prompt_reserve_tokens,
            "output_reserve_tokens": self.output_reserve_tokens,
            "gap_compression": self.gap_compression,
        }


DEFAULT_POLICY = BudgetPolicy()
RECHECK_POLICY = BudgetPolicy(context_tokens=6000, overlap_tokens=300, state_tokens=600)


@dataclass
class LedgerItem:
    item_id: str
    kind: BudgetItemKind
    tokens: int
    text_length_cp: int
    reason: str
    included: bool = False
    dropped_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "kind": self.kind.value,
            "tokens": self.tokens,
            "text_length_cp": self.text_length_cp,
            "reason": self.reason,
            "included": self.included,
            "dropped_reason": self.dropped_reason,
        }


@dataclass
class BudgetLedger:
    """逐步加入片段并记录每一次计入；用于证明“补入片段都算进预算”。"""

    policy: BudgetPolicy = field(default_factory=BudgetPolicy)
    estimator: TokenEstimator = field(default_factory=lambda: DEFAULT_ESTIMATOR)
    items: list[LedgerItem] = field(default_factory=list)

    @property
    def included_items(self) -> list[LedgerItem]:
        return [item for item in self.items if item.included]

    @property
    def omitted_items(self) -> list[LedgerItem]:
        return [item for item in self.items if not item.included]

    @property
    def context_tokens(self) -> int:
        return sum(item.tokens for item in self.included_items)

    @property
    def prompt_tokens(self) -> int:
        return self.policy.prompt_reserve_tokens

    @property
    def output_tokens(self) -> int:
        return self.policy.output_reserve_tokens

    @property
    def total_tokens(self) -> int:
        """整次调用的估算用量（提示 + 正文 + 输出预留）。"""

        return self.prompt_tokens + self.context_tokens + self.output_tokens

    @property
    def remaining_context_tokens(self) -> int:
        return max(0, self.policy.context_tokens - self.context_tokens)

    def register(
        self, *, item_id: str, kind: BudgetItemKind, text: str, reason: str
    ) -> LedgerItem:
        item = LedgerItem(
            item_id=item_id,
            kind=kind,
            tokens=self.estimator.estimate(text),
            text_length_cp=len(text),
            reason=reason,
        )
        self.items.append(item)
        return item

    def try_include(self, item: LedgerItem, *, allow_optional_overflow: bool = False) -> bool:
        """尝试计入片段：目标片段必须放下（否则报错交给上层拆分窗口）。"""

        if item.kind in REQUIRED_KINDS and item.tokens > self.policy.context_tokens:
            item.dropped_reason = "oversized_quote"
            return False
        fits = item.tokens <= self.remaining_context_tokens
        if fits or (item.kind in REQUIRED_KINDS and allow_optional_overflow):
            item.included = True
            return True
        item.dropped_reason = "budget_exhausted"
        return False

    def summary(self) -> dict[str, Any]:
        return {
            "included_items": len(self.included_items),
            "omitted_items": len(self.omitted_items),
            "context_tokens": self.context_tokens,
            "prompt_tokens": self.prompt_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "remaining_context_tokens": self.remaining_context_tokens,
            "context_budget": self.policy.context_tokens,
            "estimator": self.estimator.as_dict(),
            "policy": self.policy.as_key(),
        }

    def ledger(self) -> list[dict[str, Any]]:
        return [item.as_dict() for item in self.items]
