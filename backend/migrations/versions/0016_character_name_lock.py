"""Distinguish manually chosen names from automatically confirmed rosters."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("book_characters", sa.Column(
        "name_locked", sa.Boolean(), nullable=False, server_default=sa.false(),
    ))


def downgrade() -> None:
    op.drop_column("book_characters", "name_locked")
