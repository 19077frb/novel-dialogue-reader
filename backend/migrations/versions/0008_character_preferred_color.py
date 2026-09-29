"""Persist preferred colors for annotations restored from exported EPUB files.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("book_characters", schema=None) as batch_op:
        batch_op.add_column(sa.Column("preferred_color_index", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("book_characters", schema=None) as batch_op:
        batch_op.drop_column("preferred_color_index")
