"""Share immutable presentation baselines and bind proven cache ownership."""

import json

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "presentation_history_baselines",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("book_version_id", sa.String(36),
                  sa.ForeignKey("book_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("history_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("book_version_id", "digest", name="uq_presentation_baseline_content"),
    )
    # Inline references avoid rebuilding heavily referenced SQLite tables.
    op.execute("ALTER TABLE speaker_groups ADD COLUMN presentation_baseline_id VARCHAR(36) "
               "REFERENCES presentation_history_baselines(id) ON DELETE SET NULL")
    op.create_index("ix_speaker_groups_presentation_baseline_id", "speaker_groups",
                    ["presentation_baseline_id"])
    op.execute("ALTER TABLE result_cache ADD COLUMN book_version_id VARCHAR(36) "
               "REFERENCES book_versions(id) ON DELETE CASCADE")
    op.create_index("ix_result_cache_book_version_id", "result_cache", ["book_version_id"])
    # Unknown origin stays NULL. A cache key cannot prove which version owned it.
    op.execute("""
        UPDATE result_cache SET book_version_id = (
            SELECT jobs.book_version_id FROM inference_runs
            JOIN jobs ON jobs.id = inference_runs.job_id
            JOIN book_versions ON book_versions.id = jobs.book_version_id
            WHERE inference_runs.id = result_cache.created_run_id
        )
    """)


def downgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text("""
        SELECT speaker_groups.id, speaker_groups.presentation_history_json,
               presentation_history_baselines.history_json
        FROM speaker_groups JOIN presentation_history_baselines
          ON presentation_history_baselines.id = speaker_groups.presentation_baseline_id
    """)).all()
    for group_id, local, baseline in rows:
        history = json.loads(baseline) + json.loads(local or "[]")
        connection.execute(sa.text(
            "UPDATE speaker_groups SET presentation_history_json=:history WHERE id=:id"
        ), {"id": group_id, "history": json.dumps(history, ensure_ascii=False)})
    op.drop_index("ix_speaker_groups_presentation_baseline_id", table_name="speaker_groups")
    op.drop_column("speaker_groups", "presentation_baseline_id")
    op.drop_table("presentation_history_baselines")
    op.drop_index("ix_result_cache_book_version_id", table_name="result_cache")
    op.drop_column("result_cache", "book_version_id")
