"""单元测试：窗口材料与 canonical 全文的进程内缓存。

门槛：
- 相同版本的重复装配不再重复读文件/查库（估算与批量处理的关键路径）；
- 文件内容变化（mtime/大小变化）后 canonical 文本缓存自动失效；
- 重新扫描（``scan_and_store``）后窗口材料缓存被显式失效，绝不返回旧候选；
- 缓存容量有上限（LRU 淘汰）。

说明：不使用 pytest 的 ``tmp_path``，而是自建隔离数据目录（受限沙箱环境下
跨进程复用临时目录不可靠）。
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import ndr.context.service as context_service
from ndr.config import Settings
from ndr.context.cache import (
    MAX_CACHED_VERSIONS,
    WindowMaterial,
    get_cached_material,
    invalidate_version,
    store_material,
)
from ndr.domain.enums import BookFormat
from ndr.ingest.query import load_canonical_text
from ndr.quotes.service import scan_and_store
from ndr.storage.base import Base
from ndr.storage.models import Book, BookVersion

TEXT = "「雨停了。」少女合上伞。\n少年没有回答。\n「……谢谢。」她低声说。"


@pytest.fixture()
def sandbox_settings() -> Settings:
    """进程内创建、测试结束时清理的隔离数据目录。"""

    root = Path(__file__).resolve().parents[2] / "pytest-sandbox" / uuid.uuid4().hex
    settings = Settings(data_dir=root, credential_backend="session")
    yield settings
    shutil.rmtree(root, ignore_errors=True)


def _version_with_canonical(
    settings: Settings, content: str, *, relative: str
) -> tuple[Session, BookVersion]:
    """一个指向真实 canonical 文件的版本行（内存库）。"""

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    book = Book(title="t", format=BookFormat.TXT, source_sha256="a" * 64)
    session.add(book)
    session.flush()
    path = settings.data_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    version = BookVersion(
        book_id=book.id,
        encoding="utf-8",
        parser_version="p1",
        normalization_version="n1",
        canonical_sha256="b" * 64,
        canonical_length_cp=len(content),
        canonical_path=relative,
    )
    session.add(version)
    session.flush()
    return session, version


def test_canonical_text_cache_reuses_and_invalidates_on_change(
    sandbox_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    relative = "books/b2/canonical.txt"
    path = sandbox_settings.data_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("第一版", encoding="utf-8")
    version = SimpleNamespace(id="v-text", canonical_path=relative)

    calls: list[Path] = []
    real_read_text = Path.read_text

    def spy_read_text(self: Path, *args: object, **kwargs: object) -> str:
        calls.append(self)
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", spy_read_text)

    assert load_canonical_text(sandbox_settings, version) == "第一版"
    assert load_canonical_text(sandbox_settings, version) == "第一版"
    assert len(calls) == 1  # 第二次命中缓存

    path.write_text("第二版", encoding="utf-8")
    assert load_canonical_text(sandbox_settings, version) == "第二版"
    assert len(calls) == 2  # 文件变化后重新读取


def test_window_material_cache_hits_and_invalidation(
    sandbox_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, version = _version_with_canonical(
        sandbox_settings, TEXT, relative="books/b3/canonical.txt"
    )

    calls = {"count": 0}
    real_canonical_of = context_service.canonical_text_of

    def spy_canonical_of(settings: Settings, row: BookVersion) -> str:
        calls["count"] += 1
        return real_canonical_of(settings, row)

    monkeypatch.setattr(context_service, "canonical_text_of", spy_canonical_of)

    first = context_service.load_window_inputs(session, sandbox_settings, version)
    second = context_service.load_window_inputs(session, sandbox_settings, version)
    assert calls["count"] == 1
    assert second.canonical_text == first.canonical_text
    assert tuple(second.quotes) == tuple(first.quotes)

    invalidate_version(version.id)
    context_service.load_window_inputs(session, sandbox_settings, version)
    assert calls["count"] == 2  # 失效后重新装配

    session.close()
    session.get_bind().dispose()


def test_scan_and_store_invalidates_material_cache(sandbox_settings: Settings) -> None:
    session, version = _version_with_canonical(
        sandbox_settings, TEXT, relative="books/b4/canonical.txt"
    )

    # 先装配一次（此时库里还没有候选），把空材料放进缓存。
    empty = context_service.load_window_inputs(session, sandbox_settings, version)
    assert empty.quotes == ()

    outcome = scan_and_store(session, sandbox_settings, version, canonical_text=TEXT)
    assert outcome.quotes > 0  # 确实扫出了候选

    refreshed = context_service.load_window_inputs(session, sandbox_settings, version)
    assert len(tuple(refreshed.quotes)) == outcome.quotes  # 缓存已失效，看到新候选

    session.close()
    session.get_bind().dispose()


def test_material_cache_evicts_oldest() -> None:
    invalidate_version("v-evict")  # 清理同名键，避免测试间串扰
    for index in range(MAX_CACHED_VERSIONS + 1):
        store_material(
            SimpleNamespace(id=f"v-evict-{index}", canonical_path="same"),
            WindowMaterial(canonical_text="t", quotes=(), gaps=(), paragraphs=()),
        )

    # 最旧的一条被 LRU 淘汰；最新的一条仍在。
    assert get_cached_material(SimpleNamespace(id="v-evict-0", canonical_path="same")) is None
    latest = SimpleNamespace(id=f"v-evict-{MAX_CACHED_VERSIONS}", canonical_path="same")
    assert get_cached_material(latest) is not None
    invalidate_version("v-evict")
