"""任务查询路由（T02 最小轮询；T10 扩展 usage/剩余范围）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..domain.common import DataEnvelope
from ..domain.documents import JobOut
from ..ingest.query import job_out
from ..storage.models import Job
from .deps import get_session
from .errors import ApiError, current_request_id

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=DataEnvelope[JobOut], summary="任务状态")
def get_job_route(
    request: Request,
    job_id: str,
    session: Session = Depends(get_session),
) -> DataEnvelope[JobOut]:
    job = session.get(Job, job_id)
    if job is None:
        raise ApiError.not_found("任务不存在", job_id=job_id)
    return DataEnvelope(data=job_out(job), request_id=current_request_id(request))
