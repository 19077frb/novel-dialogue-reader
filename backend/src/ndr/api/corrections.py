"""人工更正、待确认队列与撤销路由（T12）。

契约见 docs/CONTRACTS.md 第 21 节；语义见决策 0014。

- 读：`GET /api/books/{id}/review-items`、`GET /api/review-items/{id}`。
- 队列：`POST /api/quotes/{id}/review-items`（主动标记，幂等）、
  `POST /api/review-items/{id}/defer`。
- 更正：`POST /api/quotes/{id}/corrections`、`POST /api/gaps/{id}/corrections`、
  `POST /api/scenes/{id}/speaker-revisions`、`POST /api/corrections/{id}/undo`。

这些接口**不调用模型**：只写更正/历史/队列，并标记下游 stale。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from ..corrections.identity import apply_speaker_revision, speaker_revision_out
from ..corrections.review import (
    defer_review_item,
    flag_review_item,
    get_review_item_or_404,
    list_review_items,
    review_count_map,
    review_counts,
    review_item_detail,
    review_item_out,
)
from ..corrections.service import (
    apply_gap_correction,
    apply_quote_correction,
    correction_out,
    gap_correction_out,
    get_correction_or_404,
    undo_correction,
    undo_out,
)
from ..domain.common import DataEnvelope
from ..domain.corrections import (
    CorrectionOut,
    GapCorrectionIn,
    GapCorrectionOut,
    QuoteCorrectionIn,
    ReviewDeferIn,
    ReviewFlagIn,
    ReviewItemCountsOut,
    ReviewItemDetailOut,
    ReviewItemOut,
    ReviewQueueResponse,
    SpeakerRevisionIn,
    SpeakerRevisionOut,
    UndoOut,
)
from ..domain.enums import ErrorCode, ReviewQueueStatus, ReviewReason
from ..ingest.query import active_version, get_book_or_404, load_canonical_text
from ..storage.models import Gap, Quote
from ..storage.transactions import transaction
from .deps import get_session
from .errors import ApiError, current_request_id
from .pagination import parse_limit

book_router = APIRouter(prefix="/books", tags=["corrections"])
review_router = APIRouter(prefix="/review-items", tags=["corrections"])
quotes_router = APIRouter(prefix="/quotes", tags=["corrections"])
gaps_router = APIRouter(prefix="/gaps", tags=["corrections"])
corrections_router = APIRouter(prefix="/corrections", tags=["corrections"])
scenes_router = APIRouter(prefix="/scenes", tags=["corrections"])


def _active_version_or_409(session: Session, book_id: str):  # noqa: ANN202
    book = get_book_or_404(session, book_id)
    version = active_version(session, book)
    if version is None:
        raise ApiError(
            ErrorCode.NOT_FOUND,
            "书籍还没有可用版本",
            details={"book_id": book_id},
            status_code=409,
        )
    return book, version

@book_router.get(
    "/{book_id}/review-items",
    response_model=DataEnvelope[ReviewQueueResponse],
    summary="待确认队列（按章节/场景/原因/状态过滤）",
)
def list_review_items_route(
    request: Request,
    book_id: str,
    chapter_id: str | None = Query(default=None),
    scene_id: str | None = Query(default=None),
    reason: ReviewReason | None = Query(default=None),
    queue_status: ReviewQueueStatus | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=200),
    cursor: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> DataEnvelope[ReviewQueueResponse]:
    _, version = _active_version_or_409(session, book_id)
    items, next_cursor = list_review_items(
        session,
        book_version_id=version.id,
        chapter_id=chapter_id,
        scene_id=scene_id,
        reason=reason,
        queue_status=queue_status,
        limit=parse_limit(limit, default=50, maximum=200),
        cursor=cursor,
    )
    payload = ReviewQueueResponse(
        items=items, next_cursor=next_cursor, counts=review_counts(session, version.id)
    )
    return DataEnvelope(data=payload, request_id=current_request_id(request))


@review_router.get(
    "/{item_id}",
    response_model=DataEnvelope[ReviewItemDetailOut],
    summary="待确认项详情（目标、上下文、候选、版本）",
)
def get_review_item_route(
    request: Request,
    item_id: str,
    session: Session = Depends(get_session),
) -> DataEnvelope[ReviewItemDetailOut]:
    settings = request.app.state.settings
    item = get_review_item_or_404(session, item_id)
    version_id = None
    if item.quote_id:
        quote = session.get(Quote, item.quote_id)
        version_id = quote.book_version_id if quote is not None else None
    elif item.gap_id:
        gap = session.get(Gap, item.gap_id)
        version_id = gap.book_version_id if gap is not None else None
    if version_id is None:
        raise ApiError.not_found("待确认项的目标已不存在", review_item_id=item_id)
    from ..storage.models import BookVersion

    version = session.get(BookVersion, version_id)
    if version is None:
        raise ApiError.not_found("书籍版本不存在", book_version_id=version_id)
    text = load_canonical_text(settings, version)
    detail = review_item_detail(session, item=item, canonical_text=text)
    return DataEnvelope(data=detail, request_id=current_request_id(request))


@review_router.post(
    "/{item_id}/defer",
    response_model=DataEnvelope[ReviewItemOut],
    summary="延后处理（只改队列状态，不增加确认计数）",
)
def defer_review_item_route(
    request: Request, item_id: str, payload: ReviewDeferIn | None = None
) -> DataEnvelope[ReviewItemOut]:
    factory = request.app.state.session_factory
    with transaction(factory) as session:
        item = get_review_item_or_404(session, item_id)
        out = defer_review_item(session, item=item, note=payload.note if payload else "")
    return DataEnvelope(data=out, request_id=current_request_id(request))


@quotes_router.post(
    "/{quote_id}/review-items",
    status_code=201,
    response_model=DataEnvelope[ReviewItemOut],
    summary="用户主动标记问题（幂等：同一目标 + 原因只有一条当前项）",
)
def flag_quote_review_item_route(
    request: Request, quote_id: str, payload: ReviewFlagIn
) -> DataEnvelope[ReviewItemOut]:
    factory = request.app.state.session_factory
    with transaction(factory) as session:
        item = flag_review_item(
            session, quote_id=quote_id, reason=payload.reason, note=payload.note
        )
        out = review_item_out(item)
    return DataEnvelope(data=out, request_id=current_request_id(request))

@quotes_router.post(
    "/{quote_id}/corrections",
    status_code=201,
    response_model=DataEnvelope[CorrectionOut],
    summary="当前发言人工更正（不调用模型）",
)
def quote_correction_route(
    request: Request, quote_id: str, payload: QuoteCorrectionIn
) -> DataEnvelope[CorrectionOut]:
    factory = request.app.state.session_factory
    with transaction(factory) as session:
        quote = session.get(Quote, quote_id)
        if quote is None:
            raise ApiError.not_found("候选对白不存在", quote_id=quote_id)
        outcome = apply_quote_correction(session, target=quote, payload=payload)
        counts = review_counts(session, quote.book_version_id)
        out = correction_out(outcome, counts)
    return DataEnvelope(data=out, request_id=current_request_id(request))


@gaps_router.post(
    "/{gap_id}/corrections",
    status_code=201,
    response_model=DataEnvelope[GapCorrectionOut],
    summary="确认 Gap 的 CONTINUE/UPDATE/BREAK/UNCERTAIN，返回场景修订影响",
)
def gap_correction_route(
    request: Request, gap_id: str, payload: GapCorrectionIn
) -> DataEnvelope[GapCorrectionOut]:
    factory = request.app.state.session_factory
    with transaction(factory) as session:
        gap = session.get(Gap, gap_id)
        if gap is None:
            raise ApiError.not_found("Gap 不存在", gap_id=gap_id)
        outcome = apply_gap_correction(session, gap=gap, payload=payload)
        counts = review_counts(session, gap.book_version_id)
        out = gap_correction_out(outcome, counts)
    return DataEnvelope(data=out, request_id=current_request_id(request))


@corrections_router.post(
    "/{correction_id}/undo",
    status_code=201,
    response_model=DataEnvelope[UndoOut],
    summary="撤销一次人工更正（版本校验；历史只追加不删除）",
)
def undo_correction_route(request: Request, correction_id: str) -> DataEnvelope[UndoOut]:
    factory = request.app.state.session_factory
    with transaction(factory) as session:
        correction = get_correction_or_404(session, correction_id)
        outcome = undo_correction(session, correction=correction)
        version_id = None
        if outcome.target_type.value in {"quote", "annotation"}:
            quote = session.get(Quote, outcome.target_id)
            version_id = quote.book_version_id if quote is not None else None
        elif outcome.target_type.value == "gap":
            gap = session.get(Gap, outcome.target_id)
            version_id = gap.book_version_id if gap is not None else None
        else:
            from ..storage.models import Scene

            scene = session.get(Scene, outcome.target_id)
            version_id = scene.book_version_id if scene is not None else None
        counts = (
            review_counts(session, version_id)
            if version_id
            else ReviewItemCountsOut(total=0)
        )
        out = undo_out(outcome, counts)
    return DataEnvelope(data=out, request_id=current_request_id(request))


@scenes_router.post(
    "/{scene_id}/speaker-revisions",
    status_code=201,
    response_model=DataEnvelope[SpeakerRevisionOut],
    summary="场景内说话人分组 merge / split",
)
def speaker_revision_route(
    request: Request, scene_id: str, payload: SpeakerRevisionIn
) -> DataEnvelope[SpeakerRevisionOut]:
    factory = request.app.state.session_factory
    with transaction(factory) as session:
        from ..storage.models import Scene

        scene = session.get(Scene, scene_id)
        if scene is None:
            raise ApiError.not_found("场景不存在", scene_id=scene_id)
        outcome = apply_speaker_revision(session, scene=scene, payload=payload)
        counts = review_counts(session, scene.book_version_id)
        out = speaker_revision_out(outcome, review_count_map(counts))
    return DataEnvelope(data=out, request_id=current_request_id(request))
