"""健康检查路由。

`GET /api/health` 报告进程、数据库与版本状态，且不调用模型
（DEVELOPMENT.md 5.2）。数据库在 T01 引入迁移后从 NOT_INITIALIZED 变为 READY。
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Request

from .. import API_VERSION, __version__

router = APIRouter(tags=["health"])

DatabaseState = Literal["READY", "NOT_INITIALIZED", "ERROR"]


def _database_status() -> dict[str, Any]:
    """T00 尚无数据库层，如实报告未初始化，不伪造 READY。"""

    return {
        "state": "NOT_INITIALIZED",
        "detail": "数据库与迁移在 T01 建立；当前仅返回进程与版本状态。",
    }


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    started_at: datetime = request.app.state.started_at
    now = datetime.now(tz=UTC)
    return {
        "status": "ok",
        "app": "novel-dialogue-reader",
        "version": __version__,
        "api_version": API_VERSION,
        "environment": request.app.state.settings.environment,
        "started_at": started_at.isoformat(),
        "uptime_seconds": round(time.monotonic() - request.app.state.started_monotonic, 3),
        "server_time": now.isoformat(),
        "database": _database_status(),
    }
