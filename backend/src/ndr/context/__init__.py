"""上下文、预算与证据范围（T08）。

对外只暴露这几件事：预算策略与账本、证据选择、窗口构建。
"""

from __future__ import annotations

from .budget import (
    DEFAULT_ESTIMATOR,
    DEFAULT_POLICY,
    RECHECK_POLICY,
    BudgetItemKind,
    BudgetLedger,
    BudgetPolicy,
    LedgerItem,
    TokenEstimator,
    estimate_tokens,
)
from .source_selection import (
    ContextFragment,
    GapView,
    OmittedRecord,
    ParagraphView,
    QuoteView,
    SelectionResult,
    select_evidence,
)
from .window_builder import (
    CONTEXT_POLICY_VERSION,
    OVERSIZED_WARNING,
    ProcessingWindow,
    WindowInputs,
    WindowPlan,
    plan_windows,
)

__all__ = [
    "CONTEXT_POLICY_VERSION",
    "DEFAULT_ESTIMATOR",
    "DEFAULT_POLICY",
    "OVERSIZED_WARNING",
    "RECHECK_POLICY",
    "BudgetItemKind",
    "BudgetLedger",
    "BudgetPolicy",
    "ContextFragment",
    "GapView",
    "LedgerItem",
    "OmittedRecord",
    "ParagraphView",
    "ProcessingWindow",
    "QuoteView",
    "SelectionResult",
    "TokenEstimator",
    "WindowInputs",
    "WindowPlan",
    "estimate_tokens",
    "plan_windows",
    "select_evidence",
]
