"""T10 单元/集成：语义缓存键与 result_cache 存储（F16）。"""

from __future__ import annotations

from sqlalchemy import select

from ndr.storage.cache import (
    CACHE_SCHEMA_VERSION,
    CacheKeyParts,
    ResultCacheStore,
    compute_cache_key,
)
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.models import ResultCache
from ndr.storage.transactions import transaction


def _parts(**overrides) -> CacheKeyParts:
    payload = {
        "book_version_id": "v1",
        "target_ids": ["q1", "q2"],
        "input_fingerprint": "input-hash",
        "model": "example-model",
        "params": {"temperature": 0.2},
        "protocol_version": "chat-completions-compatible",
        "prompt_version": "labeling-1",
        "schema_version": "1.0",
        "policy_version": "context-1",
        "dependency_hash": "dep-hash",
        "reading_mode": "initial",
        "visible_horizon_cp": 1200,
    }
    payload.update(overrides)
    return CacheKeyParts(**payload)


def test_same_semantic_input_produces_same_key() -> None:
    assert compute_cache_key(_parts()) == compute_cache_key(_parts())
    # 目标顺序不同但集合相同 → 同一语义输入（由调用方排序保证）
    assert compute_cache_key(_parts(target_ids=["q2", "q1"])) != compute_cache_key(_parts())


def test_cache_key_changes_with_prompt_policy_mode_and_horizon() -> None:
    base = compute_cache_key(_parts())
    assert compute_cache_key(_parts(prompt_version="labeling-2")) != base
    assert compute_cache_key(_parts(policy_version="context-2")) != base
    assert compute_cache_key(_parts(schema_version="1.1")) != base
    assert compute_cache_key(_parts(reading_mode="reread")) != base
    assert compute_cache_key(_parts(visible_horizon_cp=900)) != base
    assert compute_cache_key(_parts(dependency_hash="dep-2")) != base
    assert compute_cache_key(_parts(model="another-model")) != base
    assert compute_cache_key(_parts(input_fingerprint="other-input")) != base


def test_cache_key_ignores_job_id_and_purpose() -> None:
    """键里没有 job_id / preview-process 目的：同一语义输入必须命中同一条缓存。"""

    key = compute_cache_key(_parts())
    assert "job" not in key
    parts = _parts().as_dict()
    assert "job_id" not in parts
    assert "purpose" not in parts
    assert parts["cache_schema_version"] == CACHE_SCHEMA_VERSION


def test_result_cache_store_round_trip_and_no_overwrite(migrated_settings) -> None:  # noqa: ANN001
    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            store = ResultCacheStore(session)
            key = compute_cache_key(_parts())
            assert store.get(key) is None
            store.put(cache_key=key, result_json='{"schema_version": "1.0"}', dependency_hash="dep-hash")
            first = store.get(key)
            assert first is not None
            assert first.payload() == {"schema_version": "1.0"}
            assert first.dependency_hash == "dep-hash"
            # 相同键再次写入不覆盖已有结果（保留原生成时点）
            store.put(cache_key=key, result_json='{"schema_version": "9.9"}')
            assert store.get(key).result_json == '{"schema_version": "1.0"}'

        with transaction(factory) as session:
            rows = list(session.execute(select(ResultCache)).scalars())
            assert len(rows) == 1
    finally:
        engine.dispose()
