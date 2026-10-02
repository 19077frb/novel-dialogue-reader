"""统一错误契约与请求 ID。

错误体固定为::

    {"error": {"code": "...", "message": "...", "details": {}}, "request_id": "..."}

上游错误必须映射到稳定业务错误码，且不透传密钥或完整上游响应。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..domain.common import ErrorBody, ErrorEnvelope
from ..domain.enums import ErrorCode
from ..storage.transactions import VersionConflict

logger = logging.getLogger("ndr.api")

STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.VERSION_CONFLICT: 409,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.RESOURCE_CONFLICT: 409,
    ErrorCode.PAYLOAD_TOO_LARGE: 413,
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: 415,
    ErrorCode.BUDGET_EXHAUSTED: 409,
    ErrorCode.PROVIDER_AUTH_FAILED: 502,
    ErrorCode.MODEL_NOT_FOUND: 502,
    ErrorCode.PROVIDER_UNAVAILABLE: 502,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.PROVIDER_TIMEOUT: 504,
    ErrorCode.INVALID_MODEL_OUTPUT: 502,
    ErrorCode.INTERNAL_ERROR: 500,
}

CODE_BY_STATUS: dict[int, ErrorCode] = {
    400: ErrorCode.VALIDATION_ERROR,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.VALIDATION_ERROR,
    409: ErrorCode.VERSION_CONFLICT,
    413: ErrorCode.PAYLOAD_TOO_LARGE,
    415: ErrorCode.UNSUPPORTED_MEDIA_TYPE,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.RATE_LIMITED,
    500: ErrorCode.INTERNAL_ERROR,
    502: ErrorCode.INVALID_MODEL_OUTPUT,
    504: ErrorCode.PROVIDER_TIMEOUT,
}


class ApiError(Exception):
    """业务错误；由异常处理器转成契约中的错误体。"""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.status_code = status_code or STATUS_BY_CODE.get(code, 400)

    @classmethod
    def not_found(cls, message: str, **details: Any) -> ApiError:
        return cls(ErrorCode.NOT_FOUND, message, details=details, status_code=404)

    @classmethod
    def validation(cls, message: str, **details: Any) -> ApiError:
        return cls(ErrorCode.VALIDATION_ERROR, message, details=details, status_code=422)


def current_request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "-"))


def error_payload(
    code: ErrorCode, message: str, *, details: dict[str, Any] | None = None, request_id: str
) -> dict[str, Any]:
    envelope = ErrorEnvelope(
        error=ErrorBody(code=code.value, message=message, details=details or {}),
        request_id=request_id,
    )
    return envelope.model_dump(mode="json")


def install_request_id_middleware(app: FastAPI) -> None:
    """每个请求分配追踪 ID，并在响应头回显。"""

    @app.middleware("http")
    async def _attach_request_id(request: Request, call_next):  # noqa: ANN001, ANN202
        request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        writing = request.method in {"POST", "PUT", "PATCH", "DELETE"}
        if writing and getattr(request.app.state, "restart_pending", False):
            return JSONResponse(
                status_code=409,
                content=error_payload(
                    ErrorCode.RESOURCE_CONFLICT,
                    "服务正在重启，请等待重新启动后再操作。",
                    request_id=request_id,
                ),
                headers={"X-Request-ID": request_id},
            )
        if writing:
            request.app.state.active_writes = getattr(request.app.state, "active_writes", 0) + 1
        try:
            response = await call_next(request)
        finally:
            if writing:
                request.app.state.active_writes -= 1
        response.headers["X-Request-ID"] = request_id
        return response


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        rid = current_request_id(request)
        if exc.status_code >= 500:
            logger.warning("API 错误 %s (request_id=%s): %s", exc.code, rid, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.code, exc.message, details=exc.details, request_id=rid),
        )

    @app.exception_handler(VersionConflict)
    async def _handle_version_conflict(request: Request, exc: VersionConflict) -> JSONResponse:
        rid = current_request_id(request)
        return JSONResponse(
            status_code=409,
            content=error_payload(
                ErrorCode.VERSION_CONFLICT, str(exc), details=exc.details, request_id=rid
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        rid = current_request_id(request)
        errors = [
            {
                "loc": [str(part) for part in error.get("loc", ())],
                "type": str(error.get("type", "")),
                "message": str(error.get("msg", "")),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_payload(
                ErrorCode.VALIDATION_ERROR,
                "请求参数不合法",
                details={"errors": errors},
                request_id=rid,
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        rid = current_request_id(request)
        code = CODE_BY_STATUS.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        message = exc.detail if isinstance(exc.detail, str) else "请求失败"
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(code, message, request_id=rid),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        rid = current_request_id(request)
        # 只记录类型与请求 ID，避免把小说正文或密钥写进日志。
        logger.exception("未处理异常 %s (request_id=%s)", type(exc).__name__, rid)
        return JSONResponse(
            status_code=500,
            content=error_payload(ErrorCode.INTERNAL_ERROR, "服务器内部错误", request_id=rid),
        )
