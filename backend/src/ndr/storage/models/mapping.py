"""canonical 全文与源文本的映射段。

每个 canonical 行一条记录，覆盖整个 canonical 文本：
``canonical_start_cp/canonical_end_cp`` 是规范化全文中的码点范围，
``source_text_start_cp/source_text_end_cp`` 是解码后源文本中的码点范围，
``synthetic`` 表示该行的行尾是导入时规范化出来的（原文是 CRLF/CR），
``node_id`` 为 NULL 表示这一行只是段落分隔（空白行），没有可渲染节点。
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, IdMixin, TimestampMixin


class TextMapping(IdMixin, TimestampMixin, Base):
    __tablename__ = "text_mappings"
    __table_args__ = (
        Index("ix_text_mappings_version_canonical", "book_version_id", "canonical_start_cp"),
        Index("ix_text_mappings_version_ordinal", "book_version_id", "ordinal"),
    )

    book_version_id: Mapped[str] = mapped_column(
        ForeignKey("book_versions.id", ondelete="CASCADE"), nullable=False
    )
    chapter_id: Mapped[str | None] = mapped_column(
        ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 节点 ID 在同一章节内唯一；空白行的映射没有节点。
    node_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_start_cp: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_end_cp: Mapped[int] = mapped_column(Integer, nullable=False)
    source_href: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    source_text_start_cp: Mapped[int] = mapped_column(Integer, nullable=False)
    source_text_end_cp: Mapped[int] = mapped_column(Integer, nullable=False)
    synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
