"""Durable receipts for original and coalesced job creation requests."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.errors import ApiError
from ..domain.enums import ErrorCode
from ..storage.models import Job, JobRequestKey


def _conflict(key: str, job_id: str) -> ApiError:
    return ApiError(
        ErrorCode.IDEMPOTENCY_CONFLICT,
        "同一幂等键已被不同的请求使用",
        details={"idempotency_key": key, "job_id": job_id},
        status_code=409,
    )


def find_request_job(session: Session, key: str, digest: str) -> Job | None:
    receipt = session.get(JobRequestKey, key)
    if receipt is not None:
        job = session.get(Job, receipt.job_id)
        # The legacy original column remains authoritative for old callers that
        # restore a historical request digest. Aliases retain their own digest.
        stored_digest = (job.request_digest if job.idempotency_key == key
                         else receipt.request_digest)
        if stored_digest != digest:
            raise _conflict(key, receipt.job_id)
        return job

    # Metadata-created test databases and legacy callers may have an original
    # key without the migration's registration trigger having populated it.
    job = session.scalar(select(Job).where(Job.idempotency_key == key))
    if job is not None and job.request_digest != digest:
        raise _conflict(key, job.id)
    return job


def bind_request_key(session: Session, job: Job, key: str, digest: str) -> None:
    """Bind within the admission transaction. The caller owns commit/rollback."""
    existing = find_request_job(session, key, digest)
    if existing is not None and existing.id != job.id:
        raise _conflict(key, existing.id)
    if session.get(JobRequestKey, key) is None:
        session.add(JobRequestKey(idempotency_key=key, job_id=job.id, request_digest=digest))
        session.flush()
