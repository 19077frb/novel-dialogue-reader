from __future__ import annotations

from pathlib import Path

import pytest

from ndr.config import Settings


@pytest.fixture()
def tmp_settings(tmp_path: Path) -> Settings:
    """隔离的数据目录，避免测试触碰真实书库。"""

    return Settings(data_dir=tmp_path / "data")
