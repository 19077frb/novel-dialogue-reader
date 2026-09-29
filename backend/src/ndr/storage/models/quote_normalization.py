"""User-visible quote delimiter normalizations; canonical text stays immutable."""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...domain.enums import QuoteNormalizationSource, QuoteNormalizationStatus
from ..base import Base, IdMixin, TimestampMixin, VersionMixin, enum_type


class QuoteNormalization(IdMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "quote_normalizations"
    __table_args__ = (
        UniqueConstraint("book_version_id", "opening_cp", name="uq_quote_normalizations_opening"),
    )

    book_version_id: Mapped[str] = mapped_column(
        ForeignKey("book_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    opening_cp: Mapped[int] = mapped_column(nullable=False)
    close_cp: Mapped[int] = mapped_column(nullable=False)
    replacement: Mapped[str] = mapped_column(String(8), nullable=False, default="”")
    source: Mapped[QuoteNormalizationSource] = mapped_column(
        enum_type(QuoteNormalizationSource, name="quote_normalization_source"),
        nullable=False,
        default=QuoteNormalizationSource.AUTO,
    )
    status: Mapped[QuoteNormalizationStatus] = mapped_column(
        enum_type(QuoteNormalizationStatus, name="quote_normalization_status"),
        nullable=False,
        default=QuoteNormalizationStatus.ACTIVE,
    )
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
