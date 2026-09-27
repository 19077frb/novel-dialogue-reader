"""数据目录内的路径解析。

所有落盘内容都必须先经过 :func:`resolve_within`：相对路径解析到数据目录内部，
越界（``..``、绝对路径、符号链接逃逸）一律拒绝。这样 API 永远不会暴露任意磁盘读写能力。
"""

from __future__ import annotations

from pathlib import Path

from ..config import Settings


class UnsafePathError(ValueError):
    """相对路径越出数据目录。"""


def data_root(settings: Settings) -> Path:
    root = settings.data_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_within(settings: Settings, relative: str | Path) -> Path:
    """把 ``relative`` 解析到数据目录内；越界时抛 :class:`UnsafePathError`。"""

    candidate = Path(relative)
    if candidate.is_absolute():
        raise UnsafePathError(f"不接受绝对路径：{relative}")
    root = data_root(settings)
    resolved = (root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise UnsafePathError(f"路径越出数据目录：{relative}")
    return resolved


def to_relative(settings: Settings, path: Path) -> str:
    """把数据目录内的路径转成相对路径字符串（POSIX 分隔符，便于跨平台存储）。"""

    root = data_root(settings)
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise UnsafePathError(f"路径不在数据目录内：{path}")
    return resolved.relative_to(root).as_posix()


def book_dir(settings: Settings, book_id: str) -> Path:
    return resolve_within(settings, Path("books") / book_id)


def book_source_path(settings: Settings, book_id: str, suffix: str = ".bin") -> Path:
    """书籍原始文件：一个书籍只保留一份不可变源文件。"""

    return resolve_within(settings, Path("books") / book_id / f"source{suffix}")


def version_dir(settings: Settings, book_id: str, version_id: str) -> Path:
    return resolve_within(settings, Path("books") / book_id / "versions" / version_id)


def version_canonical_path(settings: Settings, book_id: str, version_id: str) -> Path:
    return resolve_within(
        settings, Path("books") / book_id / "versions" / version_id / "canonical.txt"
    )
