"""标注投影路由。

`GET /api/books/{id}/annotations`：按范围/版本/阅读模式返回有效投影（颜色、编号、图例、统计）。
初读模式下 `visible_horizon_cp` 之后的证据不下发颜色与编号（默认取请求范围的末端）。
只读：不写数据库、不调用模型；翻页不会触发重新推理。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from ..domain.annotations import AnnotationsResponse
from ..domain.common import DataEnvelope
from ..domain.enums import ErrorCode, ReadingMode
from ..ingest.query import active_version, get_book_or_404
from ..scenes.projection import build_projection
from .deps import get_session
from .errors import ApiError, current_request_id

router = APIRouter(prefix="/books", tags=["annotations"])


@router.get(
    "/{book_id}/annotations",
    response_model=DataEnvelope[AnnotationsResponse],
    summary="有效标注投影（范围内颜色/编号/图例/统计）",
)
def annotations_route(
    request: Request,
    book_id: str,
    start_cp: int = Query(default=0, ge=0),
    end_cp: int | None = Query(default=None, ge=1),
    reading_mode: ReadingMode = Query(default=ReadingMode.INITIAL),
    visible_horizon_cp: int | None = Query(default=None, ge=0),
    session: Session = Depends(get_session),
) -> DataEnvelope[AnnotationsResponse]:
    book = get_book_or_404(session, book_id)
    version = active_version(session, book)
    if version is None:
        raise ApiError(
            ErrorCode.NOT_FOUND,
            "书籍还没有可用版本",
            details={"book_id": book_id},
            status_code=409,
        )
    resolved_end = version.canonical_length_cp if end_cp is None else end_cp
    if start_cp >= resolved_end or resolved_end > version.canonical_length_cp:
        raise ApiError.validation(
            "范围不合法",
            start_cp=start_cp,
            end_cp=resolved_end,
            canonical_length_cp=version.canonical_length_cp,
        )
    horizon = visible_horizon_cp
    if horizon is None and reading_mode is ReadingMode.INITIAL:
        # 默认以请求范围末端为可见 horizon：范围内结果可见，范围之后不提前泄漏
        horizon = resolved_end
    response = build_projection(
        session,
        book_id=book.id,
        book_version_id=version.id,
        start_cp=start_cp,
        end_cp=resolved_end,
        reading_mode=reading_mode,
        visible_horizon_cp=horizon,
    )
    return DataEnvelope(data=response, request_id=current_request_id(request))
