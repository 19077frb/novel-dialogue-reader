"""Index referencing columns to avoid quadratic whole-book deletion scans."""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

REFERENCES = (
    ("chapter_character_rosters", "pov_character_id"),
    ("chapter_character_rosters", "analysis_job_id"),
    ("quotes", "parent_quote_id"),
    ("gaps", "left_quote_id"),
    ("gaps", "right_quote_id"),
    ("speaker_groups", "first_quote_id"),
    # 0007 新增了字段，但旧迁移未创建模型中声明的索引。
    ("speaker_groups", "character_id"),
    ("export_artifacts", "job_id"),
    ("corrections", "undone_by"),
)


def upgrade() -> None:
    for table, column in REFERENCES:
        op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table, column in reversed(REFERENCES):
        op.drop_index(f"ix_{table}_{column}", table_name=table)
