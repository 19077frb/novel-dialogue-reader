"""Immutable reader presentation shared by groups in the same text version."""

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, IdMixin


class PresentationHistoryBaseline(IdMixin, Base):
    __tablename__ = "presentation_history_baselines"
    __table_args__ = (
        UniqueConstraint("book_version_id", "digest", name="uq_presentation_baseline_content"),
    )

    book_version_id: Mapped[str] = mapped_column(
        ForeignKey("book_versions.id", ondelete="CASCADE"), nullable=False,
    )
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    history_json: Mapped[str] = mapped_column(Text, nullable=False)
