"""稳定 ID 派生（DEVELOPMENT.md 3.1：Quote ID 由原文版本、位置、扫描器版本稳定派生）。

同一份 canonical 文本、同一套扫描器版本重复扫描时，ID 必须完全一致，
这样 T12 的人工更正引用不会因为重新扫描而失效。
"""

from __future__ import annotations

import hashlib

_PREFIX = {"quote": "q", "gap": "g"}


def _stable_id(kind: str, *parts: object) -> str:
    payload = "|".join(str(part) for part in parts)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()  # noqa: S324 - 仅用于稳定 ID，不做安全用途
    return _PREFIX[kind] + digest[:31]


def quote_id_for(
    book_version_id: str, start_cp: int, end_cp: int, scanner_version: str
) -> str:
    return _stable_id("quote", book_version_id, start_cp, end_cp, scanner_version)


def gap_id_for(
    book_version_id: str, start_cp: int, end_cp: int, scanner_version: str
) -> str:
    return _stable_id("gap", book_version_id, start_cp, end_cp, scanner_version)
