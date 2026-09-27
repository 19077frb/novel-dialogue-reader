"""候选引语、Gap 与原文定位的 API schema（T05）。"""

from __future__ import annotations

from pydantic import Field

from .common import ApiModel
from .corrections import AnnotationStateOut, ReviewItemOut, SceneGroupRefOut, SceneRefOut
from .enums import ContentNodeType, GapDecision, QuoteKind


class ScanWarningOut(ApiModel):
    code: str
    position_cp: int = Field(ge=0)
    delimiter: str
    detail: str


class QuoteOut(ApiModel):
    quote_id: str
    book_version_id: str
    chapter_id: str | None = None
    chapter_ordinal: int | None = None
    start_cp: int = Field(ge=0)
    end_cp: int = Field(ge=0)
    text: str = Field(description="引号内的文字（不含引号本身）。")
    delimited_text: str = Field(description="包含引号的原文片段。")
    delimiter: str
    opening: str
    closing: str
    nesting_depth: int = Field(ge=0)
    parent_quote_id: str | None = None
    kind_hint: QuoteKind | None = None
    scanner_version: str


class GapOut(ApiModel):
    gap_id: str
    book_version_id: str
    left_quote_id: str | None = None
    right_quote_id: str | None = None
    start_cp: int = Field(ge=0)
    end_cp: int = Field(ge=0)
    narration: str
    paragraph_count: int = Field(ge=0)
    decision: GapDecision


class QuoteDetailOut(ApiModel):
    """普通对白详情；**不要求**该对白已经在待确认队列里（T12）。"""

    quote: QuoteOut
    previous_quote_id: str | None = None
    next_quote_id: str | None = None
    gap_before: GapOut | None = None
    context_before: str = ""
    context_after: str = ""
    annotation: AnnotationStateOut | None = Field(
        default=None, description="当前有效标注；未处理过则为 null"
    )
    scene: SceneRefOut | None = None
    review_items: list[ReviewItemOut] = Field(default_factory=list)
    scene_groups: list[SceneGroupRefOut] = Field(
        default_factory=list, description="同场景内可指定的已有分组（编号 + group_id）"
    )
    can_correct: bool = Field(default=False, description="是否可通过更正接口人工修改")


class LocateSpanOut(ApiModel):
    mapping_ordinal: int
    chapter_id: str | None = None
    chapter_ordinal: int | None = None
    node_id: str | None = None
    node_type: ContentNodeType | None = None
    canonical_start_cp: int = Field(ge=0)
    canonical_end_cp: int = Field(ge=0)
    source_href: str | None = None
    source_text_start_cp: int = Field(ge=0)
    source_text_end_cp: int = Field(ge=0)
    synthetic: bool
    text: str


class LocateOut(ApiModel):
    book_id: str
    book_version_id: str
    start_cp: int = Field(ge=0)
    end_cp: int = Field(ge=0)
    text: str
    spans: list[LocateSpanOut]


class ScanResultOut(ApiModel):
    book_id: str
    book_version_id: str
    job_id: str
    scanner_version: str
    quote_count: int
    top_level_quote_count: int
    gap_count: int
    warnings: list[ScanWarningOut] = Field(default_factory=list)
    stats: dict[str, int] = Field(default_factory=dict)
