"""任务、窗口、推理尝试与结果缓存。

- 任务状态与推理尝试状态分开；未知用量保持 NULL，绝不写 0。
- 幂等键唯一；同一摘要重复创建由服务层返回既有任务，不同摘要返回 409。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...domain.enums import InferenceRunState, JobKind, JobPurpose, JobState
from ..base import Base, IdMixin, TimestampMixin, VersionMixin, enum_type
from ..types import UtcDateTime


class Job(IdMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_jobs_idempotency_key"),
        Index("ix_jobs_book_created_id", "book_id", "created_at", "id"),
        Index("ix_jobs_version_state", "book_version_id", "state"),
        Index("ix_jobs_digest_state_created", "request_digest", "state", "created_at"),
    )

    kind: Mapped[JobKind] = mapped_column(enum_type(JobKind, name="job_kind"), nullable=False)
    purpose: Mapped[JobPurpose | None] = mapped_column(
        enum_type(JobPurpose, name="job_purpose"), nullable=True
    )
    book_id: Mapped[str | None] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=True, index=True
    )
    book_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("book_versions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    range_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    profile_snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[JobState] = mapped_column(
        enum_type(JobState, name="job_state"), nullable=False, default=JobState.QUEUED, index=True
    )
    budget_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    checkpoint_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)


class JobWindow(IdMixin, TimestampMixin, Base):
    """处理窗口；同一窗口只允许一次有效提交。"""

    __tablename__ = "job_windows"
    __table_args__ = (
        UniqueConstraint("job_id", "window_id", name="uq_job_windows_job_id_window_id"),
    )

    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    window_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    dependency_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[JobState] = mapped_column(
        enum_type(JobState, name="job_window_state"), nullable=False, default=JobState.QUEUED
    )
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class InferenceRun(IdMixin, TimestampMixin, Base):
    """每次远程尝试（含格式重试与复核）；usage 缺失时保持 NULL。"""

    __tablename__ = "inference_runs"

    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    window_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    profile_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    remote_request_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    state: Mapped[InferenceRunState] = mapped_column(
        enum_type(InferenceRunState, name="inference_run_state"), nullable=False
    )
    usage_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ResultCache(IdMixin, TimestampMixin, Base):
    """语义输入相同即复用；缓存键不包含 job_id、purpose 与显示选项。"""

    __tablename__ = "result_cache"
    __table_args__ = (UniqueConstraint("cache_key", name="uq_result_cache_cache_key"),)

    cache_key: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    dependency_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
