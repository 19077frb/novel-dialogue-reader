"""模型配置的 API schema（T06）。

响应中**没有**任何存放密钥的字段：只有 `has_key`、`credential_mode` 与可选警告。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from .common import ApiModel
from .enums import CredentialMode


class ModelProfileCreate(ApiModel):
    name: str = Field(min_length=1, max_length=128)
    protocol: str = "chat-completions-compatible"
    base_url: str = Field(description="API 根路径，例如 https://api.example.com/v1")
    model: str = Field(min_length=1, max_length=256)
    params: dict[str, Any] = Field(default_factory=dict, description="生成参数（不能含密钥字段）")
    credential_mode: CredentialMode = CredentialMode.SESSION
    api_key: str | None = Field(default=None, description="只在请求里出现，绝不回传")


class ModelProfilePatch(ApiModel):
    name: str | None = None
    protocol: str | None = None
    base_url: str | None = None
    model: str | None = None
    params: dict[str, Any] | None = None
    credential_mode: CredentialMode | None = None
    api_key: str | None = Field(default=None, description="提供则视为替换密钥")
    remove_api_key: bool = Field(default=False, description="清除该配置的密钥")
    expected_version: int | None = Field(default=None, ge=1)


class ModelProfileOut(ApiModel):
    id: str
    name: str
    protocol: str
    base_url: str
    model: str
    params: dict[str, Any] = Field(default_factory=dict)
    credential_mode: CredentialMode
    has_key: bool
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
    credential_warning: str | None = Field(
        default=None, description="例如系统凭据库不可用、已降级为会话密钥"
    )


class ProtocolCapabilitiesOut(ApiModel):
    protocol: str
    supports_json_schema: bool
    supports_json_object: bool
    supports_temperature: bool
    supports_max_tokens: bool
    requires_api_key: bool
    notes: str = ""
