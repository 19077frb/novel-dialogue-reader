from datetime import datetime

from pydantic import Field

from .common import ApiModel


class BookmarkCreate(ApiModel):
    book_version_id: str
    chapter_id: str
    position_cp: int = Field(ge=0)
    note: str = Field(default="", max_length=512)


class BookmarkPatch(ApiModel):
    note: str = Field(max_length=512)
    expected_version: int = Field(ge=1)


class BookmarkOut(ApiModel):
    id: str
    book_id: str
    book_version_id: str
    chapter_id: str
    chapter_title: str
    position_cp: int
    excerpt: str
    note: str
    version: int
    created_at: datetime
    updated_at: datetime
