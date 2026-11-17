"""Record an optional registered cover resource for each book version."""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("book_versions", sa.Column("cover_resource_id", sa.String(128), nullable=True))


def downgrade() -> None:
    op.drop_column("book_versions", "cover_resource_id")
