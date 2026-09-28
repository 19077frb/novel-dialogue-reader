"""应用配置。

数据目录不能通过当前 shell 工作目录猜测：默认值由本文件的绝对路径推导，
也可用 ``NDR_DATA_DIR`` 显式覆盖。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


def default_data_dir() -> Path:
    """仓库内 ``data/``，解析为绝对路径。"""

    return (REPO_ROOT / "data").resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NDR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "local"

    # 本地服务默认只监听回环地址。
    host: str = "127.0.0.1"
    port: int = 8765

    # 开发前端默认来源；仅这些来源允许跨域访问 API。
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ]
    )

    data_dir: Path = Field(default_factory=default_data_dir)

    # 生产同源启动：把前端构建产物（frontend/dist）挂在同一个端口上。
    # 默认 None = 不在后端提供静态页面（开发时用 Vite 的 5173）；scripts/serve.ps1 会显式设置。
    static_dir: Path | None = None

    # 默认不自动迁移：显式执行 alembic 或设置 NDR_AUTO_MIGRATE=1。
    auto_migrate: bool = False

    # 单次导入的文件大小上限（字节）；超出返回 413。
    max_import_bytes: int = 50 * 1024 * 1024

    # 模型调用：超时与 FakeProvider 开关。FakeProvider 默认关闭，只在测试/演示时显式启用。
    llm_timeout_seconds: float = 30.0
    allow_fake_provider: bool = False

    # 仅测试：FakeProvider 的标注脚本模式。unknown=全部标为 UNKNOWN（默认）；
    # deterministic=确定性地建一个分组并返回 DIRECT 归属，用于离线验证着色/编号链路。
    # 只有在 NDR_ALLOW_FAKE_PROVIDER=1 且协议为 fake-provider 时才可能生效，绝不影响真实提供方。
    fake_provider_labels: str = "unknown"

    # 仅测试：FakeProvider 的失败脚本。
    # 取值：""/"rate_limited_once"/"unavailable_once"/"timeout_once"/"auth_failed_once"。
    fake_provider_script: str = ""

    # 限流/暂时不可用的**有上限**自动重试。超过上限就停下，等用户显式继续。
    rate_limit_max_retries: int = 2
    rate_limit_backoff_base_seconds: int = 1
    rate_limit_backoff_max_seconds: int = 30

    # 进程重启时把超过该租约仍未落库的尝试视为「结果未知」。
    stale_run_lease_seconds: int = 900

    # 启动时自动扫描孤儿任务（恢复可见状态）；测试可关闭。
    recover_on_startup: bool = True

    # 凭据后端："system" 用系统凭据库（keyring），"session" 只用进程内会话密钥。
    # 测试/E2E 显式设为 session，避免触碰真实的系统凭据库。
    credential_backend: str = "system"

    # EPUB 解压与结构限制。
    max_epub_entries: int = 2000
    max_epub_total_uncompressed_bytes: int = 200 * 1024 * 1024
    max_epub_entry_bytes: int = 32 * 1024 * 1024
    max_epub_spine_items: int = 500

    @field_validator("data_dir")
    @classmethod
    def _resolve_data_dir(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("static_dir")
    @classmethod
    def _resolve_static_dir(cls, value: Path | None) -> Path | None:
        return None if value is None else value.expanduser().resolve()

    @property
    def database_path(self) -> Path:
        return self.data_dir / "ndr.sqlite3"

    @property
    def database_url(self) -> str:
        """SQLAlchemy URL；SQLite 用绝对路径，绝不依赖进程 cwd。"""

        return f"sqlite+pysqlite:///{self.database_path.as_posix()}"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def alembic_ini_path(self) -> Path:
        return REPO_ROOT / "backend" / "alembic.ini"

    @property
    def migrations_dir(self) -> Path:
        return REPO_ROOT / "backend" / "migrations"

    def ensure_data_dir(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
