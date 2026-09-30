"""待确认队列与人工更正。

- ``review_items`` 的目标恰好一种：quote_id 或 gap_id，由 CHECK 约束保证。
- ``corrections`` 保存前值/后值、期望版本与实际应用版本；撤销通过 ``undone_by`` 留痕，
  历史不被硬删除。
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...domain.enums import (
    CorrectionAction,
    CorrectionTargetType,
    ReviewQueueStatus,
    ReviewReason,
    ReviewTargetType,
)
from ..base import Base, IdMixin, TimestampMixin, VersionMixin, enum_type


class ReviewItem(IdMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "review_items"
    __table_args__ = (
        CheckConstraint("(quote_id IS NULL) <> (gap_id IS NULL)", name="exactly_one_target"),
        UniqueConstraint("quote_id", "reason", name="uq_review_items_quote_id_reason"),
        UniqueConstraint("gap_id", "reason", name="uq_review_items_gap_id_reason"),
        Index("ix_review_items_created_id", "created_at", "id"),
    )

    target_type: Mapped[ReviewTargetType] = mapped_column(
        enum_type(ReviewTargetType, name="review_target_type"), nullable=False
    )
    quote_id: Mapped[str | None] = mapped_column(
        ForeignKey("quotes.id", ondelete="CASCADE"), nullable=True, index=True
    )
    gap_id: Mapped[str | None] = mapped_column(
        ForeignKey("gaps.id", ondelete="CASCADE"), nullable=True, index=True
    )
    reason: Mapped[ReviewReason] = mapped_column(
        enum_type(ReviewReason, name="review_reason"), nullable=False
    )
    candidates_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    queue_status: Mapped[ReviewQueueStatus] = mapped_column(
        enum_type(ReviewQueueStatus, name="review_queue_status"),
        nullable=False,
        default=ReviewQueueStatus.PENDING,
        index=True,
    )
    annotation_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolved_by_correction_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class Correction(IdMixin, TimestampMixin, Base):
    __tablename__ = "corrections"

    target_type: Mapped[CorrectionTargetType] = mapped_column(
        enum_type(CorrectionTargetType, name="correction_target_type"),
        nullable=False,
        index=True,
    )
    target_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    action: Mapped[CorrectionAction] = mapped_column(
        enum_type(CorrectionAction, name="correction_action"), nullable=False
    )
    before_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    after_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    expected_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    applied_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    undone_by: Mapped[str | None] = mapped_column(
        ForeignKey("corrections.id", ondelete="SET NULL"), nullable=True, index=True
    )
