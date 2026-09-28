"""标注、标注历史与身份修订。"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...domain.enums import (
    AnnotationSource,
    AnnotationStatus,
    Assignment,
    IdentityOperation,
    QuoteKind,
    SpeakerBasis,
)
from ..base import Base, IdMixin, TimestampMixin, VersionMixin, enum_type


class Annotation(IdMixin, TimestampMixin, VersionMixin, Base):
    """当前标注投影；每个 quote 只有一个当前版本，旧结果进入 annotation_history。

    ``stale`` 与 ``user_locked`` 是独立字段：UNKNOWN、用户锁定与过期状态能分别表达，
    不能被合并成一个“状态”。
    """

    __tablename__ = "annotations"
    __table_args__ = (UniqueConstraint("quote_id", name="uq_annotations_quote_id"),)

    quote_id: Mapped[str] = mapped_column(
        ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scene_id: Mapped[str | None] = mapped_column(
        ForeignKey("scenes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    kind: Mapped[QuoteKind] = mapped_column(
        enum_type(QuoteKind, name="annotation_kind"), nullable=False
    )
    assignment: Mapped[Assignment | None] = mapped_column(
        enum_type(Assignment, name="annotation_assignment"), nullable=True
    )
    basis: Mapped[SpeakerBasis | None] = mapped_column(
        enum_type(SpeakerBasis, name="annotation_basis"), nullable=True
    )
    speaker_id: Mapped[str | None] = mapped_column(
        ForeignKey("speaker_groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[AnnotationStatus] = mapped_column(
        enum_type(AnnotationStatus, name="annotation_status"),
        nullable=False,
        default=AnnotationStatus.PROVISIONAL,
    )
    source: Mapped[AnnotationSource] = mapped_column(
        enum_type(AnnotationSource, name="annotation_source"), nullable=False
    )
    evidence_refs_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    visible_from_cp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dependency_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    user_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AnnotationHistory(IdMixin, TimestampMixin, Base):
    """旧结果快照；支持撤销与按证据时点展示。"""

    __tablename__ = "annotation_history"
    __table_args__ = (
        UniqueConstraint(
            "annotation_id", "revision", name="uq_annotation_history_annotation_id_revision"
        ),
    )

    annotation_id: Mapped[str] = mapped_column(
        ForeignKey("annotations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    visible_from_cp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    correction_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class IdentityRevision(IdMixin, TimestampMixin, VersionMixin, Base):
    """merge/split 记录；可撤销且不破坏历史投影。"""

    __tablename__ = "identity_revisions"

    scene_id: Mapped[str] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    operation: Mapped[IdentityOperation] = mapped_column(
        enum_type(IdentityOperation, name="identity_operation"), nullable=False
    )
    input_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    output_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    evidence_refs_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    visible_from_cp: Mapped[int | None] = mapped_column(Integer, nullable=True)
