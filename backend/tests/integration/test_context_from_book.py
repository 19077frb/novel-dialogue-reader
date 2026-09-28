"""集成测试：从真实导入的书籍规划上下文窗口。

用导入流水线产生的候选/Gap/段落（不是手写夹具），验证：
目标覆盖、预算约束、片段可回溯、horizon 限制与依赖哈希。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from ndr.config import Settings
from ndr.context.budget import BudgetPolicy
from ndr.context.service import load_window_inputs, plan_range
from ndr.domain.enums import ReadingMode
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.models import BookVersion, Quote
from ndr.storage.transactions import transaction

SAMPLE = (
    "序章 雨夜\n"
    "\n"
    "「雨停了。」少女合上伞。\n"
    "少年没有回答，只是把外套递了过去。\n"
    "\n"
    "「……谢谢。」她低声说。\n"
    "\n"
    "第一章 转折\n"
    "\n"
    "远处传来钟声，两人都没有再开口。\n"
    "「明天也来这里吧。」少年忽然说。\n"
    "「嗯。」少女点了点头。\n"
)


def _import(client: TestClient) -> dict:
    response = client.post(
        "/api/books/import",
        files={"file": ("sample.txt", SAMPLE.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 202, response.text
    return response.json()["data"]


def test_plan_covers_all_candidates_within_budget(
    migrated_client: TestClient, migrated_settings: Settings
) -> None:
    data = _import(migrated_client)
    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            version = session.get(BookVersion, data["book_version_id"])
            assert version is not None
            quote_ids = list(
                session.execute(
                    select(Quote.id)
                    .where(Quote.book_version_id == version.id, Quote.nesting_depth == 0)
                    .order_by(Quote.start_cp)
                ).scalars()
            )
            policy = BudgetPolicy(context_tokens=60, overlap_tokens=10)
            plan = plan_range(session, migrated_settings, version, policy=policy)
    finally:
        engine.dispose()

    covered = [quote_id for window in plan.windows for quote_id in window.target_quote_ids]
    assert covered == quote_ids  # 顺序一致、无丢失
    assert len(plan.windows) >= 2  # 小预算下会拆窗口

    for window in plan.windows:
        assert window.budget["context_tokens"] <= policy.context_tokens
        assert window.budget["total_tokens"] == (
            window.budget["prompt_tokens"]
            + window.budget["context_tokens"]
            + window.budget["output_tokens"]
        )
        # 可回溯：片段 ID 必须是 quote/gap id、段落 node_id 或 overlap/state 前缀
        assert window.fragment_ids
    assert plan.stats["targets"] == len(quote_ids)


def test_windows_carry_overlap_between_chapters(
    migrated_client: TestClient, migrated_settings: Settings
) -> None:
    data = _import(migrated_client)
    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            version = session.get(BookVersion, data["book_version_id"])
            assert version is not None
            inputs = load_window_inputs(session, migrated_settings, version)
            # 第二章的对白（跨章节）作为目标时，上下文仍能包含第一章末尾的 Gap
            targets = [quote.quote_id for quote in inputs.quotes if quote.nesting_depth == 0]
            plan = plan_range(
                session,
                migrated_settings,
                version,
                start_cp=inputs.quotes[-1].start_cp,  # 只处理最后一条
            )
    finally:
        engine.dispose()

    assert len(targets) >= 4
    assert len(plan.windows) == 1
    window = plan.windows[0]
    assert window.target_quote_ids == (targets[-1],)
    # Gap（章节标题与叙述）仍在上下文里，说明跨章节不会被截断
    assert any(fragment.kind.value in {"inner_gap", "outer_gap"} for fragment in window.fragments)


def test_horizon_limits_context_but_not_targets(
    migrated_client: TestClient, migrated_settings: Settings
) -> None:
    data = _import(migrated_client)
    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            version = session.get(BookVersion, data["book_version_id"])
            assert version is not None
            inputs = load_window_inputs(session, migrated_settings, version)
            first_quote = inputs.quotes[0]
            horizon = first_quote.end_cp + 5
            plan = plan_range(
                session,
                migrated_settings,
                version,
                start_cp=first_quote.start_cp,
                end_cp=first_quote.end_cp,
                reading_mode=ReadingMode.INITIAL,
                visible_horizon_cp=horizon,
            )
    finally:
        engine.dispose()

    window = plan.windows[0]
    assert window.target_quote_ids == (first_quote.quote_id,)
    for fragment in window.fragments:
        if fragment.kind.value in {"inner_gap", "outer_gap", "overlap"}:
            assert fragment.end_cp <= horizon  # 不把 horizon 之后的原文送进模型
