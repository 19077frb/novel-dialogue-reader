"""Record human and automatic character confirmation separately."""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("book_characters", sa.Column(
        "confirmation_source", sa.String(16), nullable=False, server_default="legacy",
    ))
    op.execute("UPDATE book_characters SET confirmation_source = 'model' "
               "WHERE user_confirmed = 0")
    op.execute("UPDATE book_characters SET confirmation_source = 'manual' "
               "WHERE user_confirmed = 1 AND name_locked = 1")
    op.execute("UPDATE book_characters SET confirmation_source = 'imported' "
               "WHERE temp_key LIKE 'imported-annotation:%' AND name_locked = 0")


def downgrade() -> None:
    op.drop_column("book_characters", "confirmation_source")
