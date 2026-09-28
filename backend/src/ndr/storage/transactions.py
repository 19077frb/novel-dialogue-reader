"""事务边界与版本校验。

自动结果提交与人工更正都必须在一个事务内完成；事务期间不得等待网络响应。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, TypeVar

from sqlalchemy.orm import Session, sessionmaker

T = TypeVar("T")


class VersionConflict(Exception):
    """写请求携带的期望版本与当前版本不符；调用方应返回 409 并保留数据。"""

    def __init__(self, *, entity: str, target_id: str, expected: int, current: int) -> None:
        super().__init__(f"{entity} {target_id} 版本冲突：期望 {expected}，当前 {current}")
        self.entity = entity
        self.target_id = target_id
        self.expected = expected
        self.current = current

    @property
    def details(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "target_id": self.target_id,
            "expected_version": self.expected,
            "current_version": self.current,
        }


@contextmanager
def transaction(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """一次提交的事务：异常时回滚，绝不吞掉错误。"""

    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_version(instance: Any, expected_version: int | None) -> None:
    """校验期望版本；``None`` 表示调用方明确不做乐观并发检查。"""

    if expected_version is None:
        return
    current = int(instance.version)
    if current != expected_version:
        raise VersionConflict(
            entity=type(instance).__name__,
            target_id=instance.id,
            expected=expected_version,
            current=current,
        )


def apply_versioned_update(
    session: Session,
    instance: Any,
    *,
    expected_version: int | None,
    changes: Mapping[str, Any],
) -> int:
    """校验版本 → 应用字段 → 递增 version → flush；返回新版本。"""

    check_version(instance, expected_version)
    for field, value in changes.items():
        if not hasattr(instance, field):
            raise AttributeError(f"{type(instance).__name__} 没有字段 {field}")
        setattr(instance, field, value)
    instance.version = int(instance.version) + 1
    session.flush()
    return int(instance.version)
