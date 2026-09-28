"""上下文、预算与证据范围。

对外只暴露这几件事：预算策略与账本、证据选择、窗口构建。
"""

from __future__ import annotations

from .budget import (
    COMPRESSED_POLICY,
    CONTEXT_POLICY_COMPRESSED,
    CONTEXT_POLICY_CONSERVATIVE,
    DEFAULT_ESTIMATOR,
    DEFAULT_POLICY,
    POLICY_BY_VERSION,
    RECHECK_POLICY,
    BudgetItemKind,
    BudgetLedger,
    BudgetPolicy,
    LedgerItem,
    TokenEstimator,
    estimate_tokens,
    policy_for_version,
    policy_version_for,
)
from .recheck import (
    RecheckDecision,
    RouteDecision,
    plan_recheck,
    route_window,
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
    "COMPRESSED_POLICY",
    "CONTEXT_POLICY_COMPRESSED",
    "CONTEXT_POLICY_CONSERVATIVE",
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
    "POLICY_BY_VERSION",
    "ParagraphView",
    "ProcessingWindow",
    "QuoteView",
    "RecheckDecision",
    "RouteDecision",
    "SelectionResult",
    "TokenEstimator",
    "WindowInputs",
    "WindowPlan",
    "estimate_tokens",
    "plan_recheck",
    "plan_windows",
    "policy_for_version",
    "policy_version_for",
    "route_window",
    "select_evidence",
]
