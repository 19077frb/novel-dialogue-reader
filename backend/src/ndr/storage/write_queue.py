"""Process-local FIFO writer turns; sessions stay on their calling thread.

Only root write transactions reserve a turn. Reads and provider calls do not.
Offline maintenance and other processes still require SQLite's own locking.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import weakref
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from time import monotonic

from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import TextClause

from ..diagnostics import logger

_job_context = ContextVar("writer_job", default=None)
_registry_guard = threading.Lock()
_queues = weakref.WeakValueDictionary()
_engine_queues = weakref.WeakKeyDictionary()
_progress_guard = threading.Lock()
_progress = {}
WRITE_QUEUE_TIMEOUT = 60.0


@dataclass(eq=False)
class WriterTurn:
    job_id: str | None
    thread_id: int
    queued_at: float
    acquired_at: float | None = None


def _busy():
    error = sqlite3.OperationalError("数据库写入排队等待超时")
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY
    error.sqlite_errorname = "SQLITE_BUSY"
    return OperationalError(None, None, error)


class WriteQueue:
    def __init__(self):
        self._condition = threading.Condition()
        self._waiting = deque()
        self._active = None

    def acquire(self, job_id=None, *, timeout=WRITE_QUEUE_TIMEOUT):
        turn = WriterTurn(job_id, threading.get_ident(), monotonic())
        with self._condition:
            # A second session on the same thread cannot wait for itself to commit.
            if self._active and self._active.thread_id == turn.thread_id:
                logger.warning("write_queue_nested_session job=%s", job_id)
                raise _busy()
            self._waiting.append(turn)
            with _progress_guard:
                _progress[turn] = "write_queue_waiting"
            try:
                while self._active is not None or self._waiting[0] is not turn:
                    remaining = timeout - (monotonic() - turn.queued_at)
                    if remaining <= 0:
                        logger.warning("write_queue_timeout job=%s wait_ms=%s depth=%s",
                                       job_id, int(timeout * 1000), len(self._waiting))
                        raise _busy()
                    self._condition.wait(remaining)
                self._waiting.popleft()
                self._active = turn
                turn.acquired_at = monotonic()
                with _progress_guard:
                    _progress[turn] = "write_queue_active"
                wait_ms = int((turn.acquired_at - turn.queued_at) * 1000)
                log = logger.info if wait_ms >= 100 else logger.debug
                log("write_queue_acquired job=%s wait_ms=%s depth=%s", job_id,
                    wait_ms, len(self._waiting))
                return turn
            except BaseException:
                if self._active is turn:
                    self._active = None
                else:
                    self._waiting.remove(turn)
                with _progress_guard:
                    _progress.pop(turn, None)
                self._condition.notify_all()
                raise

    def release(self, turn):
        with self._condition:
            if self._active is not turn:
                return
            self._active = None
            with _progress_guard:
                _progress.pop(turn, None)
            hold_ms = int((monotonic() - turn.acquired_at) * 1000)
            log = logger.info if hold_ms >= 1000 else logger.debug
            log("write_queue_released job=%s hold_ms=%s depth=%s", turn.job_id,
                hold_ms, len(self._waiting))
            self._condition.notify_all()


def queue_for_engine(engine):
    if engine.dialect.name != "sqlite":
        return None
    with _registry_guard:
        if engine in _engine_queues:
            return _engine_queues[engine]
        database = engine.url.database
        # Separate in-memory engines are separate databases.
        key = (os.path.normcase(str(Path(database).resolve()))
               if database and database != ":memory:" else id(engine))
        queue = _queues.get(key)
        if queue is None:
            queue = WriteQueue()
            _queues[key] = queue
        _engine_queues[engine] = queue
        return queue


def reserve_write(session, *, job_id=None):
    if "ndr_writer_turn" in session.info:
        return
    bind = session.get_bind()
    queue = queue_for_engine(getattr(bind, "engine", bind))
    if queue is None:
        return
    turn = queue.acquire(job_id or _job_context.get())
    try:
        # Ensure close/rollback can release even if no SQL was reached.
        if not session.in_transaction():
            session.begin()
        session.info["ndr_writer_turn"] = (queue, turn)
        if not event.contains(session, "after_transaction_end", _release_root):
            event.listen(session, "after_transaction_end", _release_root)
    except BaseException:
        session.info.pop("ndr_writer_turn", None)
        queue.release(turn)
        raise


def _release_root(session, transaction):
    if transaction.parent is None:
        reserved = session.info.pop("ndr_writer_turn", None)
        if reserved:
            reserved[0].release(reserved[1])


class QueuedSession(Session):
    """Catch ORM/Core writes outside explicit application write contexts."""


@event.listens_for(QueuedSession, "before_flush")
def _reserve_flush(session, flush_context, instances):
    reserve_write(session)


@event.listens_for(QueuedSession, "do_orm_execute")
def _reserve_statement(execute_state):
    statement = execute_state.statement
    writing = bool(getattr(statement, "is_dml", False))
    if isinstance(statement, TextClause):
        sql = statement.text.lstrip().upper()
        writing = sql.startswith(("BEGIN IMMEDIATE", "BEGIN EXCLUSIVE", "INSERT ",
                                  "UPDATE ", "DELETE ", "REPLACE ", "CREATE ",
                                  "ALTER ", "DROP "))
    if writing:
        reserve_write(execute_state.session)


def writer_job_scope(function):
    @wraps(function)
    def scoped(*args, **kwargs):
        token = _job_context.set(kwargs.get("job_id") or _job_context.get())
        try:
            return function(*args, **kwargs)
        finally:
            _job_context.reset(token)
    return scoped


def writer_progress(job_id):
    with _progress_guard:
        stages = [stage for turn, stage in _progress.items() if turn.job_id == job_id]
    if not stages:
        return None
    stage = "write_queue_waiting" if "write_queue_waiting" in stages else "write_queue_active"
    return {"stage": stage, "message": "正在等待保存" if stage == "write_queue_waiting"
            else "正在保存任务数据"}
