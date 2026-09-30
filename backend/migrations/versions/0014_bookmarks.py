"""Separate manual bookmarks from automatic reading progress."""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bookmarks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("book_id", sa.String(36), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("book_version_id", sa.String(36), sa.ForeignKey("book_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_id", sa.String(36), sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position_cp", sa.Integer(), nullable=False),
        sa.Column("excerpt", sa.String(160), nullable=False),
        sa.Column("note", sa.String(512), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_bookmarks_book_created_id", "bookmarks", ["book_id", "created_at", "id"])
    op.create_index("ix_bookmarks_book_version_id", "bookmarks", ["book_version_id"])
    op.create_index("ix_bookmarks_chapter_id", "bookmarks", ["chapter_id"])


def downgrade() -> None:
    op.drop_table("bookmarks")
