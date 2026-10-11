"""事务边界与版本校验。

自动结果提交与人工更正都必须在一个事务内完成；事务期间不得等待网络响应。
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any, TypeVar

from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from .write_queue import reserve_write, writer_job_scope

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
        reserve_write(session)
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def admission_transaction(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Serialize overlap checks and admission on SQLite, not model execution."""
    with transaction(session_factory) as session:
        if session.get_bind().dialect.name == "sqlite":
            session.execute(text("BEGIN IMMEDIATE"))
        yield session


def sqlite_lock_error(error: Exception) -> bool:
    """Only retry SQLite lock contention, never disk/schema/permission failures."""
    if not isinstance(error, OperationalError) or not isinstance(error.orig, sqlite3.Error):
        return False
    code = getattr(error.orig, "sqlite_errorcode", None)
    if isinstance(code, int):
        return code & 0xFF in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
    return str(error.orig).lower() in {
        "database is locked", "database table is locked", "database schema is locked",
    }


def database_error_detail(error: Exception) -> str:
    """Diagnostic without SQL statements, parameters, or user data."""
    if isinstance(error, OperationalError) and isinstance(error.orig, sqlite3.Error):
        name = getattr(error.orig, "sqlite_errorname", None) or type(error.orig).__name__
        code = getattr(error.orig, "sqlite_errorcode", None)
        reason = "数据库写入繁忙" if sqlite_lock_error(error) else "数据库操作失败"
        return f"{reason}（{name}, code={code}）"
    return type(error).__name__


@contextmanager
def local_write_session(session_factory: sessionmaker[Session], *, job_id=None):
    """Acquire a writer with bounded retries before any reads; never replay the body."""
    from ..diagnostics import logger

    session = None
    started = time.monotonic()
    for attempt in range(3):
        candidate = session_factory()
        try:
            reserve_write(candidate, job_id=job_id)
            if candidate.get_bind().dialect.name == "sqlite":
                candidate.execute(text("BEGIN IMMEDIATE"))
            session = candidate
            break
        except OperationalError as error:
            candidate.rollback()
            candidate.close()
            logger.warning("writer_wait job=%s attempt=%s elapsed_ms=%s error=%s",
                           job_id, attempt + 1, int((time.monotonic() - started) * 1000),
                           database_error_detail(error))
            if not sqlite_lock_error(error) or attempt == 2:
                raise
            time.sleep(0.1 * (attempt + 1))
        except Exception:
            candidate.close()
            raise
    acquired = time.monotonic()
    commit_started = None
    commit_seconds = 0.0
    first_release = None

    def before_commit(_):
        nonlocal commit_started
        commit_started = time.monotonic()

    def after_commit(_):
        nonlocal commit_seconds, commit_started, first_release
        now = time.monotonic()
        if first_release is None:
            first_release = now
        if commit_started is not None:
            commit_seconds += now - commit_started
            commit_started = None

    event.listen(session, "before_commit", before_commit)
    event.listen(session, "after_commit", after_commit)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        if commit_started is not None:
            commit_seconds += time.monotonic() - commit_started
        event.remove(session, "before_commit", before_commit)
        event.remove(session, "after_commit", after_commit)
        session.close()
        elapsed = time.monotonic() - started
        if elapsed >= 1:
            held = time.monotonic() - acquired
            logger.info("writer_released job=%s elapsed_ms=%s wait_ms=%s "
                        "initial_hold_ms=%s write_context_ms=%s work_ms=%s commit_ms=%s",
                        job_id, int(elapsed * 1000), int((acquired - started) * 1000),
                        int(((first_release - acquired) if first_release is not None
                             else held) * 1000),
                        int(held * 1000), int(max(0, held - commit_seconds) * 1000),
                        int(commit_seconds * 1000))


@writer_job_scope
def finish_local_write(session_factory: sessionmaker[Session], action: Callable[[Session], T],
                       *, job_id: str | None = None) -> T:
    """Atomic local finalization; retry a fresh transaction, never a model call.

    BEGIN IMMEDIATE precedes reads/SAVEPOINTs so SQLite cannot commit the
    candidate savepoint separately or upgrade a stale read snapshot to a writer.
    Each lock wait uses the engine's busy_timeout; three attempts at most.
    """
    from ..diagnostics import logger

    started = time.monotonic()
    for attempt in range(3):
        try:
            with admission_transaction(session_factory) as session:
                result = action(session)
            elapsed = time.monotonic() - started
            if attempt or elapsed >= 1:
                logger.info("local_write_done job=%s operation=%s attempts=%s elapsed_ms=%s",
                            job_id, action.__name__, attempt + 1, int(elapsed * 1000))
            return result
        except OperationalError as exc:
            logger.warning("local_write_error job=%s operation=%s attempt=%s "
                           "elapsed_ms=%s error=%s",
                           job_id, action.__name__, attempt + 1,
                           int((time.monotonic() - started) * 1000), database_error_detail(exc))
            if not sqlite_lock_error(exc) or attempt == 2:
                raise
            time.sleep(0.1 * (attempt + 1))
    raise AssertionError("unreachable")


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
