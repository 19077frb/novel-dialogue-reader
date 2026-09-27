"""书籍、文档与内容节点 API schema（DEVELOPMENT.md 4.1 / 5.2）。

字段一律 snake_case；时间为 UTC ISO 8601；码点指某个书籍版本的 canonical 全文。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .common import ApiModel
from .enums import (
    BookFormat,
    ContentNodeType,
    ImportStatus,
    JobKind,
    JobPurpose,
    JobState,
    ReadingMode,
)


class BookVersionOut(ApiModel):
    id: str
    encoding: str
    parser_version: str
    normalization_version: str
    canonical_sha256: str
    canonical_length_cp: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime


class BookOut(ApiModel):
    id: str
    title: str
    format: BookFormat
    source_sha256: str
    import_status: ImportStatus
    read_position_cp: int = Field(ge=0)
    reading_mode: ReadingMode = ReadingMode.INITIAL
    version: int = Field(ge=1)
    active_version_id: str | None = None
    active_version: BookVersionOut | None = None
    created_at: datetime
    updated_at: datetime


class ChapterOut(ApiModel):
    id: str
    ordinal: int
    title: str | None = None
    start_cp: int = Field(ge=0)
    end_cp: int = Field(ge=0)
    source_href: str | None = None


class ContentNodeOut(ApiModel):
    node_id: str
    node_type: ContentNodeType
    ordinal: int
    start_cp: int = Field(ge=0)
    end_cp: int = Field(ge=0)
    chapter_id: str | None = None
    chapter_ordinal: int | None = None
    text: str
    # 受限节点附加数据：标题层级、图片 resource_id/media_type、ruby 注音（rt 不进正文）。
    payload: dict[str, object] = Field(default_factory=dict)


class ContentResponse(ApiModel):
    book_id: str
    book_version_id: str
    canonical_length_cp: int
    chapter_id: str | None = None
    start_cp: int = Field(ge=0)
    end_cp: int = Field(ge=0)
    nodes: list[ContentNodeOut]
    next_cursor: str | None = None


class ReadingProgressIn(ApiModel):
    """保存阅读书签：不调用模型，只写位置与模式。"""

    book_version_id: str
    read_position_cp: int = Field(ge=0)
    reading_mode: ReadingMode = ReadingMode.INITIAL
    expected_version: int | None = None


class ReadingProgressOut(ApiModel):
    book_id: str
    book_version_id: str
    read_position_cp: int = Field(ge=0)
    reading_mode: ReadingMode
    version: int = Field(ge=1)


class ImportResult(ApiModel):
    book_id: str
    book_version_id: str
    job_id: str
    format: BookFormat
    import_status: ImportStatus
    # TXT 记录检测/指定的编码；EPUB 的正文编码由各 XHTML 文档的 XML 声明决定，故为 null。
    encoding: str | None = None
    encoding_confidence: str | None = None
    chapter_count: int
    node_count: int = 0
    resource_count: int = 0
    canonical_length_cp: int
    reused_book: bool
    reused_version: bool
    warnings: list[str] = Field(default_factory=list)


class JobOut(ApiModel):
    id: str
    kind: JobKind
    purpose: JobPurpose | None = None
    state: JobState
    book_id: str | None = None
    book_version_id: str | None = None
    progress: dict[str, object] | None = None
    checkpoint: dict[str, object] | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime
