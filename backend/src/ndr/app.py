"""FastAPI 应用工厂。"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from . import __version__
from .api.errors import install_error_handlers, install_request_id_middleware
from .api.health import router as health_router
from .api.openapi import install_openapi
from .config import Settings, get_settings
from .storage.engine import create_db_engine, create_session_factory
from .storage.migrate import run_migrations


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    engine: Engine = create_db_engine(resolved)
    session_factory: sessionmaker[Session] = create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.started_at = datetime.now(tz=UTC)
        app.state.started_monotonic = time.monotonic()
        resolved.ensure_data_dir()
        if resolved.auto_migrate:
            # 显式 opt-in（NDR_AUTO_MIGRATE=1）才会在启动时迁移；默认由用户/脚本显式执行。
            run_migrations(resolved)
        yield
        engine.dispose()

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
    app.state.engine = engine
    app.state.session_factory = session_factory
    # 默认值：未进入 lifespan（例如直接构造 app）时健康检查仍可用。
    app.state.started_at = datetime.now(tz=UTC)
    app.state.started_monotonic = time.monotonic()

    install_request_id_middleware(app)
    install_error_handlers(app)
    install_openapi(app)

    # 最后添加：CORS 位于最外层，错误响应也带跨域头。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Idempotency-Key"],
        expose_headers=["X-Request-ID"],
    )

    app.include_router(health_router, prefix="/api")
    return app


app = create_app()
