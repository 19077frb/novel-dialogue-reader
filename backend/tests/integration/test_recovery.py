"""T14 集成测试：进程重启恢复、未知结果、预算到顶、限流退避与缺凭据（F15 / F20）。

三条门槛都在这里被验证：

1. 任务失败/中断不会让原文不可读；
2. 未知付费结果**不自动重发**（只有显式 reconcile 才会回到队列）；
3. 每种非完成状态都有可理解的恢复动作（`GET /api/jobs/{id}/recovery`）。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from fixtures.corrections import import_sample, session_scope
from ndr.config import Settings
from ndr.domain.enums import InferenceRunState, JobState
from ndr.jobs.scheduler import ensure_windows, run_job
from ndr.recovery.service import recover_on_startup
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.models import InferenceRun, Job, JobWindow
from ndr.storage.transactions import transaction


@pytest.fixture()
def factory(migrated_settings: Settings):
    """隔离数据库上的 session factory（测试结束后释放引擎）。"""

    engine = create_db_engine(migrated_settings)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


def _create_profile(client: TestClient, *, name: str, credential_mode: str = "none") -> str:
    response = client.post(
        "/api/model-profiles",
        json={
            "name": name,
            "protocol": "fake-provider",
            "base_url": "http://127.0.0.1:1",
            "model": "fake-model",
            "credential_mode": credential_mode,
        },
    )
    assert response.status_code in {200, 201}, response.text
    return response.json()["data"]["id"]


def _create_job(
    client: TestClient,
    *,
    book_id: str,
    profile_id: str | None,
    key: str,
    budget: dict | None = None,
    start_cp: int = 0,
    end_cp: int | None = None,
) -> dict:
    payload: dict = {
        "book_id": book_id,
        "mode": "process",
        "range": {"start_cp": start_cp} | ({"end_cp": end_cp} if end_cp is not None else {}),
        "idempotency_key": key,
        "run_now": False,
        "budget": budget or {"max_input_tokens": 200_000},
    }
    if profile_id:
        payload["profile_id"] = profile_id
    response = client.post("/api/jobs", json=payload)
    assert response.status_code == 202, response.text
    return response.json()["data"]


def _crash_after_dispatch(settings: Settings, job_id: str) -> str:
    """模拟「请求已发出、结果还没落库时进程被杀」：留下 RUNNING 任务 + DISPATCHED 尝试。"""

    from ndr.storage.models import BookVersion

    with session_scope(settings) as factory, transaction(factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        version = session.get(BookVersion, job.book_version_id)
        assert version is not None
        ensure_windows(session, settings, job, version)
        window = (
            session.execute(
                select(JobWindow)
                .where(JobWindow.job_id == job_id)
                .order_by(JobWindow.window_id)
            )
            .scalars()
            .first()
        )
        assert window is not None
        window.state = JobState.QUEUED
        run = InferenceRun(
            job_id=job_id,
            window_id=window.window_id,
            profile_snapshot_json=job.profile_snapshot_json,
            request_fingerprint=window.dependency_hash,
            state=InferenceRunState.DISPATCHED,
        )
        session.add(run)
        job.state = JobState.RUNNING
        session.flush()
        run_id = run.id
    return run_id


def _crash_between_windows(settings: Settings, job_id: str, *, completed: int = 1) -> None:
    """模拟「窗口之间进程被杀」：任务 RUNNING、前面的窗口已完成、没有未落库的尝试。"""

    from ndr.storage.models import BookVersion

    with session_scope(settings) as factory, transaction(factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        version = session.get(BookVersion, job.book_version_id)
        assert version is not None
        ensure_windows(session, settings, job, version)
        windows = list(
            session.execute(
                select(JobWindow)
                .where(JobWindow.job_id == job_id)
                .order_by(JobWindow.window_id)
            ).scalars()
        )
        for window in windows[:completed]:
            window.state = JobState.COMPLETED
            session.add(
                InferenceRun(
                    job_id=job_id,
                    window_id=window.window_id,
                    profile_snapshot_json=job.profile_snapshot_json,
                    request_fingerprint=window.dependency_hash,
                    state=InferenceRunState.SUCCEEDED,
                    usage_json=json.dumps(
                        {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
                    ),
                    elapsed_ms=5,
                )
            )
        job.state = JobState.RUNNING
        session.flush()


def _job_row(settings: Settings, job_id: str) -> dict:
    with session_scope(settings) as factory, transaction(factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        windows = list(
            session.execute(select(JobWindow).where(JobWindow.job_id == job_id)).scalars()
        )
        runs = list(
            session.execute(
                select(InferenceRun)
                .where(InferenceRun.job_id == job_id)
                .order_by(InferenceRun.created_at, InferenceRun.id)
            ).scalars()
        )
        return {
            "state": job.state.value,
            "last_error": job.last_error,
            "progress": json.loads(job.progress_json) if job.progress_json else {},
            "windows": {row.window_id: row.state.value for row in windows},
            "runs": [
                {
                    "state": run.state.value,
                    "usage": run.usage_json,
                    "error_code": run.error_code,
                }
                for run in runs
            ],
        }


def _content_readable(client: TestClient, book_id: str) -> bool:
    response = client.get(f"/api/books/{book_id}/content", params={"limit": 20})
    return response.status_code == 200 and bool(response.json()["data"]["nodes"])

def test_restart_marks_unknown_then_requires_explicit_retry(
    fake_provider_client: TestClient, migrated_settings: Settings, factory
) -> None:
    """F15：重启后未知结果不自动重发；显式 reconcile 才能回到队列。"""

    app_settings = fake_provider_client.app.state.settings
    data = import_sample(fake_provider_client)
    book_id = data["book_id"]
    profile_id = _create_profile(fake_provider_client, name="T14 恢复提供方")
    job = _create_job(
        fake_provider_client, book_id=book_id, profile_id=profile_id, key="k-restart"
    )
    run_id = _crash_after_dispatch(app_settings, job["id"])

    # 租约未过期：不能把可能还在进行的请求判成未知
    recover_on_startup(factory, lease_seconds=3600)
    state = _job_row(app_settings, job["id"])
    assert state["runs"][0]["state"] == "DISPATCHED"

    summary = recover_on_startup(factory, lease_seconds=0)
    assert run_id in summary.stale_runs
    state = _job_row(app_settings, job["id"])
    assert state["state"] == "NEEDS_RECONCILIATION"
    assert state["runs"][0]["state"] == "UNKNOWN_OUTCOME"
    assert state["runs"][0]["usage"] is None  # 未知用量不写 0

    # 恢复动作可解释：保留未知（免费）或确认重发（付费）
    recovery = fake_provider_client.get(f"/api/jobs/{job['id']}/recovery").json()["data"]
    actions = {item["action"]: item for item in recovery["actions"]}
    assert actions["reconcile_keep"]["paid"] is False
    assert actions["reconcile_retry"]["paid"] is True
    assert recovery["unknown_runs"] >= 1

    # 原文始终可读（任务失败不影响阅读）
    assert _content_readable(fake_provider_client, book_id)

    # 不自动重发：直接再跑一次任务，不会发出任何新调用
    outcome = run_job(
        factory,
        app_settings,
        job_id=job["id"],
        credentials=fake_provider_client.app.state.credentials,
    )
    assert outcome.state is JobState.NEEDS_RECONCILIATION
    assert len(_job_row(app_settings, job["id"])["runs"]) == 1

    # 显式重发 → 窗口回到队列 → 再跑成功
    reconciled = fake_provider_client.post(
        f"/api/jobs/{job['id']}/reconcile", json={"action": "retry"}
    )
    assert reconciled.status_code == 200, reconciled.text
    assert _job_row(app_settings, job["id"])["state"] == "QUEUED"

    outcome = run_job(
        factory,
        app_settings,
        job_id=job["id"],
        credentials=fake_provider_client.app.state.credentials,
    )
    assert outcome.state is JobState.COMPLETED, outcome.as_dict()
    assert _content_readable(fake_provider_client, book_id)


def test_startup_keeps_completed_windows_and_labels_interrupted_work(
    fake_provider_client: TestClient, migrated_settings: Settings, factory
) -> None:
    """RUNNING → PARTIAL、PAUSING → PAUSED；已完成窗口保持有效。"""

    data = import_sample(fake_provider_client)
    book_id = data["book_id"]
    profile_id = _create_profile(fake_provider_client, name="T14 中断提供方")
    app_settings = fake_provider_client.app.state.settings
    job = _create_job(
        fake_provider_client, book_id=book_id, profile_id=profile_id, key="k-interrupted"
    )
    _crash_between_windows(app_settings, job["id"], completed=1)

    summary = recover_on_startup(factory, lease_seconds=0)
    assert job["id"] in summary.interrupted_jobs
    state = _job_row(app_settings, job["id"])
    assert state["state"] == "PARTIAL"
    assert "进程重启" in (state["last_error"] or "")
    assert JobState.COMPLETED.value in state["windows"].values()  # 已完成窗口保留

    recovery = fake_provider_client.get(f"/api/jobs/{job['id']}/recovery").json()["data"]
    actions = {item["action"] for item in recovery["actions"]}
    assert "resume" in actions
    assert recovery["windows_done"] >= 1


def test_budget_exhausted_points_at_explicit_recompute(
    fake_provider_client: TestClient, migrated_settings: Settings, factory
) -> None:
    """F20：预算到顶不发调用；恢复动作是「用新预算重新处理」（默认不自动付费重算）。"""

    app_settings = fake_provider_client.app.state.settings
    data = import_sample(fake_provider_client)
    book_id = data["book_id"]
    profile_id = _create_profile(fake_provider_client, name="T14 预算提供方")
    job = _create_job(
        fake_provider_client,
        book_id=book_id,
        profile_id=profile_id,
        key="k-budget",
        budget={"max_input_tokens": 1, "max_rechecks": 0},
    )

    outcome = run_job(
        factory,
        app_settings,
        job_id=job["id"],
        credentials=fake_provider_client.app.state.credentials,
    )
    assert outcome.state is JobState.BUDGET_EXHAUSTED
    state = _job_row(app_settings, job["id"])
    assert state["runs"] == []  # 到顶后不再发调用

    recovery = fake_provider_client.get(f"/api/jobs/{job['id']}/recovery").json()["data"]
    assert recovery["state"] == "BUDGET_EXHAUSTED"
    actions = {item["action"]: item for item in recovery["actions"]}
    assert "new_job" in actions
    assert actions["new_job"]["paid"] is True
    assert "预算" in recovery["summary"]

    # 用更大的预算重新建任务（显式入口）→ 能完整跑完
    retried = _create_job(
        fake_provider_client,
        book_id=book_id,
        profile_id=profile_id,
        key="k-budget-2",
        budget={"max_input_tokens": 200_000},
    )
    outcome = run_job(
        factory,
        app_settings,
        job_id=retried["id"],
        credentials=fake_provider_client.app.state.credentials,
    )
    assert outcome.state is JobState.COMPLETED
    assert _content_readable(fake_provider_client, book_id)


def test_rate_limit_retries_are_bounded(
    fake_provider_client: TestClient, migrated_settings: Settings, factory
) -> None:
    """限流按有上限的退避重试：成功则继续；用尽上限则停下并给出恢复动作。"""

    from ndr.llm.adapters.fake import FakeProviderAdapter

    app_settings = fake_provider_client.app.state.settings
    app_settings.rate_limit_backoff_base_seconds = 0  # 测试不真等
    data = import_sample(fake_provider_client)
    book_id = data["book_id"]
    profile_id = _create_profile(fake_provider_client, name="T14 限流提供方")

    job = _create_job(
        fake_provider_client, book_id=book_id, profile_id=profile_id, key="k-rate-limit"
    )
    outcome = run_job(
        factory,
        app_settings,
        job_id=job["id"],
        adapter_factory=lambda *_: FakeProviderAdapter(
            labeling_mode="deterministic", script_mode="rate_limited_once"
        ),
    )
    assert outcome.state is JobState.COMPLETED, outcome.as_dict()
    state = _job_row(app_settings, job["id"])
    assert [run["state"] for run in state["runs"]] == ["FAILED", "SUCCEEDED"]
    assert state["runs"][0]["error_code"] == "RATE_LIMITED"

    # 上限设为 0：不重试，任务失败但给出可理解的恢复动作
    # （换一个模型名 → 缓存键不同，确保真的会调用适配器）
    app_settings.rate_limit_max_retries = 0
    strict_profile = _create_profile(
        fake_provider_client, name="T14 限流提供方（不重试）"
    )
    fake_provider_client.patch(
        f"/api/model-profiles/{strict_profile}", json={"model": "fake-model-strict"}
    )
    strict = _create_job(
        fake_provider_client,
        book_id=book_id,
        profile_id=strict_profile,
        key="k-rate-limit-2",
    )
    outcome = run_job(
        factory,
        app_settings,
        job_id=strict["id"],
        adapter_factory=lambda *_: FakeProviderAdapter(
            labeling_mode="deterministic", script_mode="rate_limited_once"
        ),
    )
    assert outcome.state is JobState.FAILED
    recovery = fake_provider_client.get(f"/api/jobs/{strict['id']}/recovery").json()["data"]
    actions = {item["action"]: item for item in recovery["actions"]}
    assert "run" in actions and actions["run"]["paid"] is True
    assert _content_readable(fake_provider_client, book_id)


def test_provider_timeout_is_unknown_outcome_not_auto_resent(
    fake_provider_client: TestClient, migrated_settings: Settings, factory
) -> None:
    """超时 = 结果未知：不自动重发，记录未知用量并提供「保留未知 / 确认重发」。"""

    from ndr.llm.adapters.fake import FakeProviderAdapter

    app_settings = fake_provider_client.app.state.settings
    data = import_sample(fake_provider_client)
    book_id = data["book_id"]
    profile_id = _create_profile(fake_provider_client, name="T14 超时提供方")
    job = _create_job(
        fake_provider_client, book_id=book_id, profile_id=profile_id, key="k-timeout"
    )

    outcome = run_job(
        factory,
        app_settings,
        job_id=job["id"],
        adapter_factory=lambda *_: FakeProviderAdapter(script_mode="timeout_once"),
    )
    assert outcome.state is JobState.NEEDS_RECONCILIATION
    state = _job_row(app_settings, job["id"])
    assert state["runs"][-1]["state"] == "UNKNOWN_OUTCOME"
    assert state["runs"][-1]["usage"] is None  # 未知用量不写 0

    recovery = fake_provider_client.get(f"/api/jobs/{job['id']}/recovery").json()["data"]
    actions = {item["action"]: item for item in recovery["actions"]}
    assert set(actions) == {"reconcile_keep", "reconcile_retry"}
    assert recovery["unknown_runs"] >= 1
    assert "不会" in recovery["summary"]

    kept = fake_provider_client.post(
        f"/api/jobs/{job['id']}/reconcile", json={"action": "keep_unknown"}
    )
    assert kept.status_code == 200, kept.text
    assert _job_row(app_settings, job["id"])["state"] == "PARTIAL"


def test_missing_credential_is_explainable_and_recoverable(
    fake_provider_client: TestClient, migrated_settings: Settings, factory
) -> None:
    """缺 Key：任务落到可解释的失败状态（不是卡在 RUNNING），并提示先去补密钥。"""

    app_settings = fake_provider_client.app.state.settings
    data = import_sample(fake_provider_client)
    book_id = data["book_id"]
    profile_id = _create_profile(
        fake_provider_client, name="T14 缺 Key 提供方", credential_mode="session"
    )
    job = _create_job(
        fake_provider_client, book_id=book_id, profile_id=profile_id, key="k-no-key"
    )

    outcome = run_job(
        factory,
        app_settings,
        job_id=job["id"],
        credentials=fake_provider_client.app.state.credentials,
    )
    assert outcome.state is JobState.FAILED
    state = _job_row(app_settings, job["id"])
    assert "PROVIDER_AUTH_FAILED" in (state["last_error"] or "")
    assert state["runs"] == []  # 没有发起任何调用

    recovery = fake_provider_client.get(f"/api/jobs/{job['id']}/recovery").json()["data"]
    actions = {item["action"]: item for item in recovery["actions"]}
    assert recovery["requires_credential"] is True
    assert "open_settings" in actions
    assert actions["open_settings"]["paid"] is False
    assert _content_readable(fake_provider_client, book_id)
