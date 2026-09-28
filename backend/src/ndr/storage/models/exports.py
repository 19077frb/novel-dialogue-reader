"""导出快照与产物。

- `export_snapshots`：一次**冻结**的投影（标注 + 身份 + 可见性策略 + 样式 + 原文 revision）。
  冻结之后即使有人继续更正，这个快照也不会变化；导出只读快照，不读实时投影。
- `export_artifacts`：由快照生成的文件。相同 fingerprint 可复用，`relative_path` 始终在数据目录内，
  **从不覆盖原书**。
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...domain.enums import ExportFormat
from ..base import Base, IdMixin, TimestampMixin, VersionMixin, enum_type


class ExportSnapshot(IdMixin, TimestampMixin, Base):
    __tablename__ = "export_snapshots"

    book_id: Mapped[str] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    book_version_id: Mapped[str] = mapped_column(
        ForeignKey("book_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    selected_chapter_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    source_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    annotation_projection_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    identity_projection_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    visibility_policy: Mapped[str] = mapped_column(String(32), nullable=False)
    style_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")


class ExportArtifact(IdMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "export_artifacts"

    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("export_snapshots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    format: Mapped[ExportFormat] = mapped_column(
        enum_type(ExportFormat, name="export_format"), nullable=False
    )
    options_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    exporter_version: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="QUEUED")
    relative_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
