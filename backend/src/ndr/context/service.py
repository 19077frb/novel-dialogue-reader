"""从数据库加载上下文输入并规划窗口。

只读：不写数据库、不调用模型。证据全部按 §4.3 的记录方式给出（片段 ID 可回溯到
quote/gap/node）。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..domain.enums import ContentNodeType, ReadingMode
from ..quotes.service import canonical_text_of
from ..storage.models import Chapter, ContentNode, Gap, Quote
from .budget import DEFAULT_POLICY, BudgetPolicy
from .source_selection import GapView, ParagraphView, QuoteView
from .window_builder import WindowInputs, WindowPlan, plan_windows

PARAGRAPH_NODE_TYPES = (ContentNodeType.PARAGRAPH, ContentNodeType.HEADING)


def load_window_inputs(
    session: Session,
    settings: Settings,
    version,  # noqa: ANN001 - BookVersion
    *,
    reading_mode: ReadingMode = ReadingMode.INITIAL,
    visible_horizon_cp: int | None = None,
    scene_ref: str = "scene_current",
    scene_state: str | None = None,
    locked_summary: str | None = None,
    speaker_refs: tuple[str, ...] = (),
    policy: BudgetPolicy | None = None,
) -> WindowInputs:
    """把某个书籍版本的引语/Gap/段落装配成 :class:`WindowInputs`（纯只读）。"""

    text = canonical_text_of(settings, version)
    quotes = [
        QuoteView(
            quote_id=row.id,
            start_cp=row.start_cp,
            end_cp=row.end_cp,
            nesting_depth=row.nesting_depth,
        )
        for row in session.execute(
            select(Quote).where(Quote.book_version_id == version.id).order_by(Quote.start_cp)
        ).scalars()
    ]
    gaps = [
        GapView(
            gap_id=row.id,
            start_cp=row.start_cp,
            end_cp=row.end_cp,
            left_quote_id=row.left_quote_id,
            right_quote_id=row.right_quote_id,
        )
        for row in session.execute(
            select(Gap).where(Gap.book_version_id == version.id).order_by(Gap.start_cp)
        ).scalars()
    ]
    paragraphs: list[ParagraphView] = []
    node_rows = session.execute(
        select(ContentNode)
        .join(Chapter, ContentNode.chapter_id == Chapter.id)
        .where(
            Chapter.book_version_id == version.id,
            ContentNode.node_type.in_(PARAGRAPH_NODE_TYPES),
            ContentNode.start_cp.is_not(None),
        )
        .order_by(ContentNode.start_cp)
    ).scalars()
    for node in node_rows:
        if node.start_cp is None or node.end_cp is None:
            continue
        paragraphs.append(
            ParagraphView(node_id=node.node_id, start_cp=node.start_cp, end_cp=node.end_cp)
        )

    return WindowInputs(
        book_version_id=version.id,
        canonical_text=text,
        quotes=quotes,
        gaps=gaps,
        paragraphs=paragraphs,
        reading_mode=reading_mode,
        visible_horizon_cp=visible_horizon_cp,
        scene_ref=scene_ref,
        scene_state=scene_state,
        locked_summary=locked_summary,
        speaker_refs=speaker_refs,
        policy=policy or DEFAULT_POLICY,
    )


def plan_range(
    session: Session,
    settings: Settings,
    version,  # noqa: ANN001
    *,
    start_cp: int = 0,
    end_cp: int | None = None,
    top_level_only: bool = True,
    **kwargs,  # noqa: ANN003 - 透传给 load_window_inputs
) -> WindowPlan:
    """为码点范围规划窗口；默认只用外层候选作为目标（嵌套引用作为上下文出现）。"""

    inputs = load_window_inputs(session, settings, version, **kwargs)
    limit = version.canonical_length_cp if end_cp is None else end_cp
    targets = [
        quote.quote_id
        for quote in inputs.quotes
        if quote.start_cp >= start_cp
        and quote.start_cp < limit
        and (quote.nesting_depth == 0 or not top_level_only)
    ]
    return plan_windows(inputs, target_quote_ids=targets)
