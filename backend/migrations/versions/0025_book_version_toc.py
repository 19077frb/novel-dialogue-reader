"""Preserve publication navigation independently of chapter boundaries."""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("book_versions", sa.Column("toc_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("book_versions", "toc_json")
