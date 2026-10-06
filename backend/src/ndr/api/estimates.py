"""估算与用量路由。

- `POST /api/books/{id}/estimates`：纯本地估算（不调用模型、不写数据库）。
- `GET /api/books/{id}/usage`：按任务汇总真实用量；未知用量单独计数。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..context.budget import policy_for_version
from ..context.full_source import FullContextError
from ..domain.common import DataEnvelope
from ..domain.jobs import EstimateIn, EstimateOut, UsageOut
from ..ingest.query import active_version, get_book_or_404
from ..jobs.service import estimate_inference, usage_summary
from ..storage.models import BookVersion
from .deps import get_session
from .errors import ApiError, current_request_id

router = APIRouter(prefix="/books", tags=["jobs"])


@router.post(
    "/{book_id}/estimates",
    response_model=DataEnvelope[EstimateOut],
    summary="本地估算（不调用模型）",
)
def estimate_route(
    request: Request,
    book_id: str,
    payload: EstimateIn,
    session: Session = Depends(get_session),
) -> DataEnvelope[EstimateOut]:
    settings = request.app.state.settings
    book = get_book_or_404(session, book_id)
    version = (
        session.get(BookVersion, payload.book_version_id)
        if payload.book_version_id
        else active_version(session, book)
    )
    if version is None or version.book_id != book.id:
        raise ApiError.validation(
            "书籍版本不存在或不属于该书籍", book_version_id=payload.book_version_id
        )

    try:
        estimate = estimate_inference(
            session,
            settings,
            version,
            start_cp=int(payload.range.get("start_cp", 0) or 0),
            end_cp=payload.range.get("end_cp"),
            reading_mode=payload.reading_mode,
            visible_horizon_cp=payload.visible_horizon_cp,
            max_recheck_rounds=payload.budget.max_recheck_rounds or 0,
            policy=policy_for_version(payload.range.get("context_policy")),
            review_protocol=payload.range.get("review_protocol"),
        )
    except FullContextError as exc:
        raise ApiError.validation(str(exc)) from exc
    return DataEnvelope(
        data=EstimateOut(book_id=book.id, book_version_id=version.id, **estimate.as_dict()),
        request_id=current_request_id(request),
    )


@router.get("/{book_id}/usage", response_model=DataEnvelope[UsageOut], summary="按任务汇总用量")
def usage_route(
    request: Request,
    book_id: str,
    session: Session = Depends(get_session),
) -> DataEnvelope[UsageOut]:
    book = get_book_or_404(session, book_id)
    summary = usage_summary(session, book.id)
    return DataEnvelope(data=UsageOut(**summary), request_id=current_request_id(request))
