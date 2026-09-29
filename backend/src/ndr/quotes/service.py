"""候选引语的持久化与查询。

- ``scan_and_store``：扫描 + 构造 Gap + 落库；候选是**派生数据**，重新扫描会替换旧的候选行，
  但 ID 由（版本+位置+扫描器版本）稳定派生，所以同一份文本重复扫描得到同一批 ID。
- 已有用户标注时拒绝覆盖（``ScanConflict``）——人工结果永远优先于自动候选。
- 查询：候选列表、单条候选详情（含上下文与前置 Gap）、Gap 列表、按码点定位原文片段。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..api.errors import ApiError
from ..api.pagination import decode_cursor, encode_cursor
from ..config import Settings
from ..context.cache import invalidate_version
from ..domain.enums import QuoteNormalizationStatus
from ..domain.quotes import (
    GapOut,
    LocateOut,
    LocateSpanOut,
    QuoteDetailOut,
    QuoteOut,
    ScanWarningOut,
)
from ..storage.models import (
    Annotation,
    Chapter,
    ContentNode,
    Gap,
    Quote,
    QuoteNormalization,
    TextMapping,
)
from .delimiters import DELIMITER_PAIRS
from .gaps import build_gaps
from .normalization import active_close_points
from .scanner import SCANNER_VERSION, AutoClosePoint, ScanLimits, scan_quotes

CONTEXT_WINDOW_CP = 120


class ScanConflict(RuntimeError):
    """该版本已有用户标注，拒绝用重新扫描覆盖候选。"""


@dataclass
class ScanOutcome:
    scanner_version: str
    quotes: int
    top_level_quotes: int
    gaps: int
    warnings: tuple[ScanWarningOut, ...]
    stats: dict[str, int]


def canonical_text_of(settings: Settings, version) -> str:  # noqa: ANN001
    from ..ingest.query import load_canonical_text

    return load_canonical_text(settings, version)


def has_user_labeling(session: Session, version_id: str) -> bool:
    """该版本下是否已存在任何标注（用户结果不能被自动候选覆盖）。"""

    count = session.execute(
        select(func.count(Annotation.id))
        .join(Quote, Annotation.quote_id == Quote.id)
        .where(Quote.book_version_id == version_id)
    ).scalar_one()
    return bool(count)


def scan_and_store(
    session: Session,
    settings: Settings,
    version,  # noqa: ANN001 - BookVersion
    *,
    canonical_text: str | None = None,
    scanner_version: str = SCANNER_VERSION,
    limits: ScanLimits | None = None,
    replace: bool = True,
    auto_close_points: Sequence[AutoClosePoint] | None = None,
) -> ScanOutcome:
    """扫描候选引语与 Gap 并落库；返回候选数量与警告。"""

    text = canonical_text if canonical_text is not None else canonical_text_of(settings, version)
    if auto_close_points is None:
        rows = session.execute(
            select(QuoteNormalization).where(
                QuoteNormalization.book_version_id == version.id,
                QuoteNormalization.status == QuoteNormalizationStatus.ACTIVE,
            )
        ).scalars()
        auto_close_points = active_close_points(rows)
    result = scan_quotes(
        text,
        book_version_id=version.id,
        scanner_version=scanner_version,
        limits=limits,
        auto_close_points=auto_close_points,
    )
    gaps = build_gaps(
        text,
        result.quotes,
        book_version_id=version.id,
        scanner_version=scanner_version,
    )

    if replace:
        if has_user_labeling(session, version.id):
            raise ScanConflict(
                "该版本已有用户标注或更正，拒绝用重新扫描覆盖候选；请先撤销或确认人工修改。"
            )
        session.execute(delete(Gap).where(Gap.book_version_id == version.id))
        session.execute(delete(Quote).where(Quote.book_version_id == version.id))
        session.flush()

    chapters = list(
        session.execute(
            select(Chapter).where(Chapter.book_version_id == version.id).order_by(Chapter.ordinal)
        ).scalars()
    )

    def chapter_id_for(position_cp: int) -> str | None:
        for chapter in chapters:
            if chapter.start_cp <= position_cp < chapter.end_cp:
                return chapter.id
        return chapters[-1].id if chapters else None

    for quote in result.quotes:
        session.add(
            Quote(
                id=quote.quote_id,
                book_version_id=version.id,
                chapter_id=chapter_id_for(quote.start_cp),
                start_cp=quote.start_cp,
                end_cp=quote.end_cp,
                delimiter=quote.delimiter,
                nesting_depth=quote.nesting_depth,
                parent_quote_id=quote.parent_quote_id,
                utterance_id=None,
                scanner_version=scanner_version,
                kind_hint=quote.kind_hint,
                normalized=quote.normalized,
            )
        )
    session.flush()

    for gap in gaps:
        session.add(
            Gap(
                id=gap.gap_id,
                book_version_id=version.id,
                left_quote_id=gap.left_quote_id,
                right_quote_id=gap.right_quote_id,
                start_cp=gap.start_cp,
                end_cp=gap.end_cp,
                proposed_decision=gap.decision,
            )
        )
    session.flush()
    # 候选/Gap 变更后立刻失效窗口材料缓存，保证后续读取不使用旧数据。
    invalidate_version(version.id)

    warnings = tuple(
        ScanWarningOut(
            code=warning.code,
            position_cp=warning.position_cp,
            delimiter=warning.delimiter,
            detail=warning.detail,
        )
        for warning in result.warnings
    )
    return ScanOutcome(
        scanner_version=scanner_version,
        quotes=len(result.quotes),
        top_level_quotes=sum(1 for quote in result.quotes if quote.nesting_depth == 0),
        gaps=len(gaps),
        warnings=warnings,
        stats=result.stats,
    )


def _chapters(session: Session, version_id: str) -> list[Chapter]:
    return list(
        session.execute(
            select(Chapter).where(Chapter.book_version_id == version_id).order_by(Chapter.ordinal)
        ).scalars()
    )


def _quote_out(quote: Quote, text: str, chapter: Chapter | None) -> QuoteOut:
    opening = _opening_of(quote)
    closing = _closing_of(quote)
    return QuoteOut(
        quote_id=quote.id,
        book_version_id=quote.book_version_id,
        chapter_id=quote.chapter_id,
        chapter_ordinal=chapter.ordinal if chapter is not None else None,
        start_cp=quote.start_cp,
        end_cp=quote.end_cp,
        text=text[
            quote.start_cp + len(opening) : quote.end_cp - len(closing)
        ],
        delimited_text=(
            text[quote.start_cp : quote.end_cp] + closing
            if quote.normalized
            else text[quote.start_cp : quote.end_cp]
        ),
        delimiter=quote.delimiter,
        opening=opening,
        closing=closing,
        nesting_depth=quote.nesting_depth,
        parent_quote_id=quote.parent_quote_id,
        kind_hint=quote.kind_hint,
        scanner_version=quote.scanner_version,
        normalized=quote.normalized,
    )


def _opening_of(quote: Quote) -> str:
    for pair in DELIMITER_PAIRS:
        if pair.name == quote.delimiter:
            return pair.opening
    return ""


def _closing_of(quote: Quote) -> str:
    for pair in DELIMITER_PAIRS:
        if pair.name == quote.delimiter:
            return pair.closing
    return ""


def _gap_out(gap: Gap, text: str) -> GapOut:
    narration = text[gap.start_cp : gap.end_cp]
    return GapOut(
        gap_id=gap.id,
        book_version_id=gap.book_version_id,
        left_quote_id=gap.left_quote_id,
        right_quote_id=gap.right_quote_id,
        start_cp=gap.start_cp,
        end_cp=gap.end_cp,
        narration=narration,
        paragraph_count=narration.count("\n"),
        decision=gap.proposed_decision,
    )


def list_quotes(
    session: Session,
    settings: Settings,
    version,  # noqa: ANN001
    *,
    chapter_id: str | None = None,
    cursor: str | None = None,
    limit: int = 200,
    canonical_text: str | None = None,
) -> tuple[list[QuoteOut], str | None]:
    text = canonical_text if canonical_text is not None else canonical_text_of(settings, version)
    chapters = {chapter.id: chapter for chapter in _chapters(session, version.id)}

    stmt = select(Quote).where(Quote.book_version_id == version.id)
    if chapter_id is not None:
        stmt = stmt.where(Quote.chapter_id == chapter_id)
    if cursor:
        parts = decode_cursor(cursor)
        if not parts:
            raise ApiError.validation("cursor 内容为空")
        stmt = stmt.where(Quote.start_cp > int(parts[0]))
    stmt = stmt.order_by(Quote.start_cp).limit(limit + 1)

    rows = list(session.execute(stmt).scalars())
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [_quote_out(row, text, chapters.get(row.chapter_id or "")) for row in rows]
    next_cursor = encode_cursor([rows[-1].start_cp]) if has_more and rows else None
    return items, next_cursor


def get_quote_detail(
    session: Session,
    settings: Settings,
    version,  # noqa: ANN001
    quote_id: str,
    *,
    context_window_cp: int = CONTEXT_WINDOW_CP,
    canonical_text: str | None = None,
) -> QuoteDetailOut:
    text = canonical_text if canonical_text is not None else canonical_text_of(settings, version)
    quote = session.get(Quote, quote_id)
    if quote is None or quote.book_version_id != version.id:
        raise ApiError.not_found("候选对白不存在", quote_id=quote_id)

    chapters = {chapter.id: chapter for chapter in _chapters(session, version.id)}
    previous = session.execute(
        select(Quote)
        .where(Quote.book_version_id == version.id, Quote.start_cp < quote.start_cp)
        .order_by(Quote.start_cp.desc())
        .limit(1)
    ).scalar_one_or_none()
    following = session.execute(
        select(Quote)
        .where(Quote.book_version_id == version.id, Quote.start_cp > quote.start_cp)
        .order_by(Quote.start_cp)
        .limit(1)
    ).scalar_one_or_none()
    gap = session.execute(
        select(Gap).where(Gap.right_quote_id == quote.id).limit(1)
    ).scalar_one_or_none()

    return QuoteDetailOut(
        quote=_quote_out(quote, text, chapters.get(quote.chapter_id or "")),
        previous_quote_id=previous.id if previous is not None else None,
        next_quote_id=following.id if following is not None else None,
        gap_before=_gap_out(gap, text) if gap is not None else None,
        context_before=text[max(0, quote.start_cp - context_window_cp) : quote.start_cp],
        context_after=text[quote.end_cp : quote.end_cp + context_window_cp],
    )


def list_gaps(
    session: Session,
    settings: Settings,
    version,  # noqa: ANN001
    *,
    cursor: str | None = None,
    limit: int = 200,
    canonical_text: str | None = None,
) -> tuple[list[GapOut], str | None]:
    text = canonical_text if canonical_text is not None else canonical_text_of(settings, version)
    stmt = select(Gap).where(Gap.book_version_id == version.id)
    if cursor:
        parts = decode_cursor(cursor)
        if not parts:
            raise ApiError.validation("cursor 内容为空")
        stmt = stmt.where(Gap.start_cp > int(parts[0]))
    stmt = stmt.order_by(Gap.start_cp).limit(limit + 1)

    rows = list(session.execute(stmt).scalars())
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [_gap_out(row, text) for row in rows]
    next_cursor = encode_cursor([rows[-1].start_cp]) if has_more and rows else None
    return items, next_cursor


def locate(
    session: Session,
    settings: Settings,
    version,  # noqa: ANN001
    *,
    start_cp: int,
    end_cp: int,
    canonical_text: str | None = None,
) -> LocateOut:
    """把一段 canonical 码点范围映射回章节/节点/源文档（不含任何模型调用）。"""

    text = canonical_text if canonical_text is not None else canonical_text_of(settings, version)
    if start_cp < 0 or end_cp > version.canonical_length_cp or start_cp > end_cp:
        raise ApiError.validation(
            "范围不合法",
            start_cp=start_cp,
            end_cp=end_cp,
            canonical_length_cp=version.canonical_length_cp,
        )

    stmt = (
        select(TextMapping)
        .where(
            TextMapping.book_version_id == version.id,
            TextMapping.canonical_end_cp > start_cp,
            TextMapping.canonical_start_cp < end_cp,
        )
        .order_by(TextMapping.ordinal)
    )
    mappings = list(session.execute(stmt).scalars())
    chapters = {chapter.id: chapter for chapter in _chapters(session, version.id)}
    chapter_ids = {mapping.chapter_id for mapping in mappings if mapping.chapter_id}

    nodes: dict[tuple[str, str], ContentNode] = {}
    if chapter_ids:
        for node in session.execute(
            select(ContentNode).where(ContentNode.chapter_id.in_(chapter_ids))
        ).scalars():
            nodes[(node.chapter_id, node.node_id)] = node

    spans: list[LocateSpanOut] = []
    for mapping in mappings:
        node = (
            nodes.get((mapping.chapter_id or "", mapping.node_id or ""))
            if mapping.node_id
            else None
        )
        spans.append(
            LocateSpanOut(
                mapping_ordinal=mapping.ordinal,
                chapter_id=mapping.chapter_id,
                chapter_ordinal=(
                    chapters[mapping.chapter_id].ordinal
                    if mapping.chapter_id in chapters
                    else None
                ),
                node_id=mapping.node_id,
                node_type=node.node_type if node is not None else None,
                canonical_start_cp=mapping.canonical_start_cp,
                canonical_end_cp=mapping.canonical_end_cp,
                source_href=mapping.source_href,
                source_text_start_cp=mapping.source_text_start_cp,
                source_text_end_cp=mapping.source_text_end_cp,
                synthetic=mapping.synthetic,
                text=text[mapping.canonical_start_cp : mapping.canonical_end_cp],
            )
        )

    return LocateOut(
        book_id=version.book_id,
        book_version_id=version.id,
        start_cp=start_cp,
        end_cp=end_cp,
        text=text[start_cp:end_cp],
        spans=spans,
    )
