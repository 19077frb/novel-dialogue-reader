"""Keep scene/FK lookup coverage without the removed dependency-hash query."""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_annotations_scene_id", "annotations", ["scene_id"])
    op.drop_index("ix_annotations_scene_dependency", table_name="annotations")


def downgrade() -> None:
    op.create_index(
        "ix_annotations_scene_dependency", "annotations", ["scene_id", "dependency_hash"],
    )
    op.drop_index("ix_annotations_scene_id", table_name="annotations")
