"""Backfill chapter processing state once, instead of scanning jobs on every read.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-30
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT book_version_id, range_json FROM jobs "
        "WHERE kind = 'INFERENCE' AND state = 'COMPLETED'"
    ))
    completed: set[tuple[str, str]] = set()
    for version_id, range_json in rows:
        try:
            job_range = json.loads(range_json or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        chapter_id = job_range.get("chapter_id")
        if chapter_id and not job_range.get("selected_window_ids"):
            completed.add((str(version_id), str(chapter_id)))
    for version_id, chapter_id in completed:
        bind.execute(
            sa.text(
                "UPDATE chapters SET dialogue_processed = 1 "
                "WHERE book_version_id = :version_id AND id = :chapter_id"
            ),
            {"version_id": version_id, "chapter_id": chapter_id},
        )


def downgrade() -> None:
    # 无法可靠区分迁移回填与之后正常完成的章节，因此保留状态。
    pass
