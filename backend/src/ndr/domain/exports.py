"""导出相关的 API schema。"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .common import ApiModel
from .enums import ExportFormat, ExportStylePreset, VisibilityPolicy


class ExportStyleIn(ApiModel):
    preset: ExportStylePreset = Field(
        default=ExportStylePreset.COLOR_AND_LABEL,
        description="color_and_label / color_only / label_only",
    )
    palette_id: str = Field(default="reader-default", max_length=64)


class ExportPreviewIn(ApiModel):
    """冻结快照 + 样张；**不调用模型**。"""

    book_version_id: str | None = None
    chapter_ids: list[str] | None = Field(
        default=None, description="留空表示整本；否则只导出选中的章节"
    )
    visibility_policy: VisibilityPolicy = VisibilityPolicy.POSITION_SAFE
    style: ExportStyleIn = Field(default_factory=ExportStyleIn)


class ExportPreviewOut(ApiModel):
    snapshot_id: str
    book_id: str
    book_version_id: str
    snapshot_hash: str
    source_revision: str
    visibility_policy: VisibilityPolicy
    selected_chapter_ids: list[str] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    coverage: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    sample_html: str = Field(description="后端导出渲染器产出的样张（隔离容器里展示）")
    sample_fragments: list[str] = Field(default_factory=list)
    created_at: str


class ExportCreateIn(ApiModel):
    snapshot_id: str
    format: ExportFormat
    style: ExportStyleIn = Field(default_factory=ExportStyleIn)
    idempotency_key: str = Field(min_length=1, max_length=128)


class ExportArtifactOut(ApiModel):
    id: str
    snapshot_id: str
    format: ExportFormat
    state: str
    filename: str | None = None
    relative_path: str | None = None
    byte_size: int | None = None
    file_sha256: str | None = None
    exporter_version: str
    validation: dict[str, Any] = Field(default_factory=dict)
    download_available: bool = False
    created_at: str
    updated_at: str
