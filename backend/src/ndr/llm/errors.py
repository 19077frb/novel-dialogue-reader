"""模型接入层的稳定错误类型。

上游错误必须映射成这些业务错误码；消息经过脱敏，绝不透传密钥或完整上游响应。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from ..domain.enums import ErrorCode


class ProviderErrorKind(StrEnum):
    AUTH = "AUTH"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    MISSING_CREDENTIAL = "MISSING_CREDENTIAL"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"


KIND_TO_CODE: dict[ProviderErrorKind, ErrorCode] = {
    ProviderErrorKind.AUTH: ErrorCode.PROVIDER_AUTH_FAILED,
    ProviderErrorKind.MODEL_NOT_FOUND: ErrorCode.MODEL_NOT_FOUND,
    ProviderErrorKind.RATE_LIMITED: ErrorCode.RATE_LIMITED,
    ProviderErrorKind.TIMEOUT: ErrorCode.PROVIDER_TIMEOUT,
    ProviderErrorKind.UNAVAILABLE: ErrorCode.PROVIDER_UNAVAILABLE,
    ProviderErrorKind.INVALID_OUTPUT: ErrorCode.INVALID_MODEL_OUTPUT,
    ProviderErrorKind.MISSING_CREDENTIAL: ErrorCode.PROVIDER_AUTH_FAILED,
    ProviderErrorKind.UNKNOWN_OUTCOME: ErrorCode.PROVIDER_TIMEOUT,
}

# 哪些错误值得让调用方重试
RETRYABLE_KINDS: frozenset[ProviderErrorKind] = frozenset(
    {ProviderErrorKind.RATE_LIMITED, ProviderErrorKind.TIMEOUT, ProviderErrorKind.UNAVAILABLE}
)


class ProviderError(RuntimeError):
    """上游调用失败（本地判定，不含密钥与完整响应体）。"""

    def __init__(
        self,
        kind: ProviderErrorKind,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.details = details or {}
        self.retryable = RETRYABLE_KINDS.__contains__(kind) if retryable is None else retryable

    @property
    def code(self) -> ErrorCode:
        return KIND_TO_CODE[self.kind]

    @property
    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


class InvalidModelOutput(ProviderError):
    """模型输出无法解析为契约结构（坏 JSON、字段类型错、引用不合法）。"""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ProviderErrorKind.INVALID_OUTPUT, message, details=details, retryable=False
        )
