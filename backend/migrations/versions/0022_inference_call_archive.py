"""Retain bounded compressed inference evidence without loading it in lists."""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inference_runs", sa.Column("call_archive", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("inference_runs", "call_archive")
