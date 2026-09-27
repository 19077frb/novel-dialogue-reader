"""FastAPI 应用工厂。"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api.health import router as health_router
from .config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.started_at = datetime.now(tz=UTC)
        app.state.started_monotonic = time.monotonic()
        resolved.data_dir.mkdir(parents=True, exist_ok=True)
        yield

    app = FastAPI(
        title="Novel Dialogue Reader API",
        version=__version__,
        description=(
            "轻小说对话辅助阅读器本地后端。契约摘要见 docs/CONTRACTS.md，"
            "完整规格见 DEVELOPMENT.md。"
        ),
        lifespan=lifespan,
    )
    app.state.settings = resolved

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Idempotency-Key"],
    )

    app.include_router(health_router, prefix="/api")
    return app


app = create_app()
