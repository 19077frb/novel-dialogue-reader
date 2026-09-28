"""单元测试：有限复核与成本路由的决策规则（不调用模型、不写数据库）。

门槛相关：默认关闭；复核只覆盖「未解决 ∩ 本窗口」的目标并受上限约束；
强模型路由受 share 上限约束，任何不确定都退回基础模型。
"""

from __future__ import annotations

from dataclasses import dataclass

from ndr.context.budget import DEFAULT_POLICY, BudgetPolicy
from ndr.context.recheck import (
    BASE_PROFILE,
    DISABLED_BY_POLICY,
    HARD_WINDOW,
    NOT_HARD,
    SHARE_CAP_REACHED,
    STRONG_PROFILE,
    STRONG_PROFILE_UNAVAILABLE,
    plan_recheck,
    route_window,
    strong_cap,
)
from ndr.context.source_selection import OmittedRecord


@dataclass
class _Window:
    """最小的窗口替身：规则只依赖目标、省略记录与警告。"""

    target_quote_ids: tuple[str, ...] = ()
    omitted: tuple[OmittedRecord, ...] = ()
    warnings: tuple[str, ...] = ()


def _omitted(reason: str = "gap_compression") -> OmittedRecord:
    return OmittedRecord(
        fragment_id="g1#drop", kind="inner_gap", start_cp=1, end_cp=9, reason=reason, tokens=5
    )


def test_recheck_is_disabled_by_default() -> None:
    window = _Window(("q1", "q2"), (_omitted(),))
    decision = plan_recheck(policy=DEFAULT_POLICY, window=window, unresolved_target_ids=["q1"])
    assert decision.enabled is False
    assert decision.reason == DISABLED_BY_POLICY
    assert decision.targets == ()


def test_recheck_skips_windows_without_unresolved_targets() -> None:
    policy = BudgetPolicy(recheck_max_targets=2)
    decision = plan_recheck(policy=policy, window=_Window(("q1", "q2")), unresolved_target_ids=[])
    assert decision.enabled is False
    assert decision.reason == "no_unresolved_targets"


def test_recheck_caps_targets_and_restores_dropped_evidence() -> None:
    policy = BudgetPolicy(recheck_max_targets=2)
    window = _Window(("q1", "q2", "q3", "q4"), (_omitted(),))
    decision = plan_recheck(policy=policy, window=window, unresolved_target_ids=["q4", "q2", "q9"])
    assert decision.enabled is True
    assert decision.targets == ("q2", "q4")  # 按窗口顺序、上限 2；不在窗口里的 q9 被忽略
    assert decision.restore_evidence is True
    assert decision.context_policy_version == "context-1"  # 复核回到保守策略


def test_recheck_without_compressed_evidence_does_not_claim_restoration() -> None:
    policy = BudgetPolicy(recheck_max_targets=1)
    window = _Window(("q1",), (_omitted(reason="budget_exhausted"),))
    decision = plan_recheck(policy=policy, window=window, unresolved_target_ids=["q1"])
    assert decision.enabled is True
    assert decision.restore_evidence is False


def test_strong_cap_is_floor_and_disabled_at_zero() -> None:
    assert strong_cap(share=0.0, total_windows=5) == 0
    assert strong_cap(share=1.0, total_windows=3) == 3
    assert strong_cap(share=0.5, total_windows=3) == 1
    assert strong_cap(share=0.4, total_windows=2) == 0
    assert strong_cap(share=0.5, total_windows=0) == 0


def test_route_window_is_off_by_default() -> None:
    decision = route_window(
        policy=DEFAULT_POLICY,
        window=_Window(("q1", "q2", "q3", "q4")),
        total_windows=1,
        strong_available=True,
        strong_used=0,
    )
    assert decision.profile == BASE_PROFILE
    assert decision.reason == DISABLED_BY_POLICY
    assert decision.strong is False


def test_route_window_falls_back_when_no_strong_profile() -> None:
    policy = BudgetPolicy(strong_model_share=1.0)
    decision = route_window(
        policy=policy,
        window=_Window(("q1", "q2", "q3", "q4")),
        total_windows=1,
        strong_available=False,
        strong_used=0,
    )
    assert decision.profile == BASE_PROFILE
    assert decision.reason == STRONG_PROFILE_UNAVAILABLE


def test_route_window_respects_share_cap() -> None:
    policy = BudgetPolicy(strong_model_share=0.5)
    window = _Window(("q1", "q2", "q3", "q4"))
    first = route_window(
        policy=policy, window=window, total_windows=4, strong_available=True, strong_used=0
    )
    assert first.profile == STRONG_PROFILE
    assert first.reason == HARD_WINDOW
    assert first.cap == 2

    second = route_window(
        policy=policy, window=window, total_windows=4, strong_available=True, strong_used=2
    )
    assert second.profile == BASE_PROFILE
    assert second.reason == SHARE_CAP_REACHED


def test_route_window_keeps_easy_windows_on_base_model() -> None:
    policy = BudgetPolicy(strong_model_share=1.0)
    decision = route_window(
        policy=policy,
        window=_Window(("q1",)),
        total_windows=2,
        strong_available=True,
        strong_used=0,
    )
    assert decision.profile == BASE_PROFILE
    assert decision.reason == NOT_HARD


def test_route_window_flags_oversized_and_compressed_windows_as_hard() -> None:
    policy = BudgetPolicy(strong_model_share=1.0)
    oversized = route_window(
        policy=policy,
        window=_Window(("q1",), warnings=("oversized_quote:q1",)),
        total_windows=2,
        strong_available=True,
        strong_used=0,
    )
    assert oversized.profile == STRONG_PROFILE
    compressed = route_window(
        policy=policy,
        window=_Window(("q1",), (_omitted(),)),
        total_windows=2,
        strong_available=True,
        strong_used=0,
    )
    assert compressed.profile == STRONG_PROFILE
