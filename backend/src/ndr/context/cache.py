"""进程内缓存：窗口装配材料（canonical 全文 + 引语 / Gap / 段落视图）。

- 键是 ``(book_version_id, canonical_path)``：canonical 与派生数据对版本不可变，
  唯一的变更是重新扫描（``quotes.service.scan_and_store``），它负责显式失效。
- 容量上限防止多本书同时驻留内存（LRU 淘汰）。
- 缓存对象全部是不可变数据（``tuple`` + frozen dataclass），跨线程共享安全。
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass

from .source_selection import GapView, ParagraphView, QuoteView

MAX_CACHED_VERSIONS = 4


@dataclass(frozen=True)
class WindowMaterial:
    """与调用参数无关的重装配成本：全文 + 全量引语 / Gap / 段落视图。"""

    canonical_text: str
    quotes: tuple[QuoteView, ...]
    gaps: tuple[GapView, ...]
    paragraphs: tuple[ParagraphView, ...]


_lock = threading.Lock()
_materials: OrderedDict[tuple[str, str], WindowMaterial] = OrderedDict()


def _key_of(version) -> tuple[str, str]:  # noqa: ANN001 - BookVersion
    return (str(version.id), str(version.canonical_path))


def get_cached_material(version) -> WindowMaterial | None:  # noqa: ANN001 - BookVersion
    key = _key_of(version)
    with _lock:
        material = _materials.get(key)
        if material is not None:
            _materials.move_to_end(key)
        return material


def store_material(version, material: WindowMaterial) -> None:  # noqa: ANN001 - BookVersion
    key = _key_of(version)
    with _lock:
        _materials[key] = material
        _materials.move_to_end(key)
        while len(_materials) > MAX_CACHED_VERSIONS:
            _materials.popitem(last=False)


def invalidate_version(version_id: str) -> None:
    """版本派生数据（引语 / Gap / 节点）变更后必须调用。"""

    with _lock:
        for key in [key for key in _materials if key[0] == version_id]:
            del _materials[key]
