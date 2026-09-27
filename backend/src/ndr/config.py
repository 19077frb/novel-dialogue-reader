"""应用配置。

数据目录不能通过当前 shell 工作目录猜测：默认值由本文件的绝对路径推导，
也可用 ``NDR_DATA_DIR`` 显式覆盖（见 DEVELOPMENT.md 2.2）。
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

    @field_validator("data_dir")
    @classmethod
    def _resolve_data_dir(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @property
    def database_path(self) -> Path:
        return self.data_dir / "ndr.sqlite3"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
