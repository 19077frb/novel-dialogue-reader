"""FastAPI 应用工厂。"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from . import __version__
from .api.annotations import router as annotations_router
from .api.books import router as books_router
from .api.characters import router as characters_router
from .api.corrections import (
    book_router as corrections_book_router,
)
from .api.corrections import (
    corrections_router,
    gaps_router,
    review_router,
    scenes_router,
)
from .api.corrections import (
    quotes_router as corrections_quotes_router,
)
from .api.errors import ApiError, install_error_handlers, install_request_id_middleware
from .api.estimates import router as estimates_router
from .api.exports import book_router as exports_book_router
from .api.exports import export_router
from .api.health import router as health_router
from .api.jobs import router as jobs_router
from .api.model_profiles import router as model_profiles_router
from .api.openapi import install_openapi
from .api.quotes import quote_router
from .api.quotes import router as quotes_router
from .config import Settings, get_settings
from .llm.credentials import CredentialService, SystemCredentialStore
from .recovery.service import recover_on_startup
from .storage.engine import create_db_engine, create_session_factory
from .storage.migrate import run_migrations

logger = logging.getLogger("ndr.app")


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
        if resolved.recover_on_startup:
            # 进程重启后修复任务可见状态（超租约的尝试 → 未知结果；不留 RUNNING 孤儿）。
            try:
                app.state.recovery = recover_on_startup(
                    session_factory, lease_seconds=resolved.stale_run_lease_seconds
                ).as_dict()
            except Exception as exc:  # noqa: BLE001 - 恢复失败不能阻止服务启动
                app.state.recovery = {"error": type(exc).__name__}
                logger.warning("启动恢复扫描失败：%s", type(exc).__name__)
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
    # 凭据服务：system 用系统凭据库；测试/E2E 用 NDR_CREDENTIAL_BACKEND=session 隔离。
    app.state.credentials = CredentialService(
        system=SystemCredentialStore(
            enabled=resolved.credential_backend.lower() != "session"
        )
    )
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
    app.include_router(books_router, prefix="/api")
    app.include_router(characters_router, prefix="/api")
    app.include_router(jobs_router, prefix="/api")
    app.include_router(quotes_router, prefix="/api")
    app.include_router(quote_router, prefix="/api")
    app.include_router(model_profiles_router, prefix="/api")
    app.include_router(estimates_router, prefix="/api")
    app.include_router(annotations_router, prefix="/api")
    app.include_router(corrections_book_router, prefix="/api")
    app.include_router(review_router, prefix="/api")
    app.include_router(corrections_quotes_router, prefix="/api")
    app.include_router(gaps_router, prefix="/api")
    app.include_router(corrections_router, prefix="/api")
    app.include_router(scenes_router, prefix="/api")
    app.include_router(exports_book_router, prefix="/api")
    app.include_router(export_router, prefix="/api")

    if resolved.static_dir is not None:
        _install_static(app, resolved.static_dir)
    return app


def _install_static(app: FastAPI, static_dir: Path) -> None:
    """生产同源启动：把前端构建产物挂在 `/`，未命中的非 API 路径回退到 index.html。

    - `/api/**` 永远走 JSON 契约：未知路径仍是契约 404，不会被 SPA 回退吞掉；
    - 静态路径解析限制在构建目录内，`..` 之类越界一律回退到 index.html（不读目录外文件）。

    该路由在全部 API 路由之后注册，因此不会遮挡既有端点（含 /docs 与 /openapi.json）。
    """

    root = static_dir.resolve()
    index = root / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise ApiError.not_found("接口不存在", path=f"/{full_path}")
        candidate = (root / full_path).resolve()
        if candidate.is_file() and root in candidate.parents:
            return FileResponse(candidate)
        if index.is_file():
            # 前端路由（/library、/books/:id/read …）交给 SPA 自己解析
            return FileResponse(index)
        raise ApiError.not_found("前端构建产物缺失", static_dir=str(root))


app = create_app()
