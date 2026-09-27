"""候选引语、Gap 与原文定位的 API schema（T05）。"""

from __future__ import annotations

from pydantic import Field

from .common import ApiModel
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
    quote: QuoteOut
    previous_quote_id: str | None = None
    next_quote_id: str | None = None
    gap_before: GapOut | None = None
    context_before: str = ""
    context_after: str = ""


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
