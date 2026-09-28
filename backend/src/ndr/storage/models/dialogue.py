"""引语、Gap、场景与说话人分组。

引用 ID 由“原文版本 + 位置 + 扫描器版本”稳定派生，
``uq_quotes_position`` 用唯一约束把这条规则固化在数据库里。
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...domain.enums import GapDecision, QuoteKind, SceneStatus
from ..base import Base, IdMixin, TimestampMixin, VersionMixin, enum_type


class Quote(IdMixin, TimestampMixin, Base):
    """候选引语；扫描器只提出候选，不代表已判定为对白。"""

    __tablename__ = "quotes"
    __table_args__ = (
        UniqueConstraint(
            "book_version_id",
            "start_cp",
            "end_cp",
            "scanner_version",
            name="uq_quotes_position",
        ),
    )

    book_version_id: Mapped[str] = mapped_column(
        ForeignKey("book_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_id: Mapped[str | None] = mapped_column(
        ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    start_cp: Mapped[int] = mapped_column(Integer, nullable=False)
    end_cp: Mapped[int] = mapped_column(Integer, nullable=False)
    delimiter: Mapped[str] = mapped_column(String(16), nullable=False)
    nesting_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parent_quote_id: Mapped[str | None] = mapped_column(
        ForeignKey("quotes.id", ondelete="SET NULL"), nullable=True
    )
    utterance_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    scanner_version: Mapped[str] = mapped_column(String(64), nullable=False)
    kind_hint: Mapped[QuoteKind | None] = mapped_column(
        enum_type(QuoteKind, name="quote_kind"), nullable=True
    )


class Gap(IdMixin, TimestampMixin, Base):
    """相邻外层候选对白之间的叙述；允许跨章节，不能只按章节关闭。"""

    __tablename__ = "gaps"

    book_version_id: Mapped[str] = mapped_column(
        ForeignKey("book_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    left_quote_id: Mapped[str | None] = mapped_column(
        ForeignKey("quotes.id", ondelete="SET NULL"), nullable=True
    )
    right_quote_id: Mapped[str | None] = mapped_column(
        ForeignKey("quotes.id", ondelete="SET NULL"), nullable=True
    )
    start_cp: Mapped[int] = mapped_column(Integer, nullable=False)
    end_cp: Mapped[int] = mapped_column(Integer, nullable=False)
    proposed_decision: Mapped[GapDecision] = mapped_column(
        enum_type(GapDecision, name="gap_decision"),
        nullable=False,
        default=GapDecision.UNCERTAIN,
    )


class Scene(IdMixin, TimestampMixin, VersionMixin, Base):
    """交谈关系连续的一组发言；边界变更保留历史。"""

    __tablename__ = "scenes"

    book_version_id: Mapped[str] = mapped_column(
        ForeignKey("book_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    start_cp: Mapped[int] = mapped_column(Integer, nullable=False)
    end_cp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[SceneStatus] = mapped_column(
        enum_type(SceneStatus, name="scene_status"), nullable=False, default=SceneStatus.OPEN
    )


class SceneMembership(IdMixin, TimestampMixin, Base):
    """引语到场景的归属；场景修订不直接丢弃历史。"""

    __tablename__ = "scene_memberships"

    quote_id: Mapped[str] = mapped_column(
        ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scene_id: Mapped[str] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    valid_from_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    valid_to_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SpeakerGroup(IdMixin, TimestampMixin, VersionMixin, Base):
    """场景内匿名说话人分组；展示编号只在所属场景内有意义。"""

    __tablename__ = "speaker_groups"
    __table_args__ = (
        UniqueConstraint(
            "scene_id", "display_label", name="uq_speaker_groups_scene_id_display_label"
        ),
    )

    scene_id: Mapped[str] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    first_quote_id: Mapped[str | None] = mapped_column(
        ForeignKey("quotes.id", ondelete="SET NULL"), nullable=True
    )
    display_label: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    character_id: Mapped[str | None] = mapped_column(
        ForeignKey("book_characters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    evidence_refs_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")


class Participant(IdMixin, TimestampMixin, Base):
    """被提及或参与活动、但可能尚未发言的角色。"""

    __tablename__ = "participants"

    scene_id: Mapped[str] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    mention_refs_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    speaker_id: Mapped[str | None] = mapped_column(
        ForeignKey("speaker_groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
