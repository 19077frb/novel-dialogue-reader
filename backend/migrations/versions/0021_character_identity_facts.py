"""Store sourced identity facts without guessing legacy reveal positions."""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "book_characters",
        sa.Column(
            "identity_facts_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("book_characters", "identity_facts_json")
