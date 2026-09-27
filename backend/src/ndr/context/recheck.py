"""T17：有限局部复核与成本路由的纯决策层。

只做**决策**，不调用模型、不写数据库：调度器照此派发，因此这套规则可以完全离线单测。

- **有限局部复核**（:func:`plan_recheck`）：只复核「首次结果仍未解决」的目标，每个窗口最多
  ``recheck_max_targets`` 条；复核改用**保守策略**重建窗口，把压缩阶段丢掉的句子原样补回
  （``restore_evidence``）。默认 ``recheck_max_targets=0``，即不复核。
- **成本路由**（:func:`route_window`）：困难窗口可交给强模型配置，但受 ``strong_model_share``
  上限约束；没有强模型配置或超出上限就退回基础模型，理由如实记录，不静默改路由。
- **默认保守**：两个开关默认关闭；只有拿到 B3/B4 真实对比证据才应改变默认策略。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .budget import CONTEXT_POLICY_CONSERVATIVE, BudgetPolicy
from .source_selection import COMPRESSION_OMIT_REASON

RECHECK_POLICY_VERSION = "recheck-1"
ROUTING_POLICY_VERSION = "routing-1"
HARD_TARGET_THRESHOLD = 4

BASE_PROFILE = "base"
STRONG_PROFILE = "strong"

DISABLED_BY_POLICY = "policy_disabled"
NO_UNRESOLVED_TARGETS = "no_unresolved_targets"
UNRESOLVED_TARGETS = "unresolved_targets"
STRONG_PROFILE_UNAVAILABLE = "strong_profile_unavailable"
SHARE_CAP_REACHED = "share_cap_reached"
HARD_WINDOW = "hard_window"
NOT_HARD = "not_hard"


@dataclass(frozen=True)
class RecheckDecision:
    """一个窗口的复核决策（默认关闭）。"""

    enabled: bool
    reason: str
    targets: tuple[str, ...] = ()
    context_policy_version: str = CONTEXT_POLICY_CONSERVATIVE
    restore_evidence: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_version": RECHECK_POLICY_VERSION,
            "enabled": self.enabled,
            "reason": self.reason,
            "targets": list(self.targets),
            "context_policy_version": self.context_policy_version,
            "restore_evidence": self.restore_evidence,
        }


def dropped_compressed_evidence(window) -> bool:  # noqa: ANN001
    """该窗口是否因为 T17 压缩丢过句子（丢过就必须能在复核里补回）。"""

    return any(record.reason == COMPRESSION_OMIT_REASON for record in window.omitted)


def plan_recheck(
    *,
    policy: BudgetPolicy,
    window,  # noqa: ANN001 - ProcessingWindow
    unresolved_target_ids,  # noqa: ANN001 - Iterable[str]
) -> RecheckDecision:
    """给出复核决策：只取「未解决且属于本窗口」的目标，并按 ``recheck_max_targets`` 截断。"""

    if policy.recheck_max_targets <= 0:
        return RecheckDecision(enabled=False, reason=DISABLED_BY_POLICY)
    unresolved = set(unresolved_target_ids)
    ordered = [quote_id for quote_id in window.target_quote_ids if quote_id in unresolved]
    if not ordered:
        return RecheckDecision(enabled=False, reason=NO_UNRESOLVED_TARGETS)
    return RecheckDecision(
        enabled=True,
        reason=UNRESOLVED_TARGETS,
        targets=tuple(ordered[: policy.recheck_max_targets]),
        restore_evidence=dropped_compressed_evidence(window),
    )


def strong_cap(*, share: float, total_windows: int) -> int:
    """允许升级到强模型的窗口数上限（向下取整；``share<=0`` 表示关闭）。"""

    if share <= 0 or total_windows <= 0:
        return 0
    return int(share * total_windows)


def is_hard_window(window, *, recheck: RecheckDecision | None = None) -> bool:  # noqa: ANN001
    """困难窗口：需要复核、丢过证据、目标过多，或有超长目标。"""

    if recheck is not None and recheck.enabled:
        return True
    if window.omitted:
        return True
    if any(str(item).startswith("oversized_quote") for item in window.warnings):
        return True
    return len(window.target_quote_ids) >= HARD_TARGET_THRESHOLD


@dataclass(frozen=True)
class RouteDecision:
    """一个窗口的路由决策：``base`` 或 ``strong``，附理由与上限。"""

    profile: str
    reason: str
    cap: int = 0
    used: int = 0

    @property
    def strong(self) -> bool:
        return self.profile == STRONG_PROFILE

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_version": ROUTING_POLICY_VERSION,
            "profile": self.profile,
            "reason": self.reason,
            "strong_cap": self.cap,
            "strong_used": self.used,
        }


def route_window(
    *,
    policy: BudgetPolicy,
    window,  # noqa: ANN001 - ProcessingWindow
    total_windows: int,
    strong_available: bool,
    strong_used: int,
    recheck: RecheckDecision | None = None,
) -> RouteDecision:
    """选择本窗口用基础模型还是强模型；任何不确定都退回基础模型并写明理由。"""

    cap = strong_cap(share=policy.strong_model_share, total_windows=total_windows)
    if cap <= 0:
        return RouteDecision(BASE_PROFILE, DISABLED_BY_POLICY, cap, strong_used)
    if not strong_available:
        return RouteDecision(BASE_PROFILE, STRONG_PROFILE_UNAVAILABLE, cap, strong_used)
    if strong_used >= cap:
        return RouteDecision(BASE_PROFILE, SHARE_CAP_REACHED, cap, strong_used)
    if not is_hard_window(window, recheck=recheck):
        return RouteDecision(BASE_PROFILE, NOT_HARD, cap, strong_used)
    return RouteDecision(STRONG_PROFILE, HARD_WINDOW, cap, strong_used)
