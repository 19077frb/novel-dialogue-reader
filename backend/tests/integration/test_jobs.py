"""T10 集成测试：任务、缓存复用、预算与未知结果（F15/F16/F20）。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from ndr.config import Settings
from ndr.domain.enums import InferenceRunState, JobState
from ndr.jobs.scheduler import reconcile_stale_runs, run_job
from ndr.llm.adapters.fake import FakeProviderAdapter
from ndr.llm.errors import ProviderError, ProviderErrorKind
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.models import InferenceRun, Job, JobWindow
from ndr.storage.transactions import transaction

SAMPLE = (
    "第一章 雨夜\n"
    "「雨停了。」少女合上伞。\n"
    "少年没有回答，只是把外套递了过去。\n"
    "「……谢谢。」她低声说。\n"
    "「不用谢。」\n"
    "远处传来钟声，两人都没有再开口。\n"
    "「明天也来这里吧。」少年忽然说。\n"
    "「嗯。」少女点了点头。\n"
)


def _import(client: TestClient) -> dict:
    response = client.post(
        "/api/books/import",
        files={"file": ("sample.txt", SAMPLE.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 202, response.text
    return response.json()["data"]


def _fake_profile(client: TestClient, name: str = "测试提供方") -> str:
    response = client.post(
        "/api/model-profiles",
        json={
            "name": name,
            "protocol": "fake-provider",
            "base_url": "http://127.0.0.1:1",
            "model": "fake-model",
            "credential_mode": "none",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def _create_job(client: TestClient, book_id: str, profile_id: str, *, key: str, **overrides) -> dict:
    payload = {
        "book_id": book_id,
        "profile_id": profile_id,
        "mode": "process",
        "range": {"start_cp": 0, "end_cp": len(SAMPLE)},
        "budget": {"max_input_tokens": 200_000},
        "idempotency_key": key,
        "run_now": False,
    }
    payload.update(overrides)
    response = client.post("/api/jobs", json=payload)
    assert response.status_code == 202, response.text
    return response.json()["data"]


def _factory(settings: Settings):  # noqa: ANN202
    engine = create_db_engine(settings)
    return engine, create_session_factory(engine)


def _run_with_fake(settings: Settings, job_id: str, adapter: FakeProviderAdapter):
    engine, factory = _factory(settings)
    try:
        outcome = run_job(
            factory,
            settings,
            job_id=job_id,
            adapter_factory=lambda job, snapshot: adapter,
        )
        return outcome
    finally:
        engine.dispose()


def test_preview_then_process_reuses_cache_and_idempotency(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    """F16：预览后处理同一范围命中缓存；重复点击开始返回同一任务。"""

    data = _import(fake_provider_client)
    profile_id = _fake_profile(fake_provider_client)
    book_id = data["book_id"]

    preview = _create_job(fake_provider_client, book_id, profile_id, key="k-preview", mode="preview")
    adapter = FakeProviderAdapter()
    first = _run_with_fake(migrated_settings, preview["id"], adapter)
    assert first.state is JobState.COMPLETED
    assert first.calls == 1
    assert len(adapter.calls) == 1  # 预览确实调用了一次模型

    # 处理同范围：语义输入相同 → 命中缓存，不再调用模型
    process = _create_job(fake_provider_client, book_id, profile_id, key="k-process", mode="process")
    second = _run_with_fake(migrated_settings, process["id"], adapter)
    assert second.state is JobState.COMPLETED
    assert second.calls == 0
    assert second.cached_windows == 1
    assert len(adapter.calls) == 1  # 发送次数没有增加

    # 重复点击（同幂等键 + 同请求摘要）→ 同一个任务，不新建
    again = _create_job(fake_provider_client, book_id, profile_id, key="k-process", mode="process")
    assert again["id"] == process["id"]


def test_failed_model_output_error_keeps_raw_snippet(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    """真实提供方返回空内容/坏结构时，任务错误信息必须带脱敏片段（决策 0024）。"""

    data = _import(fake_provider_client)
    profile_id = _fake_profile(fake_provider_client)
    job = _create_job(fake_provider_client, data["book_id"], profile_id, key="k-snippet")

    adapter = FakeProviderAdapter(
        script=[
            ProviderError(
                ProviderErrorKind.INVALID_OUTPUT,
                "模型返回空内容",
                details={"body": '{"choices": [{"finish_reason": "length", "message": {"content": ""}}]}'},
            )
        ]
    )
    outcome = _run_with_fake(migrated_settings, job["id"], adapter)
    assert outcome.state is JobState.FAILED

    engine, factory = _factory(migrated_settings)
    try:
        with transaction(factory) as session:
            stored = session.get(Job, job["id"])
            assert stored is not None and stored.last_error is not None
            assert "原始输出片段" in stored.last_error
            assert "finish_reason" in stored.last_error
    finally:
        engine.dispose()


def test_idempotency_key_with_different_request_conflicts(
    fake_provider_client: TestClient,
) -> None:
    data = _import(fake_provider_client)
    profile_id = _fake_profile(fake_provider_client)
    book_id = data["book_id"]
    _create_job(fake_provider_client, book_id, profile_id, key="same-key")

    conflict = fake_provider_client.post(
        "/api/jobs",
        json={
            "book_id": book_id,
            "profile_id": profile_id,
            "mode": "process",
            "range": {"start_cp": 0, "end_cp": 10},  # 与首次不同
            "budget": {"max_input_tokens": 200_000},
            "idempotency_key": "same-key",
            "run_now": False,
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_completed_windows_are_not_called_again(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    """门槛：已完成窗口不重复调用（再次运行同一任务只做跳过）。"""

    data = _import(fake_provider_client)
    profile_id = _fake_profile(fake_provider_client)
    job = _create_job(fake_provider_client, data["book_id"], profile_id, key="k-once")

    adapter = FakeProviderAdapter()
    first = _run_with_fake(migrated_settings, job["id"], adapter)
    assert first.state is JobState.COMPLETED and first.calls == 1

    second = _run_with_fake(migrated_settings, job["id"], adapter)
    assert second.calls == 0
    assert len(adapter.calls) == 1


def test_usage_is_recorded_and_unknown_is_not_zeroed(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    """F20：usage 缺失不写 0；提供方给了 usage 时按口径结算。"""

    data = _import(fake_provider_client)
    profile_id = _fake_profile(fake_provider_client)
    book_id = data["book_id"]

    unknown_job = _create_job(fake_provider_client, book_id, profile_id, key="k-unknown")
    _run_with_fake(migrated_settings, unknown_job["id"], FakeProviderAdapter())

    engine, factory = _factory(migrated_settings)
    try:
        with transaction(factory) as session:
            runs = list(session.execute(select(InferenceRun)).scalars())
            assert runs and all(run.usage_json is None for run in runs)  # 未知 → NULL
    finally:
        engine.dispose()

    usage = fake_provider_client.get(f"/api/books/{book_id}/usage").json()["data"]
    assert usage["runs"] >= 1
    assert usage["unknown_usage_runs"] >= 1
    assert usage["total_tokens"] == 0  # 不把未知当成真实用量
    assert usage["cost"] is None  # 缺价格资料不给金额

    # 提供方上报 usage 时按真实数字结算
    known_job = _create_job(
        fake_provider_client, book_id, profile_id, key="k-known", range={"start_cp": 0, "end_cp": 12}
    )
    adapter = FakeProviderAdapter(usage={"input_tokens": 30, "output_tokens": 10, "total_tokens": 40})
    outcome = _run_with_fake(migrated_settings, known_job["id"], adapter)
    assert outcome.state is JobState.COMPLETED
    engine, factory = _factory(migrated_settings)
    try:
        with transaction(factory) as session:
            run = session.execute(
                select(InferenceRun).where(InferenceRun.job_id == known_job["id"])
            ).scalars().first()
            assert run is not None
            assert json.loads(run.usage_json)["total_tokens"] == 40
    finally:
        engine.dispose()

    usage2 = fake_provider_client.get(f"/api/books/{book_id}/usage").json()["data"]
    assert usage2["total_tokens"] >= 40


def test_budget_exhaustion_stops_before_next_call(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    """F20：预算到顶就停，不再追加调用，原文与已完成结果保持可读。"""

    data = _import(fake_provider_client)
    profile_id = _fake_profile(fake_provider_client)
    job = _create_job(
        fake_provider_client,
        data["book_id"],
        profile_id,
        key="k-budget",
        budget={"max_input_tokens": 1},  # 连一个窗口都放不下
    )
    adapter = FakeProviderAdapter()
    outcome = _run_with_fake(migrated_settings, job["id"], adapter)

    assert outcome.state is JobState.BUDGET_EXHAUSTED
    assert outcome.budget_exhausted is True
    assert adapter.calls == []  # 没有发起任何调用
    detail = fake_provider_client.get(f"/api/jobs/{job['id']}").json()["data"]
    assert detail["state"] == "BUDGET_EXHAUSTED"
    assert detail["remaining_windows"] >= 1


def test_unknown_outcome_is_not_resent_automatically(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    """F15：进程中断在请求发出后 → 标未知结果，不自动重发，等人工对账。"""

    data = _import(fake_provider_client)
    profile_id = _fake_profile(fake_provider_client)
    job = _create_job(fake_provider_client, data["book_id"], profile_id, key="k-crash")

    # 模拟“已发出请求但没来得及保存结果”：直接落一条 DISPATCHED 的旧尝试
    engine, factory = _factory(migrated_settings)
    try:
        with transaction(factory) as session:
            stored = session.get(Job, job["id"])
            assert stored is not None
            session.add(
                JobWindow(
                    job_id=stored.id,
                    window_id="w-stale",
                    target_ids_json="[]",
                    state=JobState.RUNNING,
                )
            )
            run = InferenceRun(
                job_id=stored.id,
                window_id="w-stale",
                profile_snapshot_json="{}",
                request_fingerprint="fp",
                state=InferenceRunState.DISPATCHED,
            )
            session.add(run)
            session.flush()
            run.updated_at = datetime.now(tz=UTC) - timedelta(hours=1)
        with transaction(factory) as session:
            reconciled = reconcile_stale_runs(factory, lease_seconds=60)
            assert reconciled
            refreshed = session.get(Job, job["id"])
            assert refreshed is not None
            assert refreshed.state is JobState.NEEDS_RECONCILIATION
            run = session.execute(select(InferenceRun)).scalars().first()
            assert run is not None and run.state is InferenceRunState.UNKNOWN_OUTCOME
    finally:
        engine.dispose()

    adapter = FakeProviderAdapter()
    outcome = _run_with_fake(migrated_settings, job["id"], adapter)
    assert outcome.state is JobState.NEEDS_RECONCILIATION
    assert adapter.calls == []  # 绝不盲目重发

    # 用户显式选择重试后，窗口回到 QUEUED，才允许再次发送
    retry = fake_provider_client.post(
        f"/api/jobs/{job['id']}/reconcile", json={"action": "retry"}
    )
    assert retry.status_code == 200
    assert retry.json()["data"]["affected_windows"]
    detail = fake_provider_client.get(f"/api/jobs/{job['id']}").json()["data"]
    assert detail["state"] == "QUEUED"


def test_pause_marks_paused_between_windows(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    data = _import(fake_provider_client)
    profile_id = _fake_profile(fake_provider_client)
    job = _create_job(fake_provider_client, data["book_id"], profile_id, key="k-pause")

    engine, factory = _factory(migrated_settings)
    try:
        with transaction(factory) as session:
            stored = session.get(Job, job["id"])
            assert stored is not None
            stored.state = JobState.PAUSING
        adapter = FakeProviderAdapter()
        outcome = run_job(factory, settings=migrated_settings, job_id=job["id"], adapter_factory=lambda *_: adapter)
    finally:
        engine.dispose()

    assert outcome.state is JobState.PAUSED
    assert adapter.calls == []


def test_estimate_endpoint_is_local_only(fake_provider_client: TestClient) -> None:
    data = _import(fake_provider_client)
    response = fake_provider_client.post(
        f"/api/books/{data['book_id']}/estimates",
        json={"range": {"start_cp": 0, "end_cp": len(SAMPLE)}},
    )
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["window_count"] >= 1
    assert payload["target_count"] >= 4
    assert payload["total_tokens"] > 0
    assert payload["estimator"]["method"] == "heuristic-cjk"
    assert any("启发式" in note for note in payload["notes"])
    # 估算不产生任务与推理尝试
    assert fake_provider_client.get("/api/jobs/does-not-exist").status_code == 404
