"""单元测试：上下文窗口与证据范围。

门槛：
- 预算边界**不是**场景边界；
- 不丢目标对白（放不下就拆窗口，绝不截断）；
- 上下文来源可回溯（片段 ID 都来自输入）；
- 补入任何片段都计入预算。
"""

from __future__ import annotations

from ndr.context.budget import (
    BudgetItemKind,
    BudgetPolicy,
    estimate_tokens,
)
from ndr.context.source_selection import GapView, ParagraphView, QuoteView, select_evidence
from ndr.context.window_builder import (
    CONTEXT_POLICY_VERSION,
    OVERSIZED_WARNING,
    WindowInputs,
    plan_windows,
)
from ndr.domain.enums import ReadingMode

# 构造一篇原创小文本：三段对白 + 叙述 + 一条很长的心理描写
PARAGRAPHS = [
    "「雨停了。」少女合上伞。",
    "少年没有回答，只是把外套递了过去。远处传来钟声，两人都没有再开口。",
    "「……谢谢。」她低声说。",
    "「明天也来这里吧。」少年忽然说。",
    "「嗯。」少女点了点头。",
]
TEXT = "\n".join(PARAGRAPHS)
LINES = []
cursor = 0
for line in PARAGRAPHS:
    LINES.append((cursor, cursor + len(line)))
    cursor += len(line) + 1  # 计入换行


QUOTE_TEXTS = ["「雨停了。」", "「……谢谢。」", "「明天也来这里吧。」", "「嗯。」"]


def _spans() -> list[tuple[int, int]]:
    """按出现顺序在 TEXT 里定位引语（避免手写偏移写错）。"""

    spans: list[tuple[int, int]] = []
    cursor = 0
    for text in QUOTE_TEXTS:
        index = TEXT.index(text, cursor)
        spans.append((index, index + len(text)))
        cursor = index + len(text)
    return spans


def _quotes() -> list[QuoteView]:
    return [
        QuoteView(quote_id=f"q{index}", start_cp=start, end_cp=end)
        for index, (start, end) in enumerate(_spans(), start=1)
    ]


def _gaps() -> list[GapView]:
    spans = _spans()
    return [
        GapView(
            gap_id=f"g{index + 1}",
            start_cp=spans[index][1],
            end_cp=spans[index + 1][0],
            left_quote_id=f"q{index + 1}",
            right_quote_id=f"q{index + 2}",
        )
        for index in range(len(spans) - 1)
    ]


def _inputs(**overrides) -> WindowInputs:  # noqa: ANN003
    values = {
        "book_version_id": "v1",
        "canonical_text": TEXT,
        "quotes": _quotes(),
        "gaps": _gaps(),
        "paragraphs": [
            ParagraphView(node_id=f"n{index}", start_cp=start, end_cp=end)
            for index, (start, end) in enumerate(LINES)
        ],
        "policy": BudgetPolicy(),
    }
    values.update(overrides)
    return WindowInputs(**values)  # type: ignore[arg-type]


def test_single_window_keeps_quotes_and_gaps_in_order() -> None:
    plan = plan_windows(_inputs(), target_quote_ids=["q1", "q2"])

    assert len(plan.windows) == 1
    window = plan.windows[0]
    assert window.target_quote_ids == ("q1", "q2")
    kinds = [fragment.kind for fragment in window.fragments]
    assert kinds.count(BudgetItemKind.TARGET_QUOTE) == 2
    assert BudgetItemKind.INNER_GAP in kinds  # 目标之间的 Gap 必留
    # 片段顺序按文档位置
    starts = [fragment.start_cp for fragment in window.fragments if fragment.kind is not BudgetItemKind.STATE]
    assert starts == sorted(starts)
    # 可回溯：引语/Gap/状态片段都来自输入，重叠片段带 overlap: 前缀
    known = {"q1", "q2", "q3", "q4", "g1", "g2", "g3"} | {"state:scene", "state:locked"}
    for fragment_id in window.fragment_ids:
        assert fragment_id in known or fragment_id.startswith("overlap:")


def test_long_narration_between_quotes_does_not_split_window_or_scene() -> None:
    """长心理/环境描写插在问答之间时，上下文照常包含该 Gap，也不产生新场景。"""

    plan = plan_windows(_inputs(), target_quote_ids=["q1", "q2"])

    assert len(plan.windows) == 1
    window = plan.windows[0]
    gap_fragment = next(
        fragment for fragment in window.fragments if fragment.fragment_id == "g1"
    )
    assert "少年没有回答" in gap_fragment.text
    # 预算/窗口切分不是场景边界：场景引用保持不变，窗口里没有 BREAK 之类的主张
    assert window.scene_ref == "scene_current"
    assert "scene_updates" not in window.as_dict()


def test_short_gap_with_time_jump_is_still_included() -> None:
    """允许短 Gap 结束场景，但**决定权在场景引擎**；上下文构建不做判断。"""

    plan = plan_windows(_inputs(), target_quote_ids=["q2", "q3"])
    window = plan.windows[0]

    assert len(plan.windows) == 1
    assert any(fragment.fragment_id == "g2" for fragment in window.fragments)
    assert not window.warnings  # 上下文构建不判断场景是否断开


def test_long_scene_splits_into_windows_with_overlap_and_carry() -> None:
    """预算不足时拆窗口，携带重叠与接力点，目标一条都不少。"""

    # 60 token 的正文预算：刚好拆成两个窗口，且第二窗口仍放得下少量重叠
    policy = BudgetPolicy(context_tokens=60, overlap_tokens=10)
    plan = plan_windows(
        _inputs(policy=policy), target_quote_ids=["q1", "q2", "q3", "q4"]
    )

    assert len(plan.windows) >= 2
    covered = [quote_id for window in plan.windows for quote_id in window.target_quote_ids]
    assert covered == ["q1", "q2", "q3", "q4"]  # 顺序保持、无丢失、无重复

    for window in plan.windows:
        assert window.budget["context_tokens"] <= policy.context_tokens

    second = plan.windows[1]
    assert second.carry_from_window_id == plan.windows[0].window_id
    assert second.carry_last_quote_id == plan.windows[0].target_quote_ids[-1]
    assert second.estimated_tokens["overlap_tokens"] > 0  # 下一窗口带少量重叠
    assert plan.stats["windows"] == len(plan.windows)


def test_budget_boundary_is_not_a_scene_boundary() -> None:
    policy = BudgetPolicy(context_tokens=30)
    plan = plan_windows(_inputs(policy=policy), target_quote_ids=["q1", "q2", "q3", "q4"])

    assert len(plan.windows) >= 2
    assert {window.scene_ref for window in plan.windows} == {"scene_current"}
    assert all(window.reading_mode is ReadingMode.INITIAL for window in plan.windows)


def test_horizon_limits_evidence_in_initial_mode_only() -> None:
    """初读只用 horizon 以内的原文；重读可用全文，且哈希不同。"""

    horizon = 60  # 只能看到第三条对白之前
    initial = plan_windows(
        _inputs(visible_horizon_cp=horizon, reading_mode=ReadingMode.INITIAL),
        target_quote_ids=["q1", "q2"],
    )
    window = initial.windows[0]
    assert window.visible_horizon_cp == horizon
    assert all(
        fragment.end_cp <= horizon
        for fragment in window.fragments
        if fragment.kind is not BudgetItemKind.STATE
    )
    assert any(record.reason == "beyond_visible_horizon" for record in window.omitted)

    reread = plan_windows(
        _inputs(visible_horizon_cp=horizon, reading_mode=ReadingMode.REREAD),
        target_quote_ids=["q1", "q2"],
    )
    assert reread.windows[0].visible_horizon_cp is None
    assert not any(
        record.reason == "beyond_visible_horizon" for record in reread.windows[0].omitted
    )
    # horizon 与阅读模式参与依赖哈希：初读与重读不能复用同一缓存
    assert initial.dependency_hash != reread.dependency_hash
    assert initial.windows[0].dependency_hash != reread.windows[0].dependency_hash


def test_oversized_quote_gets_its_own_window_without_truncation() -> None:
    long_line = "「" + "雨" * 200 + "」"
    text = long_line + "\n" + "「短句。」"
    quotes = [
        QuoteView(quote_id="q-long", start_cp=0, end_cp=len(long_line)),
        QuoteView(quote_id="q-short", start_cp=len(long_line) + 1, end_cp=len(long_line) + 6),
    ]
    policy = BudgetPolicy(context_tokens=50)
    plan = plan_windows(
        WindowInputs(
            book_version_id="v1",
            canonical_text=text,
            quotes=quotes,
            gaps=[],
            paragraphs=[ParagraphView(node_id="n1", start_cp=0, end_cp=len(long_line))],
            policy=policy,
        ),
        target_quote_ids=["q-long", "q-short"],
    )

    assert any(warning.startswith(OVERSIZED_WARNING) for warning in plan.warnings)
    oversized_window = plan.windows[0]
    assert oversized_window.target_quote_ids == ("q-long",)
    # 超长目标不截断、不猜测：独立窗口 + 明确报告（按 4.3「保留待定并报告 oversized_quote」）
    record = next(item for item in oversized_window.omitted if item.fragment_id == "q-long")
    assert record.reason == OVERSIZED_WARNING
    assert record.tokens == estimate_tokens(long_line)  # 按完整长度计费，没有截断
    assert record.end_cp - record.start_cp == len(long_line)
    # 目标仍然全部覆盖
    covered = [quote_id for window in plan.windows for quote_id in window.target_quote_ids]
    assert covered == ["q-long", "q-short"]


def test_optional_overlap_is_dropped_before_targets() -> None:
    policy = BudgetPolicy(context_tokens=25, overlap_tokens=100)
    plan = plan_windows(_inputs(policy=policy), target_quote_ids=["q3"])
    window = plan.windows[0]

    assert not any(
        fragment.kind is BudgetItemKind.OVERLAP for fragment in window.fragments
    ), "预算不足时应先丢重叠"
    assert len(window.target_quote_ids) == 1
    assert any(record.kind == BudgetItemKind.OVERLAP.value for record in window.omitted)


def test_every_fragment_is_counted_in_the_budget() -> None:
    plan = plan_windows(_inputs(), target_quote_ids=["q1", "q2"])
    window = plan.windows[0]

    expected = sum(estimate_tokens(fragment.text) for fragment in window.fragments)
    assert window.budget["context_tokens"] == expected
    assert window.estimated_tokens["context_tokens"] == expected
    assert window.budget["total_tokens"] == (
        window.budget["prompt_tokens"] + expected + window.budget["output_tokens"]
    )


def test_window_ids_and_hashes_are_deterministic() -> None:
    first = plan_windows(_inputs(), target_quote_ids=["q1", "q2"])
    second = plan_windows(_inputs(), target_quote_ids=["q1", "q2"])

    assert first.windows[0].window_id == second.windows[0].window_id
    assert first.dependency_hash == second.dependency_hash
    assert first.policy_version == CONTEXT_POLICY_VERSION

    other_policy = plan_windows(
        _inputs(policy=BudgetPolicy(context_tokens=2000)), target_quote_ids=["q1", "q2"]
    )
    assert other_policy.windows[0].window_id == first.windows[0].window_id  # 目标相同
    assert other_policy.dependency_hash != first.dependency_hash  # 但策略变了 → 依赖哈希变


def test_state_is_budgeted_and_droppable() -> None:
    rich = select_evidence(
        canonical_text=TEXT,
        quotes=_quotes(),
        gaps=[],
        target_quote_ids=["q1"],
        policy=BudgetPolicy(context_tokens=200),
        scene_state="上一窗口：S1 与 S2 在雨夜对话。",
        locked_summary="q0 已人工确认属于 S1",
    )
    assert any(fragment.kind is BudgetItemKind.STATE for fragment in rich.fragments)

    poor = select_evidence(
        canonical_text=TEXT,
        quotes=_quotes(),
        gaps=[],
        target_quote_ids=["q1"],
        policy=BudgetPolicy(context_tokens=6),
        scene_state="上一窗口：S1 与 S2 在雨夜对话。",
        locked_summary="q0 已人工确认属于 S1",
    )
    assert poor.ledger.context_tokens <= 6
    assert any(record.kind == BudgetItemKind.STATE.value for record in poor.omitted)
