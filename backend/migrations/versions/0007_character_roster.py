"""Book-level characters and per-chapter confirmed rosters.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import ndr.storage.types  # noqa: F401 - UtcDateTime columns need module visibility

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamp_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", ndr.storage.types.UtcDateTime(), nullable=False),
        sa.Column("updated_at", ndr.storage.types.UtcDateTime(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "book_characters",
        sa.Column("book_version_id", sa.String(length=36), nullable=False),
        sa.Column("temp_key", sa.String(length=160), nullable=True),
        sa.Column("canonical_name", sa.String(length=128), nullable=True),
        sa.Column("aliases_json", sa.Text(), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column(
            "source",
            sa.Enum("MODEL", "USER", name="character_source", native_enum=False),
            nullable=False,
        ),
        sa.Column("user_confirmed", sa.Boolean(), nullable=False),
        sa.Column("first_seen_cp", sa.Integer(), nullable=True),
        *_timestamp_columns(),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["book_version_id"],
            ["book_versions.id"],
            name=op.f("fk_book_characters_book_version_id_book_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_book_characters")),
        sa.UniqueConstraint(
            "book_version_id",
            "temp_key",
            name=op.f("uq_book_characters_book_version_id_temp_key"),
        ),
    )
    with op.batch_alter_table("book_characters", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_book_characters_book_version_id"), ["book_version_id"], unique=False
        )

    op.create_table(
        "chapter_character_rosters",
        sa.Column("chapter_id", sa.String(length=36), nullable=False),
        sa.Column("book_version_id", sa.String(length=36), nullable=False),
        sa.Column(
            "status",
            sa.Enum("DRAFT", "CONFIRMED", name="character_roster_status", native_enum=False),
            nullable=False,
        ),
        sa.Column("candidates_json", sa.Text(), nullable=False),
        sa.Column("confirmed_character_ids_json", sa.Text(), nullable=False),
        sa.Column("pov_character_id", sa.String(length=36), nullable=True),
        sa.Column("analysis_job_id", sa.String(length=36), nullable=True),
        *_timestamp_columns(),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["book_version_id"],
            ["book_versions.id"],
            name=op.f("fk_chapter_character_rosters_book_version_id_book_versions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["chapter_id"],
            ["chapters.id"],
            name=op.f("fk_chapter_character_rosters_chapter_id_chapters"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pov_character_id"],
            ["book_characters.id"],
            name=op.f("fk_chapter_character_rosters_pov_character_id_book_characters"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_job_id"],
            ["jobs.id"],
            name=op.f("fk_chapter_character_rosters_analysis_job_id_jobs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chapter_character_rosters")),
        sa.UniqueConstraint(
            "chapter_id", name=op.f("uq_chapter_character_rosters_chapter_id")
        ),
    )
    with op.batch_alter_table("chapter_character_rosters", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_chapter_character_rosters_book_version_id"),
            ["book_version_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_chapter_character_rosters_chapter_id"), ["chapter_id"], unique=False
        )

    with op.batch_alter_table("speaker_groups", schema=None) as batch_op:
        batch_op.add_column(sa.Column("character_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_speaker_groups_character_id_book_characters"),
            "book_characters",
            ["character_id"],
            ["id"],
            ondelete="SET NULL",
        )

    old_job_kind = sa.Enum(
        "IMPORT", "INFERENCE", "RECHECK", "RECOMPUTE", "EXPORT",
        name="job_kind", native_enum=False,
    )
    new_job_kind = sa.Enum(
        "IMPORT", "INFERENCE", "CHARACTER_ROSTER", "RECHECK", "RECOMPUTE", "EXPORT",
        name="job_kind", native_enum=False,
    )
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.alter_column(
            "kind", existing_type=old_job_kind, type_=new_job_kind, existing_nullable=False
        )


def downgrade() -> None:
    new_job_kind = sa.Enum(
        "IMPORT", "INFERENCE", "CHARACTER_ROSTER", "RECHECK", "RECOMPUTE", "EXPORT",
        name="job_kind", native_enum=False,
    )
    old_job_kind = sa.Enum(
        "IMPORT", "INFERENCE", "RECHECK", "RECOMPUTE", "EXPORT",
        name="job_kind", native_enum=False,
    )
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.alter_column(
            "kind", existing_type=new_job_kind, type_=old_job_kind, existing_nullable=False
        )

    with op.batch_alter_table("speaker_groups", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_speaker_groups_character_id_book_characters"), type_="foreignkey"
        )
        batch_op.drop_column("character_id")

    op.drop_table("chapter_character_rosters")
    op.drop_table("book_characters")
