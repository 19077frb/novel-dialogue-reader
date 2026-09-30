"""手动书签与自动阅读进度独立；位置始终绑定不可变原文版本。"""

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, IdMixin, TimestampMixin, VersionMixin


class Bookmark(IdMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "bookmarks"
    __table_args__ = (Index("ix_bookmarks_book_created_id", "book_id", "created_at", "id"),)

    book_id: Mapped[str] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    book_version_id: Mapped[str] = mapped_column(
        ForeignKey("book_versions.id", ondelete="CASCADE"), index=True
    )
    chapter_id: Mapped[str] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), index=True
    )
    position_cp: Mapped[int] = mapped_column(Integer)
    excerpt: Mapped[str] = mapped_column(String(160))
    note: Mapped[str] = mapped_column(String(512), default="")
