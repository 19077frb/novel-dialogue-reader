"""Preserve chapter-safe names, descriptions and identities."""

import json

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("book_characters", "speaker_groups"):
        op.add_column(table, sa.Column("presentation_history_json", sa.Text(),
                                      nullable=False, server_default="[]"))
    connection = op.get_bind()
    connection.execute(sa.text(
        "UPDATE identity_revisions SET visible_from_cp = ("
        "SELECT v.canonical_length_cp FROM scenes s JOIN book_versions v "
        "ON v.id = s.book_version_id WHERE s.id = identity_revisions.scene_id) "
        "WHERE visible_from_cp IS NULL"
    ))
    # Old merges destroyed the pre-reveal metadata. Do not fabricate a safe history.
    for table, join, identity in (
        ("book_characters", "JOIN book_versions v ON v.id = r.book_version_id",
         "'character:' || r.id"),
        ("speaker_groups", "JOIN scenes s ON s.id = r.scene_id "
         "JOIN book_versions v ON v.id = s.book_version_id",
         "CASE WHEN r.character_id IS NULL THEN 'group:' || r.id "
         "ELSE 'character:' || r.character_id END"),
    ):
        rows = connection.execute(sa.text(
            f"SELECT r.id, r.canonical_name, r.description, v.canonical_length_cp, "
            f"{identity} AS identity FROM {table} r {join}"
        )).mappings().all()
        for row in rows:
            history = [{"cp": row["canonical_length_cp"], "identity": row["identity"],
                        "name": row["canonical_name"] or "",
                        "description": row["description"] or ""}]
            connection.execute(sa.text(
                f"UPDATE {table} SET presentation_history_json = :history WHERE id = :id"
            ), {"id": row["id"], "history": json.dumps(history, ensure_ascii=False)})


def downgrade() -> None:
    for table in ("speaker_groups", "book_characters"):
        op.drop_column(table, "presentation_history_json")
