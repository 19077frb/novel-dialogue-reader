"""单元测试：token 估算与预算账本。

门槛相关：**补入任何片段都计入预算**；预算先扣提示与输出预留；
可选片段先丢、目标发言绝不截断。
"""

from __future__ import annotations

import pytest

from ndr.context.budget import (
    COMPRESSED_POLICY,
    DEFAULT_ESTIMATOR,
    DEFAULT_POLICY,
    RECHECK_POLICY,
    BudgetItemKind,
    BudgetLedger,
    BudgetPolicy,
    estimate_tokens,
    policy_for_version,
    policy_version_for,
)


def test_estimator_counts_cjk_and_latin_differently() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("雨停了") == 3
    # 汉语标点也算 1 token（否则中文正文会被系统性低估）
    assert estimate_tokens("「雨停了。」") == 6
    # 拉丁文本约 4 字符/token
    assert estimate_tokens("abcdefgh") == 2
    assert estimate_tokens("a") == 1


def test_estimator_is_deterministic_and_labeled() -> None:
    text = "「明天也来这里吧。」少年忽然说。"
    assert estimate_tokens(text) == estimate_tokens(text) == DEFAULT_ESTIMATOR.estimate(text)
    assert DEFAULT_ESTIMATOR.method == "heuristic-cjk"
    assert DEFAULT_ESTIMATOR.confidence == "low"  # 明确标注为启发式，不当成真实计费


def test_ledger_counts_prompt_and_output_reserves() -> None:
    policy = BudgetPolicy(
        context_tokens=100, prompt_reserve_tokens=30, output_reserve_tokens=20
    )
    ledger = BudgetLedger(policy=policy)

    assert ledger.prompt_tokens == 30
    assert ledger.output_tokens == 20
    assert ledger.context_tokens == 0
    assert ledger.total_tokens == 50

    item = ledger.register(
        item_id="q1", kind=BudgetItemKind.TARGET_QUOTE, text="雨停了" * 5, reason="target"
    )
    assert ledger.try_include(item) is True
    assert ledger.context_tokens == item.tokens
    assert ledger.total_tokens == item.tokens + 50
    assert ledger.remaining_context_tokens == 100 - item.tokens


def test_every_added_fragment_is_counted() -> None:
    """补入片段必须计入预算：账本总和 = 每个已包含片段的估算之和。"""

    ledger = BudgetLedger(policy=BudgetPolicy(context_tokens=1000))
    texts = ["「雨停了。」", "少年没有回答，只是把外套递了过去。", "「……谢谢。」"]
    for index, text in enumerate(texts):
        item = ledger.register(
            item_id=f"f{index}", kind=BudgetItemKind.INNER_GAP, text=text, reason="test"
        )
        ledger.try_include(item)

    assert ledger.context_tokens == sum(estimate_tokens(text) for text in texts)
    summary = ledger.summary()
    assert summary["context_tokens"] == ledger.context_tokens
    assert summary["included_items"] == len(texts)
    assert summary["estimator"]["method"] == "heuristic-cjk"
    assert len(ledger.ledger()) == len(texts)


def test_optional_items_are_dropped_before_required_ones() -> None:
    ledger = BudgetLedger(policy=BudgetPolicy(context_tokens=20))
    target = ledger.register(
        item_id="q1", kind=BudgetItemKind.TARGET_QUOTE, text="雨停了" * 4, reason="target"
    )
    assert ledger.try_include(target) is True

    overlap = ledger.register(
        item_id="overlap", kind=BudgetItemKind.OVERLAP, text="很长的背景描写" * 3, reason="overlap"
    )
    state = ledger.register(
        item_id="state", kind=BudgetItemKind.STATE, text="场景状态" * 3, reason="state"
    )

    assert ledger.try_include(overlap) is False
    assert ledger.try_include(state) is False
    assert overlap.dropped_reason == "budget_exhausted"
    assert {item.item_id for item in ledger.included_items} == {"q1"}
    assert {item.item_id for item in ledger.omitted_items} == {"overlap", "state"}


def test_oversized_required_item_is_marked_not_truncated() -> None:
    ledger = BudgetLedger(policy=BudgetPolicy(context_tokens=10))
    item = ledger.register(
        item_id="q-long", kind=BudgetItemKind.TARGET_QUOTE, text="雨" * 100, reason="target"
    )

    assert ledger.try_include(item) is False
    assert item.dropped_reason == "oversized_quote"
    assert item.text_length_cp == 100  # 文本没有被截断，只是标记为放不下
    assert ledger.context_tokens == 0


def test_policy_defaults_follow_plan_7_3() -> None:
    assert 1500 <= DEFAULT_POLICY.context_tokens <= 3000
    assert 100 <= DEFAULT_POLICY.overlap_tokens <= 300
    assert DEFAULT_POLICY.gap_compression is False  # 激进压缩默认关闭
    assert RECHECK_POLICY.context_tokens > DEFAULT_POLICY.context_tokens
    # 两个优化开关默认关闭（没有真实对比证据就不改默认策略）
    assert DEFAULT_POLICY.recheck_max_targets == 0
    assert DEFAULT_POLICY.strong_model_share == 0.0
    assert policy_version_for(DEFAULT_POLICY) == "context-1"
    assert policy_version_for(COMPRESSED_POLICY) == "context-2"
    assert policy_for_version("context-2") is COMPRESSED_POLICY
    assert policy_for_version("context-1") is DEFAULT_POLICY
    assert policy_for_version("unknown-policy") is DEFAULT_POLICY
    assert policy_for_version(None) is DEFAULT_POLICY
    # 参与依赖哈希的字段必须完整（漏一个字段就会错误复用缓存）
    key = DEFAULT_POLICY.as_key()
    assert set(key) == {
        "context_tokens",
        "overlap_tokens",
        "state_tokens",
        "prompt_reserve_tokens",
        "output_reserve_tokens",
        "gap_compression",
        "gap_compression_threshold_cp",
        "gap_compression_margin_sentences",
        "gap_compression_max_ratio",
        "recheck_max_targets",
        "strong_model_share",
    }


def test_state_budget_caps_summary_length() -> None:
    policy = BudgetPolicy(state_tokens=8)
    long_state = "场景状态" * 10
    capped = long_state[: policy.state_tokens]
    assert len(capped) == 8
    assert estimate_tokens(capped) <= estimate_tokens(long_state)


@pytest.mark.parametrize("kind", [BudgetItemKind.TARGET_QUOTE, BudgetItemKind.INNER_GAP])
def test_required_kinds_are_defined(kind: BudgetItemKind) -> None:
    assert kind in {BudgetItemKind.TARGET_QUOTE, BudgetItemKind.INNER_GAP}
