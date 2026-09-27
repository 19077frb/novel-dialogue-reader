from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from ndr.app import create_app
from ndr.config import Settings
from ndr.storage.engine import create_db_engine
from ndr.storage.migrate import run_migrations


@pytest.fixture()
def tmp_settings(tmp_path: Path) -> Settings:
    """隔离的数据目录，避免测试触碰真实书库。"""

    return Settings(data_dir=tmp_path / "data")


@pytest.fixture()
def migrated_settings(tmp_settings: Settings) -> Settings:
    """已迁移到 head 的隔离数据库配置。"""

    run_migrations(tmp_settings)
    return tmp_settings


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
