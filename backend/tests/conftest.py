from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from fixtures.database import initialize_test_database
from ndr.app import create_app
from ndr.config import Settings
from ndr.storage.engine import create_db_engine
from ndr.storage.migrate import run_migrations


@pytest.fixture()
def tmp_settings(tmp_path: Path) -> Settings:
    """隔离的数据目录，避免测试触碰真实书库。"""

    # 测试绝不触碰真实的系统凭据库：显式使用会话凭据后端。
    return Settings(data_dir=tmp_path / "data", credential_backend="session")


@pytest.fixture()
def migrated_settings(tmp_settings: Settings, migrated_database_template: Path) -> Settings:
    """已迁移到 head 的隔离数据库配置。"""

    initialize_test_database(tmp_settings, migrated_database_template)
    return tmp_settings


@pytest.fixture(scope="session")
def migrated_database_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One real migration per pytest process; clients never write to this database."""
    settings = Settings(data_dir=tmp_path_factory.mktemp("migrated-template"),
                        credential_backend="session")
    run_migrations(settings)
    return settings.database_path


@pytest.fixture()
def engine(tmp_settings: Settings) -> Iterator[Engine]:
    db = create_db_engine(tmp_settings)
    try:
        yield db
    finally:
        db.dispose()


@pytest.fixture()
def migrated_engine(migrated_settings: Settings) -> Iterator[Engine]:
    db = create_db_engine(migrated_settings)
    try:
        yield db
    finally:
        db.dispose()


@pytest.fixture()
def client(tmp_settings: Settings) -> Iterator[TestClient]:
    app = create_app(tmp_settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def migrated_client(migrated_settings: Settings) -> Iterator[TestClient]:
    app = create_app(migrated_settings)
    with TestClient(app) as test_client:
        yield test_client

@pytest.fixture()
def fake_provider_client(
    tmp_path: Path, migrated_database_template: Path,
) -> Iterator[TestClient]:
    """显式启用 FakeProvider 的隔离客户端（仅测试用；不发任何网络请求）。"""

    settings = Settings(
        data_dir=tmp_path / "data",
        credential_backend="session",
        allow_fake_provider=True,
    )
    initialize_test_database(settings, migrated_database_template)
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
