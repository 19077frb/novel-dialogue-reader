"""cursor/limit 分页。

列表统一返回 ``{"items": [...], "next_cursor": ...}``；cursor 是后端生成的不透明字符串，
客户端不得解析或构造。
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from ..domain.common import CursorPage
from ..domain.enums import ErrorCode
from .errors import ApiError

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def parse_limit(
    limit: int | None, *, default: int = DEFAULT_LIMIT, maximum: int = MAX_LIMIT
) -> int:
    if limit is None:
        return default
    if limit < 1:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "limit 必须大于 0", details={"limit": limit})
    if limit > maximum:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            f"limit 不能超过 {maximum}",
            details={"limit": limit, "max_limit": maximum},
        )
    return limit


def encode_cursor(parts: Sequence[Any]) -> str:
    raw = json.dumps(list(parts), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> list[Any]:
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        value = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "cursor 不是合法游标") from exc
    if not isinstance(value, list):
        raise ApiError(ErrorCode.VALIDATION_ERROR, "cursor 不是合法游标")
    return value


def chronological_cursor(session: Session, model: Any, cursor: str) -> Any:
    """Seek by the exact (created_at, id) order; accept legacy ID-only cursors."""
    parts = decode_cursor(cursor)
    if len(parts) == 1:
        row_id = str(parts[0])
        created = session.scalar(select(model.created_at).where(model.id == row_id))
        if created is None:
            raise ApiError.validation("游标对应记录不存在，请重新读取列表")
    elif len(parts) == 2:
        try:
            created = datetime.fromisoformat(str(parts[0]))
        except ValueError as exc:
            raise ApiError.validation("cursor 时间不合法") from exc
        if created.tzinfo is None:
            raise ApiError.validation("cursor 时间必须包含时区")
        row_id = str(parts[1])
    else:
        raise ApiError.validation("cursor 内容不合法")
    return tuple_(model.created_at, model.id) > (created, row_id)


def build_page(items: list[Any], *, next_cursor: str | None = None) -> CursorPage[Any]:
    return CursorPage(items=items, next_cursor=next_cursor)
