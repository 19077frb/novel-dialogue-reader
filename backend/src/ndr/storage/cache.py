"""语义结果缓存（DEVELOPMENT.md 5.5）。

缓存键包含：原文版本、目标 IDs、**完整实际输入**的指纹、模型与生成参数、
协议/提示/schema/筛选版本、有效依赖状态、阅读模式与证据 horizon。

**排除**：UI 颜色、job_id、`preview/process` 目的、以及不影响语义的显示选项——
所以「先预览、再处理同一范围」必须命中同一条缓存（F16）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ResultCache

CACHE_SCHEMA_VERSION = "cache-2"


def fingerprint(payload: Any) -> str:
    """对任意可序列化内容取稳定指纹（键序固定、UTF-8）。"""

    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CacheKeyParts:
    book_version_id: str
    target_ids: Sequence[str]
    input_fingerprint: str
    model: str
    params: Mapping[str, Any]
    protocol_version: str
    prompt_version: str
    schema_version: str
    policy_version: str
    dependency_hash: str
    reading_mode: str
    visible_horizon_cp: int | None
    acceptance_policy_version: str = "acceptance-1"
    cache_schema_version: str = CACHE_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "book_version_id": self.book_version_id,
            "target_ids": list(self.target_ids),
            "input_fingerprint": self.input_fingerprint,
            "model": self.model,
            "params": dict(self.params),
            "protocol_version": self.protocol_version,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "dependency_hash": self.dependency_hash,
            "reading_mode": self.reading_mode,
            "visible_horizon_cp": self.visible_horizon_cp,
            "acceptance_policy_version": self.acceptance_policy_version,
            "cache_schema_version": self.cache_schema_version,
        }


def compute_cache_key(parts: CacheKeyParts) -> str:
    return fingerprint(parts.as_dict())


@dataclass(frozen=True)
class CachedResult:
    cache_key: str
    schema_version: str
    result_json: str
    dependency_hash: str | None
    created_run_id: str | None
    created_at: datetime

    def payload(self) -> dict[str, Any]:
        try:
            value = json.loads(self.result_json)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}


class ResultCacheStore:
    """`result_cache` 表的薄封装（同 key 只保留一份结果）。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, cache_key: str) -> CachedResult | None:
        row = self.session.execute(
            select(ResultCache).where(ResultCache.cache_key == cache_key)
        ).scalar_one_or_none()
        if row is None:
            return None
        return CachedResult(
            cache_key=row.cache_key,
            schema_version=row.schema_version,
            result_json=row.result_json,
            dependency_hash=row.dependency_hash,
            created_run_id=row.created_run_id,
            created_at=row.created_at,
        )

    def put(
        self,
        *,
        cache_key: str,
        result_json: str,
        dependency_hash: str | None = None,
        created_run_id: str | None = None,
        schema_version: str = CACHE_SCHEMA_VERSION,
    ) -> None:
        existing = self.session.execute(
            select(ResultCache).where(ResultCache.cache_key == cache_key)
        ).scalar_one_or_none()
        if existing is not None:
            # 相同语义输入命中已有结果：不覆盖，返回既有生成时点
            return
        self.session.add(
            ResultCache(
                cache_key=cache_key,
                schema_version=schema_version,
                result_json=result_json,
                dependency_hash=dependency_hash,
                created_run_id=created_run_id,
            )
        )
        self.session.flush()
