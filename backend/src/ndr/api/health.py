"""健康检查路由。

`GET /api/health` 报告进程、数据库与版本状态，且不调用模型。
数据库状态来自真实迁移状态：未迁移 → NOT_INITIALIZED，版本落后 → OUTDATED，
迁移到 head → READY，读取失败 → ERROR 且整体状态变为 degraded。
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request

from .. import API_VERSION, __version__
from ..domain.enums import DatabaseState
from ..storage.engine import migration_status

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    summary="进程、数据库与版本状态（不调用模型）",
    description="独立响应体；不返回任意磁盘路径，也不触发任何模型调用。",
)
def health(request: Request) -> dict[str, Any]:
    app = request.app
    database = migration_status(app.state.engine)
    return {
        "status": "degraded" if database.state is DatabaseState.ERROR else "ok",
        "app": "novel-dialogue-reader",
        "version": __version__,
        "api_version": API_VERSION,
        "environment": app.state.settings.environment,
        "started_at": app.state.started_at.isoformat(),
        "uptime_seconds": round(time.monotonic() - app.state.started_monotonic, 3),
        "server_time": datetime.now(tz=UTC).isoformat(),
        "database": database.as_dict(),
    }
