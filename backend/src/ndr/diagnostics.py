"""Bounded, content-free JSON task diagnostics isolated from application work."""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import sys
import threading
from collections import OrderedDict
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import monotonic

logger = logging.getLogger("ndr.diagnostics")
MAX_RECORD_BYTES = 8 * 1024
MAX_CAUSES = 4
MAX_FRAMES = 8
REPEAT_WINDOW_SECONDS = 60
MAX_REPEAT_KEYS = 256
_scope = ContextVar("diagnostic_directory", default=None)
_INVALID_SCOPE = object()
_guard = threading.RLock()
_io_guard = threading.Lock()
_handlers = {}
_repeats = OrderedDict()
_reported_io_failure = False
_TOKEN = re.compile(r"[A-Za-z0-9_./:{}-]{1,120}\Z")
_ID_FIELDS = {"job_id", "run_id", "window_id", "request_id", "artifact_id", "book_id", "profile_id"}
_TEXT_FIELDS = _ID_FIELDS | {
    "stage", "operation", "state", "kind", "reason_code", "error_code", "provider_kind",
    "retry_type", "method", "route", "code", "type", "error_type", "trash", "action",
}
_NUMBER_FIELDS = {
    "attempt", "attempts", "limit", "calls", "count", "elapsed_ms", "delay_ms",
    "wait_ms", "hold_ms", "depth", "initial_hold_ms", "write_context_ms", "work_ms",
    "commit_ms", "tokens", "total_tokens", "input_tokens", "output_tokens",
    "reserved_tokens", "windows_total", "windows_done", "cached_windows", "unknown_runs",
    "generation", "failed_count", "candidate_count", "status_code", "http_status",
    "timeout_seconds", "requested_max_tokens", "reasoning_chars", "max_tokens",
    "paused_jobs", "interrupted_jobs", "stale_runs",
}
_BOOL_FIELDS = {"usage_unknown", "restored", "retryable", "created", "dispatched"}
_DETAIL_NUMBERS = {
    "status_code", "http_status", "timeout_seconds", "elapsed_ms", "requested_max_tokens",
    "reasoning_chars",
}
_DETAIL_TOKENS = {
    "timeout_type", "transport_type", "stage", "reason_code", "finish_reason", "stop_reason",
}
_DETAIL_VALUES = {
    "timeout_type": {"TimeoutException", "ReadTimeout", "ConnectTimeout",
                     "WriteTimeout", "PoolTimeout"},
    "transport_type": {"RequestError", "TransportError", "ConnectError", "ReadError", "WriteError",
                       "CloseError", "NetworkError", "ProtocolError", "LocalProtocolError",
                       "RemoteProtocolError", "ProxyError", "UnsupportedProtocol", "DecodingError",
                       "TooManyRedirects"},
    "stage": {"http_request", "http_response", "http_response_json", "http_response_shape",
              "model_content", "model_json", "schema", "semantic_validation"},
    "reason_code": {"missing_choices", "invalid_choice", "missing_message", "empty_content",
                    "invalid_response_object", "invalid_response_json", "invalid_model_json"},
    "finish_reason": {"stop", "length", "content_filter", "tool_calls", "function_call",
                      "end_turn", "stop_sequence", "max_tokens", "pause_turn", "refusal"},
    "stop_reason": {"end_turn", "stop_sequence", "max_tokens", "tool_use", "pause_turn", "refusal"},
}
_LEGACY_FIELDS = {
    "book_delete_cleanup_pending": ("book_id", "trash", "error_type"),
    "export_receipt_cleanup_failed": ("error_type",),
    "provider_client_close_failed": ("error_type",),
}


def _token(value):
    return value if isinstance(value, str) and _TOKEN.fullmatch(value) else "unknown"


def _directory_key(directory):
    return os.path.normcase(str(Path(directory).resolve()))


def _safe_fields(fields):
    result = {}
    for key, value in fields.items():
        if key in _TEXT_FIELDS:
            result[key] = None if value is None else _token(value)
        elif key in _NUMBER_FIELDS and type(value) in (int, float):
            if math.isfinite(value) and abs(value) <= 10**18:
                result[key] = value
        elif key in _BOOL_FIELDS and type(value) is bool:
            result[key] = value
    return result


def _frames(error):
    frames = []
    count = 0
    current = error.__traceback__
    while current is not None:
        code = current.tb_frame.f_code
        frames.append({"file": _token(Path(code.co_filename).name),
                       "line": current.tb_lineno, "function": _token(code.co_name)})
        if len(frames) > MAX_FRAMES:
            del frames[0]
        count += 1
        current = current.tb_next
    return frames, count > MAX_FRAMES


def _sqlite_fields(error):
    if not isinstance(error, sqlite3.Error):
        return {}
    code = getattr(error, "sqlite_errorcode", None)
    if type(code) is not int:
        return {}
    # Attribute text can be arbitrary; derive names from sqlite's numeric constants.
    base_names = {
        1: "SQLITE_ERROR", 2: "SQLITE_INTERNAL", 3: "SQLITE_PERM", 4: "SQLITE_ABORT",
        5: "SQLITE_BUSY", 6: "SQLITE_LOCKED", 7: "SQLITE_NOMEM", 8: "SQLITE_READONLY",
        9: "SQLITE_INTERRUPT", 10: "SQLITE_IOERR", 11: "SQLITE_CORRUPT", 12: "SQLITE_NOTFOUND",
        13: "SQLITE_FULL", 14: "SQLITE_CANTOPEN", 15: "SQLITE_PROTOCOL", 16: "SQLITE_EMPTY",
        17: "SQLITE_SCHEMA", 18: "SQLITE_TOOBIG", 19: "SQLITE_CONSTRAINT", 20: "SQLITE_MISMATCH",
        21: "SQLITE_MISUSE", 22: "SQLITE_NOLFS", 23: "SQLITE_AUTH", 24: "SQLITE_FORMAT",
        25: "SQLITE_RANGE", 26: "SQLITE_NOTADB", 27: "SQLITE_NOTICE", 28: "SQLITE_WARNING",
    }
    name = base_names.get(code & 0xFF)
    if name and code > 0xFF:
        name = next((candidate for candidate in dir(sqlite3)
                     if candidate.startswith(name + "_")
                     and getattr(sqlite3, candidate) == code), name)
    result = {"sqlite_code": code}
    if name:
        result["sqlite_name"] = name
    return result


def _error_item(error):
    from pydantic import ValidationError

    from .llm.errors import ProviderError

    frames, truncated = _frames(error)
    result = {"type": _token(type(error).__name__), "module": _token(type(error).__module__)}
    if frames:
        result["locations"] = frames
    if truncated:
        result["locations_truncated"] = True
    result.update(_sqlite_fields(error))
    if isinstance(error, OSError):
        for field in ("errno", "winerror"):
            value = getattr(error, field, None)
            if type(value) is int:
                result[field] = value
        if error.errno in errno.errorcode:
            result["errno_name"] = errno.errorcode[error.errno]
    if isinstance(error, ProviderError):
        result.update(kind=error.kind.value, code=error.code.value, retryable=bool(error.retryable))
        details = error.details if isinstance(error.details, dict) else {}
        safe = _safe_fields({key: value for key, value in details.items()
                             if key in _DETAIL_NUMBERS})
        safe.update({key: value if value in _DETAIL_VALUES[key] else "unknown"
                     for key, value in details.items()
                     if key in _DETAIL_TOKENS and isinstance(value, str)})
        if safe:
            result["details"] = safe
    if isinstance(error, json.JSONDecodeError):
        result.update(line=error.lineno, column=error.colno, position=error.pos)
    if isinstance(error, ValidationError):
        result["issue_count"] = error.error_count()
        # Never serialize input, ctx, message, or model-authored locations.
        result["issue_types"] = list(dict.fromkeys(
            _token(issue.get("type", "unknown"))
            for issue in error.errors(include_input=False, include_context=False,
                                      include_url=False)[:8]
        ))
    return result


def error_summary(error):
    """Return only typed evidence, never exception text or arbitrary attributes."""
    try:
        from sqlalchemy.exc import DBAPIError

        chain, seen = [], set()
        current = error
        while (isinstance(current, BaseException) and id(current) not in seen
               and len(chain) < MAX_CAUSES):
            seen.add(id(current))
            chain.append(_error_item(current))
            original = current.orig if isinstance(current, DBAPIError) else None
            current = (current.__cause__ or original or
                       (None if current.__suppress_context__ else current.__context__))
        result = {"chain": chain}
        if current is not None:
            result["chain_truncated"] = True
        return result
    except Exception:
        return {"chain": [{"type": "diagnostic_summary_unavailable"}]}


def _encode(payload):
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) <= MAX_RECORD_BYTES - 2:
        return encoded
    # Keep correlation and error categories when a long chain exhausts the budget.
    payload = dict(payload)
    payload["truncated"] = True
    error = payload.get("error")
    if isinstance(error, dict):
        payload["error"] = {"chain": [
            {**item, **({"locations": item["locations"][-1:], "locations_truncated": True}
                       if item.get("locations") else {})}
            for item in error.get("chain", [])
        ], "locations_truncated": True}
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_RECORD_BYTES - 2:
        retained = {"time", "level", "event", "schema_version", "error", "truncated"} | _ID_FIELDS
        payload = {key: value for key, value in payload.items() if key in retained}
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_RECORD_BYTES - 2:
        # Last-resort bound also applies if a future summary adds larger typed fields.
        payload = {"schema_version": 1, "event": _token(payload.get("event")), "truncated": True}
        encoded = json.dumps(payload, separators=(",", ":"))
    return encoded


def _io_failure():
    global _reported_io_failure
    with _io_guard:
        if _reported_io_failure:
            return
        _reported_io_failure = True
    with suppress(Exception):
        sys.stderr.write("NDR diagnostics unavailable: unable to write local diagnostic log.\n")


class _DiagnosticFormatter(logging.Formatter):
    def format(self, record):
        try:
            payload = getattr(record, "diagnostic_payload", None)
            if payload is None:
                event = record.msg.split(" ", 1)[0] if isinstance(record.msg, str) else "unknown"
                names = _LEGACY_FIELDS.get(event)
                values = record.args if isinstance(record.args, tuple) else ()
                payload = {"event": event if names else "unstructured_diagnostic",
                           **(_safe_fields(dict(zip(names, values, strict=True))) if names else {})}
                payload.update(schema_version=1,
                               time=datetime.fromtimestamp(record.created, UTC).isoformat(),
                               level=record.levelname)
            return _encode(payload)
        except Exception:
            return '{"schema_version":1,"event":"diagnostic_format_failed"}'


class _SafeRecordFilter(logging.Filter):
    def filter(self, record):
        # Sanitize before propagation as root handlers may append exc_info verbatim.
        try:
            if not hasattr(record, "diagnostic_payload"):
                rendered = _DiagnosticFormatter().format(record)
                record.diagnostic_payload = json.loads(rendered)
            record.msg = _encode(record.diagnostic_payload)
        except Exception:
            record.msg = '{"schema_version":1,"event":"diagnostic_format_failed"}'
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


logger.addFilter(_SafeRecordFilter())


class _DiagnosticHandler(RotatingFileHandler):
    def __init__(self, directory):
        self.directory = directory
        self.references = 1
        super().__init__(Path(directory) / "tasks.log", maxBytes=5 * 1024 * 1024,
                         backupCount=3, encoding="utf-8")
        self.setFormatter(_DiagnosticFormatter())

    def filter(self, record):
        selected = getattr(record, "diagnostic_directory", _scope.get())
        with _guard:
            if (not hasattr(record, "diagnostic_directory")
                    and selected is None and len(_handlers) == 1):
                selected = next(iter(_handlers))
        return selected == self.directory

    def handleError(self, record):
        _io_failure()

    def emit(self, record):
        try:
            super().emit(record)
        except Exception:
            _io_failure()

    def flush(self):
        try:
            super().flush()
        except Exception:
            _io_failure()

    def close(self):
        try:
            super().close()
        except Exception:
            _io_failure()


@contextmanager
def diagnostic_scope(directory):
    try:
        selected = _directory_key(directory)
    except Exception:
        # A broken scope must never fall back to another active library.
        selected = _INVALID_SCOPE
        _io_failure()
    token = _scope.set(selected)
    try:
        yield
    finally:
        try:
            _scope.reset(token)
        except Exception:
            _io_failure()


def start_diagnostics(directory: Path):
    try:
        key = _directory_key(directory)
        with _guard:
            if key in _handlers:
                _handlers[key].references += 1
                return _handlers[key]
            Path(directory).mkdir(parents=True, exist_ok=True)
            handler = _DiagnosticHandler(key)
            _handlers[key] = handler
            logger.setLevel(logging.INFO)
            logger.addHandler(handler)
            return handler
    except Exception:
        _io_failure()
        return None


def _write(payload, directory):
    logger.log(getattr(logging, payload["level"], logging.INFO), _encode(payload),
               extra={"diagnostic_payload": payload, "diagnostic_directory": directory})


def _repeat_summary(entry):
    if entry["count"]:
        payload = {**entry["payload"], "event": "diagnostic_repeated",
                   "original_event": entry["payload"]["event"], "repeat_count": entry["count"],
                   "fingerprint": entry["fingerprint"],
                   "first_seen": entry["payload"]["time"], "last_seen": entry["last_seen"],
                   "time": datetime.now(UTC).isoformat()}
        if "error" in payload:
            payload["error"] = {"chain": [
                {key: value for key, value in cause.items()
                 if key not in {"locations", "locations_truncated"}}
                for cause in payload["error"].get("chain", [])
            ]}
        _write(payload, entry["directory"])


def flush_repeats(job_id=None):
    """Flush suppressed counts in the current library, optionally for one job."""
    try:
        with _guard:
            directory = _scope.get()
            if directory is None and len(_handlers) == 1:
                directory = next(iter(_handlers))
            for key, entry in list(_repeats.items()):
                if entry["directory"] == directory and (
                    job_id is None or entry["payload"].get("job_id") == job_id
                ):
                    _repeat_summary(entry)
                    del _repeats[key]
    except Exception:
        _io_failure()


def stop_diagnostics(handler):
    if handler is None:
        return
    try:
        with _guard:
            if _handlers.get(handler.directory) is not handler:
                return
            handler.references -= 1
            if handler.references:
                return
            with diagnostic_scope(handler.directory):
                flush_repeats()
            logger.removeHandler(handler)
            del _handlers[handler.directory]
            handler.close()
    except Exception:
        _io_failure()


def log_event(event, *, level=logging.INFO, error=None, deduplicate=False, **fields):
    """Emit a safe event. Diagnostics must never change the caller's outcome."""
    try:
        if not logger.isEnabledFor(level):
            return
        payload = {"schema_version": 1, "time": datetime.now(UTC).isoformat(),
                   "level": logging.getLevelName(level), "event": _token(event),
                   **_safe_fields(fields)}
        if error is not None:
            payload["error"] = error_summary(error)
        directory = _scope.get()
        with _guard:
            if directory is None and len(_handlers) == 1:
                directory = next(iter(_handlers))
            now = monotonic()
            for entry in list(_repeats.values()):
                if now - entry["started"] >= REPEAT_WINDOW_SECONDS:
                    _repeat_summary(entry)
                    entry["started"] = now
                    entry["count"] = 0
            if deduplicate and directory is not None and directory is not _INVALID_SCOPE:
                identity_fields = _ID_FIELDS | {
                    "event", "stage", "operation", "reason_code", "error_code",
                    "provider_kind", "retry_type", "error",
                }
                identity = {key: value for key, value in payload.items() if key in identity_fields}
                if "error" in identity:
                    causes = []
                    for cause in identity["error"].get("chain", []):
                        stable = dict(cause)
                        if "details" in cause:
                            stable["details"] = {
                                key: value for key, value in cause["details"].items()
                                if key != "elapsed_ms"
                            }
                        causes.append(stable)
                    identity["error"] = {"chain": causes}
                encoded_identity = _encode(identity)
                key = (directory, encoded_identity)
                if key in _repeats:
                    _repeats[key]["count"] += 1
                    _repeats[key]["last_seen"] = payload["time"]
                    _repeats.move_to_end(key)
                    return
                if len(_repeats) >= MAX_REPEAT_KEYS:
                    _, oldest = _repeats.popitem(last=False)
                    _repeat_summary(oldest)
                _repeats[key] = {"payload": payload, "directory": directory,
                                 "started": now, "count": 0, "last_seen": payload["time"],
                                 "fingerprint": hashlib.sha256(
                                     encoded_identity.encode("utf-8")).hexdigest()[:16]}
            _write(payload, directory)
    except Exception:
        _io_failure()


def log_failure(operation, job_id, error, **fields):
    log_event("task_failure", level=logging.ERROR, error=error,
              operation=operation, job_id=job_id, **fields)
