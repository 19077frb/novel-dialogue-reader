"""书籍、版本、章节、内容节点与资源（DEVELOPMENT.md 3.2）。"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...domain.enums import BookFormat, ContentNodeType, ImportStatus
from ..base import Base, IdMixin, TimestampMixin, VersionMixin, enum_type


class Book(IdMixin, TimestampMixin, VersionMixin, Base):
    """书籍元数据；导入状态与推理状态分离，阅读位置不触发模型调用。"""

    __tablename__ = "books"

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    format: Mapped[BookFormat] = mapped_column(
        enum_type(BookFormat, name="book_format"), nullable=False
    )
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # 逻辑引用 book_versions.id。SQLite 下与 book_versions 互为引用，SQLAlchemy/Alembic
    # 会报循环依赖且 SQLite 不支持 ADD CONSTRAINT，因此这里不建外键约束，只保留索引。
    active_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    import_status: Mapped[ImportStatus] = mapped_column(
        enum_type(ImportStatus, name="import_status"),
        nullable=False,
        default=ImportStatus.PENDING,
    )
    read_position_cp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class BookVersion(IdMixin, TimestampMixin, Base):
    """原文版本：不可变。更换编码或解析器产生新版本（DEVELOPMENT.md 3.2）。"""

    __tablename__ = "book_versions"
    __table_args__ = (
        UniqueConstraint(
            "book_id",
            "canonical_sha256",
            "parser_version",
            "normalization_version",
            name="uq_book_versions_identity",
        ),
    )

    book_id: Mapped[str] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    encoding: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    normalization_version: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    canonical_length_cp: Mapped[int] = mapped_column(Integer, nullable=False)
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")


class Chapter(IdMixin, TimestampMixin, Base):
    """章节：ordinal 在同一个书籍版本内唯一且按阅读顺序。"""

    __tablename__ = "chapters"
    __table_args__ = (
        UniqueConstraint("book_version_id", "ordinal", name="uq_chapters_book_version_id_ordinal"),
    )

    book_version_id: Mapped[str] = mapped_column(
        ForeignKey("book_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    start_cp: Mapped[int] = mapped_column(Integer, nullable=False)
    end_cp: Mapped[int] = mapped_column(Integer, nullable=False)
    source_href: Mapped[str | None] = mapped_column(String(1024), nullable=True)


class ContentNode(IdMixin, TimestampMixin, Base):
    """统一文档树节点；非正文节点可以没有正文范围。"""

    __tablename__ = "content_nodes"
    __table_args__ = (
        UniqueConstraint("chapter_id", "node_id", name="uq_content_nodes_chapter_id_node_id"),
    )

    chapter_id: Mapped[str] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_id: Mapped[str] = mapped_column(String(128), nullable=False)
    node_type: Mapped[ContentNodeType] = mapped_column(
        enum_type(ContentNodeType, name="content_node_type"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tree_json: Mapped[str] = mapped_column(Text, nullable=False)
    start_cp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_cp: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Resource(IdMixin, TimestampMixin, Base):
    """登记过的书籍资源；只允许读取书籍目录内的登记项。"""

    __tablename__ = "resources"
    __table_args__ = (
        UniqueConstraint(
            "book_version_id", "resource_id", name="uq_resources_book_version_id_resource_id"
        ),
    )

    book_version_id: Mapped[str] = mapped_column(
        ForeignKey("book_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
