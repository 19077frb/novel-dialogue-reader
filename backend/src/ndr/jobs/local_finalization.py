"""Retry exited workers' local settlement only, never provider dispatch."""

import threading
import time

from ..diagnostics import flush_repeats, log_event, log_failure

_guard = threading.Lock()
_pending = {}


class PendingLocalWrite(Exception):
    """An in-memory returned receipt awaiting local persistence, not redispatch."""

    def __init__(self, persist):
        super().__init__("模型已返回，正在等待本地保存")
        self.persist = persist


def defer_settlement(factory, job_id, settle):
    with _guard:
        _pending[(factory, job_id)] = (settle, 0, time.monotonic())
    log_event("settlement_deferred", job_id=job_id, stage="local_finalization",
              reason_code="local_write_pending")


def settlement_pending(job_id):
    with _guard:
        return any(key[1] == job_id for key in _pending)


def settlement_progress(job_id, progress):
    from ..storage.write_queue import writer_progress

    writing = writer_progress(job_id)
    if writing:
        return {**(progress or {}), **writing}
    if not settlement_pending(job_id):
        return progress
    return {**(progress or {}), "stage": "local_finalization_pending",
            "message": "执行已退出，正在等待数据库保存状态；不会重发模型调用"}


def retry_settlements(factory):
    with _guard:
        entries = [(key, value) for key, value in _pending.items()
                   if key[0] is factory and value[2] <= time.monotonic()]
    for key, value in entries:
        settle, attempts, _ = value
        try:
            settle()
        except Exception as error:  # local recovery must not terminate the monitor
            log_failure("settlement_retry", key[1], error, stage="local_finalization",
                        reason_code="local_settlement_failed", attempt=attempts + 1,
                        deduplicate=True)
            with _guard:
                if _pending.get(key) is value:
                    _pending[key] = (settle, attempts + 1,
                                     time.monotonic() + min(30, 2 ** min(attempts, 5)))
        else:
            with _guard:
                if _pending.get(key) is value:
                    del _pending[key]
            flush_repeats(job_id=key[1])
            log_event("settlement_recovered", job_id=key[1], stage="local_finalization",
                      attempt=attempts + 1, reason_code="local_write_recovered")


def forget_settlements(factory):
    with _guard:
        for key in list(_pending):
            if key[0] is factory:
                del _pending[key]
