"""Bounded compressed call evidence, not a second task state or replay engine."""

import json
import zlib
from collections.abc import Mapping

from .cache import fingerprint

ARCHIVE_VERSION = "inference-archive-1"
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "api_key",
        "apikey",
        "password",
        "secret",
        "access_token",
        "refresh_token",
        "headers",
    }
)


def _redact(value):
    if isinstance(value, Mapping):
        return {
            str(k): "[REDACTED]" if str(k).lower() in SENSITIVE_KEYS else _redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


def encode_archive(document):
    safe = _redact(document)
    try:
        raw = json.dumps(
            {**safe, "version": ARCHIVE_VERSION, "complete": True},
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raw = json.dumps(
            {"version": ARCHIVE_VERSION, "complete": False, "omitted": "not_serializable"}
        ).encode()
    if len(raw) > MAX_ARCHIVE_BYTES:
        raw = json.dumps(
            {
                "version": ARCHIVE_VERSION,
                "complete": False,
                "omitted": "size_limit",
                "uncompressed_bytes": len(raw),
                "document_fingerprint": fingerprint(safe),
            }
        ).encode()
    return zlib.compress(raw, level=6)


def decode_archive(blob, *, require_complete=True):
    if blob is None:
        raise ValueError("This historical attempt has no archived evidence")
    inflater = zlib.decompressobj()
    try:
        raw = inflater.decompress(blob, MAX_ARCHIVE_BYTES + 1)
        if len(raw) > MAX_ARCHIVE_BYTES or not inflater.eof or inflater.unused_data:
            raise ValueError("Invalid or oversized compressed attempt archive")
        record = json.loads(raw)
    except (zlib.error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Corrupt attempt archive") from exc
    if not isinstance(record, dict) or record.get("version") != ARCHIVE_VERSION:
        raise ValueError("Unsupported attempt archive")
    if require_complete and record.get("complete") is not True:
        raise ValueError("Attempt evidence is incomplete and cannot be replayed")
    return record


def save_run_archive(run, request, *, raw=None, error=None, elapsed_ms=None, phase="prepared"):
    run.call_archive = encode_archive(
        {
            "phase": phase,
            "request": request,
            "adapter_result": getattr(raw, "original_result", raw),
            "provider_receipt": getattr(error, "receipt", None) or getattr(raw, "receipt", None),
            "error": {
                "kind": getattr(getattr(error, "kind", None), "value", None),
                "details": getattr(error, "details", {}),
            }
            if error is not None
            else None,
            "elapsed_ms": elapsed_ms,
        }
    )
