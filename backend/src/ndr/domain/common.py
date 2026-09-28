"""公共 API schema：响应包、错误体、分页。

- 普通成功：``{"data": ..., "request_id": "..."}``
- 错误：``{"error": {"code", "message", "details"}, "request_id": "..."}``
- 列表分页：``{"items": [...], "next_cursor": null}``
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ApiModel(BaseModel):
    """API 模型基类：snake_case、禁止未声明字段。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class DataEnvelope(ApiModel, Generic[T]):
    data: T
    request_id: str = Field(description="本次请求的追踪 ID，与响应头 X-Request-ID 一致。")


class ErrorBody(ApiModel):
    code: str = Field(description="稳定业务错误码，见 domain.enums.ErrorCode。")
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(ApiModel):
    error: ErrorBody
    request_id: str


class CursorPage(ApiModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None


class VersionedModel(ApiModel):
    """可变实体的公共版本字段；写请求需携带期望版本。"""

    id: str
    version: int = Field(ge=1)
