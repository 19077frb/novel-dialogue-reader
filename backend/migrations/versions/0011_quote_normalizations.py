"""Add editable virtual quote normalization points.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import ndr.storage.types  # noqa: F401 - UtcDateTime 列类型需要模块可见

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "quotes",
        sa.Column("normalized", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "quote_normalizations",
        sa.Column("opening_cp", sa.Integer(), nullable=False),
        sa.Column("close_cp", sa.Integer(), nullable=False),
        sa.Column("replacement", sa.String(length=8), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "AUTO", "USER", name="quote_normalization_source", native_enum=False
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "DISABLED",
                name="quote_normalization_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", ndr.storage.types.UtcDateTime(), nullable=False),
        sa.Column("updated_at", ndr.storage.types.UtcDateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("book_version_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["book_version_id"],
            ["book_versions.id"],
            name=op.f("fk_quote_normalizations_book_version_id_book_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quote_normalizations")),
        sa.UniqueConstraint(
            "book_version_id", "opening_cp", name=op.f("uq_quote_normalizations_opening")
        ),
    )
    op.create_index(
        op.f("ix_quote_normalizations_book_version_id"),
        "quote_normalizations",
        ["book_version_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_quote_normalizations_book_version_id"),
        table_name="quote_normalizations",
    )
    op.drop_table("quote_normalizations")
    op.drop_column("quotes", "normalized")
