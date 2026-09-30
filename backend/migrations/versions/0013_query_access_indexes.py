"""Composite indexes for chapter reads, chronological queues and active-job guards."""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

INDEXES = (
    ("books", "ix_books_created_at_id", ["created_at", "id"]),
    (
        "content_nodes",
        "ix_content_nodes_chapter_start_ordinal",
        ["chapter_id", "start_cp", "ordinal"],
    ),
    ("quotes", "ix_quotes_version_start", ["book_version_id", "start_cp"]),
    ("quotes", "ix_quotes_chapter_start", ["chapter_id", "start_cp"]),
    ("gaps", "ix_gaps_version_start", ["book_version_id", "start_cp"]),
    ("scenes", "ix_scenes_version_start", ["book_version_id", "start_cp"]),
    ("jobs", "ix_jobs_book_created_id", ["book_id", "created_at", "id"]),
    ("jobs", "ix_jobs_version_state", ["book_version_id", "state"]),
    ("jobs", "ix_jobs_digest_state_created", ["request_digest", "state", "created_at"]),
    ("review_items", "ix_review_items_created_id", ["created_at", "id"]),
    ("annotations", "ix_annotations_scene_dependency", ["scene_id", "dependency_hash"]),
)


def upgrade() -> None:
    for table, name, columns in INDEXES:
        op.create_index(name, table, columns)


def downgrade() -> None:
    for table, name, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
