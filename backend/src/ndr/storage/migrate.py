"""以编程方式执行数据库迁移。

用途：``scripts/dev.ps1`` 的显式迁移、E2E/测试的隔离数据库、以及显式开启
``NDR_AUTO_MIGRATE=1`` 时的启动流程。默认不会自动迁移，避免隐式修改用户书库。
"""

from __future__ import annotations

from alembic import command
from alembic.config import Config

from ..config import Settings, get_settings


def build_alembic_config(settings: Settings) -> Config:
    config = Config(str(settings.alembic_ini_path))
    # 注入 URL：alembic.ini 中该项留空，避免出现第二份数据库路径真相。
    config.attributes["sqlalchemy_url"] = settings.database_url
    return config


def run_migrations(settings: Settings | None = None, *, revision: str = "head") -> None:
    """迁移到指定版本（默认 head）；重复运行安全，且会先建立数据目录。"""

    resolved = settings or get_settings()
    resolved.ensure_data_dir()
    command.upgrade(build_alembic_config(resolved), revision)
