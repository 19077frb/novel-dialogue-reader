"""Alembic 迁移环境。

数据库 URL 优先取调用方注入的 ``config.attributes["sqlalchemy_url"]``（测试使用），
否则取 ``alembic.ini`` 中的 ``sqlalchemy.url``，最后回落到应用配置推导出的 URL。
元数据来自 ``ndr.storage.models``，保证模型与迁移不漂移。
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from ndr.config import Settings
from ndr.storage.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def database_url() -> str:
    override = config.attributes.get("sqlalchemy_url") or config.get_main_option("sqlalchemy.url")
    if override:
        return str(override)
    # `alembic -c backend/alembic.ini upgrade head` is also the first-run path
    # used by the Windows launch scripts. SQLite creates the database file, but
    # it does not create a missing parent directory for it.
    settings = Settings()
    settings.ensure_data_dir()
    return settings.database_url


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
