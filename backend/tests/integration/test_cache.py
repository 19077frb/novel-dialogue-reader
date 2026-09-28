"""T10 单元/集成：语义缓存键与 result_cache 存储（F16）。"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select

from ndr.context.budget import BudgetItemKind
from ndr.jobs.scheduler import _cache_key_for, _output_token_reserve
from ndr.scenes.runner import _messages_for, _restore_output_references
from ndr.scenes.state import SceneState, SpeakerSlot
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


def test_output_reserve_tracks_contract_size_and_model_cap() -> None:
    assert _output_token_reserve({"params": {"max_tokens": 8000}}, 46) == 6144
    assert _output_token_reserve({"params": {"max_tokens": 800}}, 46) == 800
    assert _output_token_reserve({"params": {"max_tokens": "bad"}}, 1) == 384


def test_same_semantic_input_produces_same_key() -> None:
    assert compute_cache_key(_parts()) == compute_cache_key(_parts())
    # 目标顺序不同但集合相同 → 同一语义输入（由调用方排序保证）
    assert compute_cache_key(_parts(target_ids=["q2", "q1"])) != compute_cache_key(_parts())


def test_cache_key_changes_with_prompt_policy_mode_and_horizon() -> None:
    base = compute_cache_key(_parts())
    assert compute_cache_key(_parts(prompt_version="labeling-2")) != base
    assert compute_cache_key(_parts(policy_version="context-2")) != base
    assert compute_cache_key(_parts(schema_version="1.1")) != base
    assert compute_cache_key(_parts(acceptance_policy_version="acceptance-2")) != base
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


def test_runtime_speaker_state_changes_cache_key() -> None:
    fragment = SimpleNamespace(
        fragment_id="q1",
        kind=BudgetItemKind.TARGET_QUOTE,
        start_cp=10,
        end_cp=15,
        text="「你好。」",
    )
    window = SimpleNamespace(
        target_quote_ids=("q1",),
        fragments=(fragment,),
        fragment_ids=("q1",),
        policy_version="context-1",
        dependency_hash="dep",
        visible_horizon_cp=None,
        scene_ref="scene_current",
    )
    job = SimpleNamespace(range_json='{"reading_mode":"initial"}')
    version = SimpleNamespace(id="v1")
    snapshot = {
        "model": "m",
        "protocol": "chat-completions-compatible",
        "base_url": "https://one.example/v1",
        "params": {},
    }
    state_a = SceneState(
        participants=[SpeakerSlot("S1", "q0", group_id="g1", description="叙述者")]
    )
    state_b = SceneState(
        participants=[SpeakerSlot("S1", "q0", group_id="g1", description="父亲")]
    )
    key_a = _cache_key_for(
        job=job, version=version, window=window, snapshot=snapshot, state=state_a
    )
    key_b = _cache_key_for(
        job=job, version=version, window=window, snapshot=snapshot, state=state_b
    )
    assert key_a != key_b
    assert _cache_key_for(
        job=job,
        version=version,
        window=window,
        snapshot={**snapshot, "base_url": "https://two.example/v1"},
        state=state_a,
    ) != key_a


def test_window_short_refs_are_restored_to_stable_ids() -> None:
    fragments = (
        SimpleNamespace(
            fragment_id="quote-stable-id",
            kind=BudgetItemKind.TARGET_QUOTE,
            start_cp=10,
            end_cp=15,
            text="「你好。」",
        ),
        SimpleNamespace(
            fragment_id="gap-stable-id",
            kind=BudgetItemKind.INNER_GAP,
            start_cp=15,
            end_cp=20,
            text="他抬起头。",
        ),
    )
    window = SimpleNamespace(
        target_quote_ids=("quote-stable-id",),
        fragments=fragments,
        fragment_ids=("quote-stable-id", "gap-stable-id"),
    )

    messages = _messages_for(window=window, state=SceneState(), locked_summary=None)
    prompt = messages[-1]["content"]
    assert '"ref":"Q1"' in prompt
    assert '"ref":"G1"' in prompt
    assert "quote-stable-id" not in prompt
    assert "gap-stable-id" not in prompt

    restored = _restore_output_references(
        {
            "labels": [{"quote_id": "Q1", "evidence_refs": ["G1"]}],
            "gap_decisions": [{"gap_id": "G1", "evidence_refs": ["Q1"]}],
            "needs_context": ["Q1"],
        },
        window,
    )
    assert restored["labels"][0] == {
        "quote_id": "quote-stable-id",
        "evidence_refs": ["gap-stable-id"],
    }
    assert restored["gap_decisions"][0] == {
        "gap_id": "gap-stable-id",
        "evidence_refs": ["quote-stable-id"],
    }
    assert restored["needs_context"] == ["quote-stable-id"]
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
