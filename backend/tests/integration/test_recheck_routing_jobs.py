"""集成测试：context-2 压缩策略 + 有限局部复核 + 强模型路由真正落到调度器。

用显式开启的 FakeProvider（不发任何网络请求）验证：

- 任务范围里的 ``context_policy=context-2`` 真的启用压缩，并且策略版本进入窗口依赖哈希；
- 首次结果未解决（UNKNOWN）时只做**有限**复核，且复核回到保守策略，把压缩丢掉的文句补回；
- 强模型路由受 ``strong_model_share`` 上限约束，并且每次尝试（含复核）都单独记
  ``inference_runs``，用量不是「只统计最后一次调用」。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ndr.config import Settings
from ndr.context.budget import BudgetPolicy
from ndr.domain.enums import InferenceRunState, JobState
from ndr.jobs.scheduler import run_job
from ndr.jobs.service import digest_request, usage_summary
from ndr.llm.adapters.fake import FakeProviderAdapter
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.models import (
    Annotation,
    AnnotationHistory,
    InferenceRun,
    Job,
    JobWindow,
    Quote,
    Scene,
)
from ndr.storage.transactions import transaction

OPENING = "「雨停了。」"
CLOSING = "「……谢谢。」"
HEAD = ["雨点落在窗沿上。", "屋檐还在滴水。"]
MIDDLE = [
    "她把收好的雨伞靠在门边又看了一眼窗外。",
    "远处钟楼的钟声穿过雨后的空气慢慢传来。",
    "路灯把路边水洼照得发亮而街上很安静。",
    "两个人谁都没有先动只是站着。",
]
CUE = "少女低声说。"
TAIL = [
    "夜风带着凉意吹过走廊又吹动了门帘。",
    "街上的店铺都已经关了门。",
    "远处传来自行车经过的铃声。",
    "她把伞收好放在门边。",
    "两人站在门口没有动。",
]
GAP = "".join([*HEAD, *MIDDLE, CUE, *TAIL])
SAMPLE = f"第一章 雨夜\n{OPENING}{GAP}{CLOSING}\n"
DROPPED_MARKER = "她把收好的雨伞靠在门边"


def _import(client: TestClient, sample: str = SAMPLE) -> dict:
    response = client.post(
        "/api/books/import",
        files={"file": ("live-probe.txt", sample.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 202, response.text
    return response.json()["data"]


def _profile(client: TestClient, name: str = "复核路由提供方", model: str = "fake-model") -> str:
    response = client.post(
        "/api/model-profiles",
        json={
            "name": name,
            "protocol": "fake-provider",
            "base_url": "http://127.0.0.1:1",
            "model": model,
            "credential_mode": "none",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def _create_job(
    client: TestClient,
    *,
    book_id: str,
    profile_id: str,
    key: str,
    range_payload: dict | None = None,
    max_rechecks: int | None = None,
    max_recheck_rounds: int | None = None,
) -> dict:
    payload = {
        "book_id": book_id,
        "profile_id": profile_id,
        "mode": "process",
        "range": range_payload or {"start_cp": 0, "end_cp": len(SAMPLE)},
        "budget": {"max_input_tokens": 200_000},
        "idempotency_key": key,
        "run_now": False,
    }
    if max_rechecks is not None:
        payload["budget"]["max_rechecks"] = max_rechecks
    if max_recheck_rounds is not None:
        payload["budget"]["max_recheck_rounds"] = max_recheck_rounds
    response = client.post("/api/jobs", json=payload)
    assert response.status_code == 202, response.text
    return response.json()["data"]


def _factory(settings: Settings):  # noqa: ANN202
    engine = create_db_engine(settings)
    return engine, create_session_factory(engine)


@pytest.mark.parametrize("rounds", [0, 1, 3])
def test_full_window_review_rounds_cover_all_quotes_and_resume_without_first_call(
    fake_provider_client: TestClient, migrated_settings: Settings, rounds: int,
) -> None:
    data = _import(fake_provider_client)
    job = _create_job(fake_provider_client, book_id=data["book_id"],
                      profile_id=_profile(fake_provider_client), key=f"full-review-{rounds}",
                      max_recheck_rounds=rounds, max_rechecks=20)
    adapter = FakeProviderAdapter()
    outcome = _run(migrated_settings, job["id"], lambda job, snapshot: adapter)
    assert outcome.state is JobState.COMPLETED
    assert outcome.recheck_calls == rounds
    assert outcome.recheck_targets == 2 * rounds
    assert len(adapter.calls) == 1 + rounds
    assert all(len(call["payload"]["target_quote_ids"]) == 2 for call in adapter.calls)
    engine, factory = _factory(migrated_settings)
    try:
        with transaction(factory) as session:
            row = session.get(Job, job["id"])
            checkpoint = json.loads(row.checkpoint_json)
            if rounds:
                assert list(checkpoint["review_rounds"].values()) == [rounds]
            row.state = JobState.QUEUED
            if rounds:
                checkpoint["review_rounds"] = {
                    key: rounds - 1 for key in checkpoint["review_rounds"]}
                row.checkpoint_json = json.dumps(checkpoint)
        resumed = run_job(factory, migrated_settings, job_id=job["id"],
                          adapter_factory=lambda job, snapshot: adapter)
        assert resumed.state is JobState.COMPLETED
        assert len(adapter.calls) == 1 + rounds  # completed round reused from matching cache
    finally:
        engine.dispose()


def test_full_review_checks_accepted_quotes_without_downgrading_them(
    fake_provider_client: TestClient, migrated_settings: Settings,
) -> None:
    class WeakerReviewAdapter(FakeProviderAdapter):
        async def generate_labels(self, payload):
            self.labeling_mode = "deterministic" if not self.calls else "unknown"
            return await super().generate_labels(payload)

    data = _import(fake_provider_client)
    job = _create_job(fake_provider_client, book_id=data["book_id"],
                      profile_id=_profile(fake_provider_client), key="accepted-full-review",
                      max_recheck_rounds=2)
    adapter = WeakerReviewAdapter()
    outcome = _run(migrated_settings, job["id"], lambda job, snapshot: adapter)
    assert outcome.state is JobState.COMPLETED
    assert outcome.recheck_calls == 2
    assert len(adapter.calls) == 3
    engine, factory = _factory(migrated_settings)
    try:
        with factory() as session:
            annotations = list(session.scalars(select(Annotation)))
            assert len(annotations) == 2
            assert all(a.status.value == "ACCEPTED" for a in annotations)
            assert all(a.version == 1 for a in annotations)
    finally:
        engine.dispose()


def test_full_review_is_not_capped_by_quote_count_and_preserves_manual_locks(
    fake_provider_client: TestClient, migrated_settings: Settings,
) -> None:
    sample = "第一章\n「一句。」\n他停了一会。\n「二句。」\n「三句。」\n「四句。」\n「五句。」\n"
    data = _import(fake_provider_client, sample)
    job = _create_job(fake_provider_client, book_id=data["book_id"],
                      profile_id=_profile(fake_provider_client), key="five-quote-review",
                      range_payload={"start_cp": 0, "end_cp": len(sample)},
                      max_recheck_rounds=1)
    engine, factory = _factory(migrated_settings)

    class LockAdapter(FakeProviderAdapter):
        async def generate_labels(self, payload):
            if self.calls:
                with transaction(factory) as session:
                    first = session.scalar(select(Annotation).where(
                        Annotation.quote_id == payload["target_quote_ids"][0]))
                    first.user_locked = True
                self.labeling_mode = "deterministic"
            return await super().generate_labels(payload)

    adapter = LockAdapter()
    try:
        outcome = run_job(factory, migrated_settings, job_id=job["id"],
                          adapter_factory=lambda job, snapshot: adapter)
        assert outcome.state is JobState.COMPLETED
        assert outcome.recheck_targets == 5
        assert len(adapter.calls) == 2
        assert all(len(call["payload"]["target_quote_ids"]) == 5 for call in adapter.calls)
        with factory() as session:
            locked = session.scalar(select(Annotation).where(Annotation.user_locked.is_(True)))
            assert locked.status.value == "UNKNOWN"
            assert locked.version == 1
    finally:
        engine.dispose()


def test_estimate_includes_all_review_rounds(fake_provider_client: TestClient) -> None:
    data = _import(fake_provider_client)
    payload = {"range": {"start_cp": 0, "end_cp": len(SAMPLE)},
               "budget": {"max_recheck_rounds": 0}}
    route = f"/api/books/{data['book_id']}/estimates"
    base = fake_provider_client.post(route, json=payload).json()["data"]
    payload["budget"]["max_recheck_rounds"] = 2
    revised = fake_provider_client.post(route, json=payload).json()["data"]
    assert revised["total_tokens"] == 3 * base["total_tokens"]
    assert revised["windows"][0]["estimated_tokens"] == 3 * base["windows"][0]["estimated_tokens"]
    assert revised["windows"][0]["window_id"] == base["windows"][0]["window_id"]


def test_legacy_job_request_digest_survives_nullable_rounds_field(
    fake_provider_client: TestClient, migrated_settings: Settings,
) -> None:
    data = _import(fake_provider_client)
    profile_id = _profile(fake_provider_client)
    job = _create_job(fake_provider_client, book_id=data["book_id"],
                      profile_id=profile_id, key="legacy-review-digest", max_rechecks=2)
    engine, factory = _factory(migrated_settings)
    try:
        with transaction(factory) as session:
            row = session.get(Job, job["id"])
            assert "max_recheck_rounds" not in json.loads(row.budget_json)
            row.request_digest = digest_request({
                "kind": "INFERENCE", "book_id": data["book_id"],
                "book_version_id": row.book_version_id, "profile_id": profile_id,
                "range": {"start_cp": 0, "end_cp": len(SAMPLE),
                          "selected_window_ids": None, "force_reprocess": False},
                "budget": {"max_input_tokens": 200_000, "max_output_tokens": None,
                           "max_rechecks": 2, "max_format_retries": 1},
                "reading_mode": "initial", "visible_horizon_cp": None,
                "inference_options": {},
            })
        restored = _create_job(fake_provider_client, book_id=data["book_id"],
                               profile_id=profile_id, key="legacy-review-digest", max_rechecks=2)
        assert restored["id"] == job["id"]
    finally:
        engine.dispose()


def test_full_review_keeps_existing_scenes_and_covers_each_scene(
    fake_provider_client: TestClient, migrated_settings: Settings,
) -> None:
    data = _import(fake_provider_client)
    job = _create_job(fake_provider_client, book_id=data["book_id"],
                      profile_id=_profile(fake_provider_client), key="scene-frozen-review",
                      max_recheck_rounds=0)
    adapter = FakeProviderAdapter()
    _run(migrated_settings, job["id"], lambda job, snapshot: adapter)
    engine, factory = _factory(migrated_settings)
    try:
        with transaction(factory) as session:
            quotes = list(session.scalars(select(Quote).order_by(Quote.start_cp)))
            second = session.scalar(select(Annotation).where(Annotation.quote_id == quotes[1].id))
            scene = Scene(book_version_id=quotes[1].book_version_id, start_cp=quotes[1].start_cp)
            session.add(scene)
            session.flush()
            second.scene_id = scene.id
            scene_ids = {a.quote_id: a.scene_id for a in session.scalars(select(Annotation))}
            row = session.get(Job, job["id"])
            budget = json.loads(row.budget_json)
            budget["max_recheck_rounds"] = 1
            row.budget_json = json.dumps(budget)
            row.state = JobState.QUEUED
        resumed = run_job(factory, migrated_settings, job_id=job["id"],
                          adapter_factory=lambda job, snapshot: adapter)
        assert resumed.state is JobState.COMPLETED
        assert resumed.recheck_targets == 2
        assert resumed.recheck_calls == 2
        assert len(adapter.calls) == 3
        with factory() as session:
            assert len(list(session.scalars(select(Scene)))) == 2
            assert {a.quote_id: a.scene_id for a in session.scalars(select(Annotation))} == scene_ids
    finally:
        engine.dispose()


@pytest.mark.parametrize("pause", [False, True])
def test_full_review_budget_and_pause_keep_results_and_resume_remaining_rounds(
    fake_provider_client: TestClient, migrated_settings: Settings, pause: bool,
) -> None:
    data = _import(fake_provider_client)
    job = _create_job(fake_provider_client, book_id=data["book_id"],
                      profile_id=_profile(fake_provider_client), key=f"review-stop-{pause}",
                      max_recheck_rounds=2)
    engine, factory = _factory(migrated_settings)

    class StopAdapter(FakeProviderAdapter):
        async def generate_labels(self, payload):
            raw = await super().generate_labels(payload)
            if len(self.calls) == (2 if pause else 1):
                with transaction(factory) as session:
                    row = session.get(Job, job["id"])
                    if pause:
                        row.state = JobState.PAUSING
                    else:
                        budget = json.loads(row.budget_json)
                        budget["max_input_tokens"] = 1
                        row.budget_json = json.dumps(budget)
            return raw

    adapter = StopAdapter(usage={"input_tokens": 10, "output_tokens": 10,
                                "total_tokens": 20, "unknown": False})
    try:
        outcome = run_job(factory, migrated_settings, job_id=job["id"],
                          adapter_factory=lambda job, snapshot: adapter)
        assert outcome.state is (JobState.PAUSED if pause else JobState.COMPLETED)
        assert len(adapter.calls) == (2 if pause else 1)
        with factory() as session:
            assert len(list(session.scalars(select(Annotation)))) == 2
            if not pause:
                checkpoint = json.loads(session.get(Job, job["id"]).checkpoint_json)
                assert list(checkpoint["review_rounds"].values()) == [0]
                assert "额度不足" in next(iter(checkpoint["review_stopped"].values()))
        if pause:
            with transaction(factory) as session:
                row = session.get(Job, job["id"])
                assert list(json.loads(row.checkpoint_json)["review_rounds"].values()) == [1]
                row.state = JobState.QUEUED
            resumed = run_job(factory, migrated_settings, job_id=job["id"],
                              adapter_factory=lambda job, snapshot: adapter)
            assert resumed.state is JobState.COMPLETED
            assert len(adapter.calls) == 3
    finally:
        engine.dispose()


def _run(settings: Settings, job_id: str, factory_fn, policy=None) -> object:  # noqa: ANN001, ANN202
    engine, factory = _factory(settings)
    try:
        return run_job(
            factory,
            settings,
            job_id=job_id,
            adapter_factory=factory_fn,
            policy=policy,
        )
    finally:
        engine.dispose()


def _rows(settings: Settings, job_id: str) -> tuple[list, list, dict]:  # noqa: ANN202
    """读回推理尝试、计划窗口与任务进度（只读，用于证明「每次尝试都留痕」）。"""

    engine, factory = _factory(settings)
    try:
        with transaction(factory) as session:
            runs = list(
                session.execute(
                    select(InferenceRun).where(InferenceRun.job_id == job_id)
                ).scalars()
            )
            windows = list(
                session.execute(select(JobWindow).where(JobWindow.job_id == job_id)).scalars()
            )
            row = session.get(Job, job_id)
            assert row is not None
            progress = json.loads(row.progress_json or "{}")
    finally:
        engine.dispose()
    return runs, windows, progress


def test_job_budget_enables_recheck_without_context_2(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    data = _import(fake_provider_client)
    profile_id = _profile(fake_provider_client)
    job = _create_job(
        fake_provider_client,
        book_id=data["book_id"],
        profile_id=profile_id,
        key="k-budget-recheck",
    )

    engine, factory = _factory(migrated_settings)
    try:
        with transaction(factory) as session:
            row = session.get(Job, job["id"])
            assert row is not None
            row.budget_json = json.dumps(
                {"max_input_tokens": 200_000, "max_rechecks": 2}
            )
        adapter = FakeProviderAdapter()
        outcome = run_job(
            factory,
            migrated_settings,
            job_id=job["id"],
            adapter_factory=lambda job, snapshot: adapter,
        )
    finally:
        engine.dispose()

    assert outcome.state is JobState.COMPLETED
    assert outcome.recheck_windows == 1
    assert outcome.recheck_targets == 2
    assert outcome.recheck_calls == 1
    assert len(adapter.calls) == 2


@pytest.mark.parametrize("limit", [0, 1])
def test_explicit_recheck_limit_overrides_context_policy_default(
    fake_provider_client: TestClient, migrated_settings: Settings, limit: int,
) -> None:
    data = _import(fake_provider_client)
    job = _create_job(fake_provider_client, book_id=data["book_id"],
                      profile_id=_profile(fake_provider_client), key=f"limited-recheck-{limit}",
                      range_payload={"start_cp": 0, "end_cp": len(SAMPLE), "context_policy": "context-2"})
    engine, factory = _factory(migrated_settings)
    try:
        with transaction(factory) as session:
            row = session.get(Job, job["id"])
            row.budget_json = json.dumps({"max_input_tokens": 200_000, "max_rechecks": limit})
        adapter = FakeProviderAdapter()
        outcome = run_job(factory, migrated_settings, job_id=job["id"],
                          adapter_factory=lambda job, snapshot: adapter)
        assert outcome.state is JobState.COMPLETED
        assert outcome.recheck_targets == limit
        assert len(adapter.calls) == (2 if limit else 1)
    finally:
        engine.dispose()


def test_invalid_recheck_keeps_first_annotations_and_completed_window(
    fake_provider_client: TestClient, migrated_settings: Settings,
) -> None:
    class InvalidRecheckAdapter(FakeProviderAdapter):
        async def generate_labels(self, payload):
            raw = await super().generate_labels(payload)
            if len(self.calls) > 1:
                raw["labels"][0]["quote_id"] = "not-a-sent-quote"
            return raw

    data = _import(fake_provider_client)
    job = _create_job(fake_provider_client, book_id=data["book_id"],
                      profile_id=_profile(fake_provider_client), key="failed-recheck-keeps-first",
                      max_rechecks=2,
                      range_payload={"start_cp": 0, "end_cp": len(SAMPLE), "context_policy": "context-2"})
    adapter = InvalidRecheckAdapter()
    outcome = _run(migrated_settings, job["id"], lambda job, snapshot: adapter)
    assert outcome.state is JobState.COMPLETED
    assert outcome.recheck_calls == 2
    assert outcome.recheck_windows == 0
    runs, windows, _progress = _rows(migrated_settings, job["id"])
    assert [run.state for run in runs] == [InferenceRunState.SUCCEEDED, InferenceRunState.FAILED,
                                        InferenceRunState.FAILED]
    assert windows[0].state is JobState.COMPLETED
    engine, factory = _factory(migrated_settings)
    try:
        with factory() as session:
            annotations = list(session.execute(select(Annotation)).scalars())
            assert len(annotations) == 2
            history = list(session.execute(select(AnnotationHistory)).scalars())
            assert history == []  # Invalid recheck did not replace or revise either first result.
            assert all(annotation.version == 1 for annotation in annotations)
    finally:
        engine.dispose()


def test_context_2_rechecks_unresolved_targets_and_restores_evidence(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    data = _import(fake_provider_client)
    profile_id = _profile(fake_provider_client)
    job = _create_job(
        fake_provider_client,
        book_id=data["book_id"],
        profile_id=profile_id,
        key="k-recheck",
        max_rechecks=2,
        range_payload={"start_cp": 0, "end_cp": len(SAMPLE), "context_policy": "context-2"},
    )

    adapter = FakeProviderAdapter()
    outcome = _run(migrated_settings, job["id"], lambda job, snapshot: adapter)

    assert outcome.state is JobState.COMPLETED
    assert outcome.strong_windows == 0  # 没有配置强模型：不做路由
    assert outcome.recheck_windows == 1
    assert outcome.recheck_targets == 2  # 两句对白都还没解决
    assert outcome.recheck_calls == 1

    assert len(adapter.calls) == 2
    first = json.dumps(adapter.calls[0]["payload"], ensure_ascii=False)
    second = json.dumps(adapter.calls[1]["payload"], ensure_ascii=False)
    assert CUE in first
    assert DROPPED_MARKER not in first  # 首次（context-2）确实压缩掉纯叙述
    assert DROPPED_MARKER in second  # 复核把丢掉的文句补回来了

    runs, windows, progress = _rows(migrated_settings, job["id"])

    # 两次尝试都单独留痕（首次 + 复核），不是只记最后一次
    assert len(runs) == 2
    assert all(run.state is InferenceRunState.SUCCEEDED for run in runs)
    assert len({run.window_id for run in runs}) == 2
    # 计划窗口只有一个；复核窗口不进 job_windows，不改变「每窗口一次有效提交」
    assert len(windows) == 1
    assert progress["stage"] == "completed"
    assert progress["recheck_windows"] == 1
    assert progress["recheck_targets"] == 2


def test_strong_routing_uses_configured_profile_and_records_it(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    data = _import(
        fake_provider_client,
        SAMPLE.replace("雨停了", "雨停了又停了"),
    )
    base_profile = _profile(fake_provider_client, name="基础模型")
    strong_profile = _profile(
        fake_provider_client, name="强模型", model="strong-model"
    )
    range_payload = {
        "start_cp": 0,
        "end_cp": len(SAMPLE),
        "context_policy": "context-2",
        "strong_profile_id": strong_profile,
    }
    policy = BudgetPolicy(gap_compression=True, strong_model_share=1.0)

    base = FakeProviderAdapter(name="fake-base", model="fake-model")
    strong = FakeProviderAdapter(name="fake-strong", model="strong-model")

    def factory(job, snapshot):  # noqa: ANN001, ANN202
        return strong if (snapshot or {}).get("model") == "strong-model" else base

    job = _create_job(
        fake_provider_client,
        book_id=data["book_id"],
        profile_id=base_profile,
        key="k-strong-route",
        range_payload=range_payload,
    )
    outcome = _run(migrated_settings, job["id"], factory, policy=policy)
    runs, _windows, progress = _rows(migrated_settings, job["id"])
    snapshot_models = {json.loads(run.profile_snapshot_json).get("model") for run in runs}

    engine, session_factory = _factory(migrated_settings)
    try:
        with transaction(session_factory) as session:
            summary = usage_summary(session, data["book_id"])
    finally:
        engine.dispose()

    assert outcome.state is JobState.COMPLETED
    assert outcome.strong_windows == 1
    assert len(strong.calls) == 1
    assert base.calls == []  # 困难窗口确实走了强模型
    assert snapshot_models == {"strong-model"}  # 审计：能看出这次尝试用的哪个模型
    assert set(summary["by_model"]) == {"strong-model"}  # 用量按每次尝试的模型归属
    assert progress["strong_windows"] == 1


def test_strong_share_below_one_window_keeps_base_model(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    data = _import(fake_provider_client, SAMPLE.replace("雨停了", "雨停了又下起来了"))
    base_profile = _profile(fake_provider_client, name="基础模型（封顶）")
    strong_profile = _profile(
        fake_provider_client, name="强模型（封顶）", model="strong-model"
    )
    policy = BudgetPolicy(gap_compression=True, strong_model_share=0.4)  # 1 个窗口 ×0.4 → 上限 0

    base = FakeProviderAdapter(name="fake-base", model="fake-model")
    strong = FakeProviderAdapter(name="fake-strong", model="strong-model")

    def factory(job, snapshot):  # noqa: ANN001, ANN202
        return strong if (snapshot or {}).get("model") == "strong-model" else base

    job = _create_job(
        fake_provider_client,
        book_id=data["book_id"],
        profile_id=base_profile,
        key="k-strong-cap",
        range_payload={
            "start_cp": 0,
            "end_cp": len(SAMPLE),
            "context_policy": "context-2",
            "strong_profile_id": strong_profile,
        },
    )
    outcome = _run(migrated_settings, job["id"], factory, policy=policy)

    assert outcome.state is JobState.COMPLETED
    assert outcome.strong_windows == 0
    assert strong.calls == []
    assert len(base.calls) == 1
