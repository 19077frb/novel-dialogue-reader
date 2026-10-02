"""Remove redundant read indexes; retain unique constraints and FK prefix coverage."""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

# Historical definitions must remain independent of future ORM changes.
INDEXES = (
    ("text_mappings", "ix_text_mappings_book_version_id", ["book_version_id"]),
    ("text_mappings", "ix_text_mappings_version_source",
     ["book_version_id", "source_text_start_cp"]),
    ("content_nodes", "ix_content_nodes_chapter_id", ["chapter_id"]),
    ("quotes", "ix_quotes_book_version_id", ["book_version_id"]),
    ("quotes", "ix_quotes_chapter_id", ["chapter_id"]),
    ("quotes", "ix_quotes_version_start", ["book_version_id", "start_cp"]),
    ("gaps", "ix_gaps_book_version_id", ["book_version_id"]),
    ("book_versions", "ix_book_versions_book_id", ["book_id"]),
    ("book_characters", "ix_book_characters_book_version_id", ["book_version_id"]),
    ("chapters", "ix_chapters_book_version_id", ["book_version_id"]),
    ("jobs", "ix_jobs_book_version_id", ["book_version_id"]),
    ("jobs", "ix_jobs_book_id", ["book_id"]),
    ("quote_normalizations", "ix_quote_normalizations_book_version_id", ["book_version_id"]),
    ("resources", "ix_resources_book_version_id", ["book_version_id"]),
    ("scenes", "ix_scenes_book_version_id", ["book_version_id"]),
    ("chapter_character_rosters", "ix_chapter_character_rosters_chapter_id", ["chapter_id"]),
    ("job_windows", "ix_job_windows_job_id", ["job_id"]),
    ("speaker_groups", "ix_speaker_groups_scene_id", ["scene_id"]),
    ("annotations", "ix_annotations_quote_id", ["quote_id"]),
    ("annotations", "ix_annotations_scene_id", ["scene_id"]),
    ("review_items", "ix_review_items_quote_id", ["quote_id"]),
    ("review_items", "ix_review_items_gap_id", ["gap_id"]),
    ("annotation_history", "ix_annotation_history_annotation_id", ["annotation_id"]),
    ("books", "ix_books_active_version_id", ["active_version_id"]),
    ("book_versions", "ix_book_versions_canonical_sha256", ["canonical_sha256"]),
    ("inference_runs", "ix_inference_runs_request_fingerprint", ["request_fingerprint"]),
    ("quotes", "ix_quotes_utterance_id", ["utterance_id"]),
    ("export_snapshots", "ix_export_snapshots_snapshot_hash", ["snapshot_hash"]),
    ("corrections", "ix_corrections_target_type", ["target_type"]),
)


def upgrade() -> None:
    for table, name, _ in INDEXES:
        op.drop_index(name, table_name=table)


def downgrade() -> None:
    for table, name, columns in reversed(INDEXES):
        op.create_index(name, table, columns)
