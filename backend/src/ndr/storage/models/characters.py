"""Book-level characters and per-chapter confirmed rosters.

The model may propose a chapter roster; attribution requires accepted people
and POV. Acceptance can be manual or automatic, without conflating their origin.
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...domain.enums import CharacterRosterStatus, CharacterSource
from ..base import Base, IdMixin, TimestampMixin, VersionMixin, enum_type


class BookCharacter(IdMixin, TimestampMixin, VersionMixin, Base):
    """A person in one immutable book version, shared across chapters."""

    __tablename__ = "book_characters"
    __table_args__ = (
        UniqueConstraint("book_version_id", "temp_key", name="uq_book_characters_temp_key"),
    )

    book_version_id: Mapped[str] = mapped_column(
        ForeignKey("book_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    temp_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    canonical_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    aliases_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source: Mapped[CharacterSource] = mapped_column(
        enum_type(CharacterSource, name="character_source"),
        nullable=False,
        default=CharacterSource.MODEL,
    )
    user_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    name_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confirmation_source: Mapped[str] = mapped_column(String(16), nullable=False, default="model")
    first_seen_cp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preferred_color_index: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ChapterCharacterRoster(IdMixin, TimestampMixin, VersionMixin, Base):
    """Draft/confirmed chapter roster and the user-selected POV character."""

    __tablename__ = "chapter_character_rosters"
    __table_args__ = (
        UniqueConstraint("chapter_id", name="uq_chapter_character_rosters_chapter_id"),
    )

    chapter_id: Mapped[str] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    book_version_id: Mapped[str] = mapped_column(
        ForeignKey("book_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[CharacterRosterStatus] = mapped_column(
        enum_type(CharacterRosterStatus, name="character_roster_status"),
        nullable=False,
        default=CharacterRosterStatus.DRAFT,
    )
    candidates_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    confirmed_character_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    pov_character_id: Mapped[str | None] = mapped_column(
        ForeignKey("book_characters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    analysis_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
