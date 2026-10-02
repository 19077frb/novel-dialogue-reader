"""Persist manual chapter completion choices while existing jobs finish."""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chapters", sa.Column("processing_status_override", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("chapters", "processing_status_override")
