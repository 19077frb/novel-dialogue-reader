"""集成测试：任务、缓存复用、预算与未知结果。"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ndr.config import Settings
from ndr.domain.enums import (
    AnnotationSource,
    AnnotationStatus,
    InferenceRunState,
    JobState,
    QuoteKind,
)
from ndr.jobs.scheduler import reconcile_stale_runs, run_job
from ndr.llm.adapters.fake import FakeProviderAdapter
from ndr.llm.errors import ProviderError, ProviderErrorKind
from ndr.scenes.state import ConfirmedCharacter, SceneState, SpeakerSlot
from ndr.storage.cache import fingerprint
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.models import (
    Annotation,
    BookCharacter,
    BookVersion,
    InferenceRun,
    Job,
    JobWindow,
    Quote,
    Scene,
    SpeakerGroup,
)
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


class ShortJobAdapter(FakeProviderAdapter):
    def __init__(self, *, kind="speech", bad_first=False, known_usage=True, discover=False):
        super().__init__()
        self.kind, self.bad_first = kind, bad_first
        self.known_usage, self.discover = known_usage, discover

    async def generate_labels(self, payload):
        self.calls.append({"kind": "labels", "payload": deepcopy(payload)})
        data = json.loads(payload["messages"][1]["content"])
        assert payload["output_protocol"] == "expression-production-1"
        assert "compiler_task_fingerprint" in payload
        evidence = next(r["ref"] for r in data["context"] if r["text"].strip()
                        and r["ref"] not in data["targets"]
                        and r["ref"] not in data["gap_next_quote"]) if any(
                            r["text"].strip() and r["ref"] not in data["targets"]
                            and r["ref"] not in data["gap_next_quote"] for r in data["context"]
                        ) else data["targets"][0]
        result = {"labels": [{"q": q, "kind": self.kind,
                              "character": "N1" if self.discover else data["candidates"][0]["id"],
                              "basis": "style_only", "evidence": [evidence]}
                             for q in data["targets"]]}
        if self.discover:
            result["new_characters"] = [{"ref": "N1", "name": "路人", "description": "匿名路人",
                                         "evidence": [evidence]}]
        if self.bad_first and len(self.calls) == 1:
            result["labels"] = []
        result["_usage"] = ({"input_tokens": 10, "output_tokens": 20, "total_tokens": 30,
                             "unknown": False} if self.known_usage else {"unknown": True})
        return result


def test_short_protocol_rejects_unknown_version_before_creating_job(
    fake_provider_client, migrated_settings,
):
    data = _import(fake_provider_client)
    profile = _fake_profile(fake_provider_client)
    response = fake_provider_client.post("/api/jobs", json={
        "book_id": data["book_id"], "profile_id": profile, "mode": "process",
        "range": {"output_protocol": "unrecognized"}, "budget": {},
        "idempotency_key": "invalid-short", "run_now": False,
    })
    assert response.status_code == 422, response.text
    engine, factory = _factory(migrated_settings)
    try:
        with factory() as session:
            assert not list(session.scalars(select(Job).where(Job.idempotency_key == "invalid-short")))
    finally:
        engine.dispose()


def test_short_chapter_job_uses_confirmed_roster_without_eager_scene_slots(
    fake_provider_client, migrated_settings,
):
    client = fake_provider_client
    data = _import(client)
    profile = _fake_profile(client)
    chapter = client.get(f"/api/books/{data['book_id']}/chapters").json()["data"][0]
    path = f"/api/books/{data['book_id']}/chapters/{chapter['id']}/character-roster"
    revision = client.get(path).json()["data"]["version"]
    response = client.put(path, json={"candidates": [{"temp_ref": "manual",
        "canonical_name": "少女", "description": "持伞的人物", "aliases": []},
        {"temp_ref": "other", "canonical_name": "少年", "description": "递外套的人物", "aliases": []}],
        "pov_temp_ref": "manual", "expected_version": revision})
    assert response.status_code == 200, response.text
    people_ids = {c["character_id"] for c in response.json()["data"]["candidates"]}
    engine, factory = _factory(migrated_settings)
    try:
        with factory() as session:
            cutoff = session.scalar(select(Quote).order_by(Quote.start_cp)).end_cp
        job = _create_job(client, data["book_id"], profile, key="short-confirmed-chapter",
            range={"chapter_id": chapter["id"], "start_cp": 0, "end_cp": cutoff,
                   "output_protocol": "expression-production-1"}, reading_mode="reread")
        adapter = ShortJobAdapter()
        outcome = _run_with_fake(migrated_settings, job["id"], adapter)
        assert outcome.state is JobState.COMPLETED, outcome.errors
        request = json.loads(adapter.calls[0]["payload"]["messages"][1]["content"])
        assert {c["name"] for c in request["candidates"]} == {"少女", "少年"}
        assert request["pov"] is not None
        with factory() as session:
            groups = list(session.scalars(select(SpeakerGroup)))
            assert len(groups) == 1 and groups[0].character_id in people_ids
    finally:
        engine.dispose()


@pytest.mark.parametrize("stale", [True, False])
def test_short_jobs_reject_stale_identity_bindings_and_review_without_old_answers(
    fake_provider_client, migrated_settings, stale,
):
    data = _import(fake_provider_client)
    profile = _fake_profile(fake_provider_client)
    engine, factory = _factory(migrated_settings)
    try:
        with transaction(factory) as session:
            version = session.scalar(select(BookVersion))
            cutoff = session.scalar(select(Quote).order_by(Quote.start_cp)).end_cp
            person = BookCharacter(book_version_id=version.id, canonical_name="少女",
                                   source="USER", user_confirmed=True, aliases_json="[]")
            session.add(person)
            session.flush()
            person_id = person.id
        job = _create_job(fake_provider_client, data["book_id"], profile, key="short-review",
            range={"start_cp": 0, "end_cp": cutoff, "output_protocol": "expression-production-1"},
            budget={"max_recheck_rounds": 0 if stale else 1, "max_format_retries": 2},
            reading_mode="reread")

        class CallbackAdapter(ShortJobAdapter):
            async def generate_labels(self, payload):
                result = await super().generate_labels(payload)
                if stale:
                    with transaction(factory) as session:
                        session.get(BookCharacter, person_id).canonical_name = "在途改名"
                elif len(self.calls) == 2:
                    sent = json.dumps(payload["messages"], ensure_ascii=False)
                    assert "candidate_hints" not in sent and "旧候选" not in sent
                    assert "独立复核" in sent
                    with transaction(factory) as session:
                        annotation = session.scalar(select(Annotation))
                        annotation.user_locked = True
                        annotation.source = AnnotationSource.USER
                    for label in result["labels"]:
                        label["kind"] = "quotation"
                return result

        adapter = CallbackAdapter(kind="thought")
        result = _run_with_fake(migrated_settings, job["id"], adapter)
        assert result.state is (JobState.FAILED if stale else JobState.COMPLETED), result.errors
        assert len(adapter.calls) == (1 if stale else 2)
        with factory() as session:
            annotations = list(session.scalars(select(Annotation)))
            runs = list(session.scalars(select(InferenceRun).where(InferenceRun.job_id == job["id"])))
            assert len(runs) == len(adapter.calls)
            if stale:
                assert not annotations
                assert runs[0].state is InferenceRunState.FAILED
                assert json.loads(runs[0].usage_json)["total_tokens"] == 30
            else:
                assert annotations[0].kind is QuoteKind.THOUGHT
                assert annotations[0].source is AnnotationSource.USER
    finally:
        engine.dispose()


@pytest.mark.parametrize("kind", ["speech", "thought", "quotation"])
@pytest.mark.parametrize("mode", ["initial", "reread"])
def test_real_jobs_short_protocol_keeps_stable_owner_visibility_and_replay(
    fake_provider_client, migrated_settings, kind, mode,
):
    data = _import(fake_provider_client)
    profile = _fake_profile(fake_provider_client)
    engine, factory = _factory(migrated_settings)
    try:
        with transaction(factory) as session:
            version = session.scalar(select(BookVersion))
            quote = session.scalar(select(Quote).order_by(Quote.start_cp))
            cutoff, quote_id = quote.end_cp, quote.id
            person = BookCharacter(book_version_id=version.id, canonical_name="未来姓名",
                description="未来说明", aliases_json='["未来别名"]', source="USER", user_confirmed=True,
                presentation_history_json=json.dumps([{"cp": len(SAMPLE) - 1,
                    "name": "未来姓名", "description": "未来说明", "identity": "future"}]))
            session.add(person)
            session.flush()
            person_id = person.id
        request_range = {"start_cp": 0, "end_cp": cutoff,
                         "output_protocol": "expression-production-1"}
        job = _create_job(fake_provider_client, data["book_id"], profile, key="short-main",
                          range=request_range, reading_mode=mode, visible_horizon_cp=cutoff)
        adapter = ShortJobAdapter(kind=kind)
        outcome = _run_with_fake(migrated_settings, job["id"], adapter)
        assert outcome.state is JobState.COMPLETED, outcome.errors
        sent = json.dumps(adapter.calls[0]["payload"]["messages"], ensure_ascii=False)
        if mode == "initial":
            assert all(value not in sent for value in ("未来姓名", "未来别名", "未来说明"))
        else:
            assert all(value in sent for value in ("未来姓名", "未来别名", "未来说明"))
        with transaction(factory) as session:
            annotation = session.scalar(select(Annotation).where(Annotation.quote_id == quote_id))
            group = session.get(SpeakerGroup, annotation.speaker_id)
            assert annotation.kind is QuoteKind(kind)
            assert annotation.status is AnnotationStatus.PROVISIONAL
            assert group.character_id == person_id
            assert group.canonical_name == "未来姓名"
            if mode == "initial":
                from ndr.characters.visibility import visible_value
                fallback = {"private_identity": "private", "name": "future", "description": "future"}
                assert visible_value(group.presentation_history_json, cutoff, fallback=fallback)["name"] == ""
                assert visible_value(group.presentation_history_json, len(SAMPLE), fallback=fallback)["name"] == "未来姓名"
            assert session.get(BookCharacter, person_id).canonical_name == "未来姓名"
        replay = _create_job(fake_provider_client, data["book_id"], profile, key="short-replay",
                             range=request_range, reading_mode=mode, visible_horizon_cp=cutoff)
        replay_result = _run_with_fake(migrated_settings, replay["id"], adapter)
        assert replay_result.state is JobState.COMPLETED
        assert replay_result.cached_windows == 1 and replay_result.calls == 0
        assert len(adapter.calls) == 1
        with factory() as session:
            assert all(a.status is AnnotationStatus.PROVISIONAL for a in session.scalars(select(Annotation)))
    finally:
        engine.dispose()


@pytest.mark.parametrize("known_usage", [True, False])
def test_short_jobs_format_retry_is_metered_and_unknown_usage_stops(
    fake_provider_client, migrated_settings, known_usage,
):
    data = _import(fake_provider_client)
    profile = _fake_profile(fake_provider_client)
    engine, factory = _factory(migrated_settings)
    try:
        with transaction(factory) as session:
            cutoff = session.scalar(select(Quote).order_by(Quote.start_cp)).end_cp
        job = _create_job(fake_provider_client, data["book_id"], profile, key="short-bad",
            range={"start_cp": 0, "end_cp": cutoff, "output_protocol": "expression-production-1"},
            budget={"max_format_retries": 1}, reading_mode="reread")
        adapter = ShortJobAdapter(bad_first=True, known_usage=known_usage, discover=True)
        result = _run_with_fake(migrated_settings, job["id"], adapter)
        assert result.state is (JobState.COMPLETED if known_usage else JobState.FAILED), result.errors
        assert len(adapter.calls) == (2 if known_usage else 1)
        with factory() as session:
            runs = list(session.scalars(select(InferenceRun).where(InferenceRun.job_id == job["id"])))
            assert len(runs) == len(adapter.calls)
            assert all((r.usage_json is not None) is known_usage for r in runs)
            from sqlalchemy import inspect

            from ndr.storage.run_archive import decode_archive
            archived_requests = []
            for recorded in runs:
                assert "call_archive" in inspect(recorded).unloaded
                archive = decode_archive(recorded.call_archive)
                archived_requests.append(json.dumps(archive["request"], sort_keys=True))
                assert archive["phase"] == "returned"
                assert "labels" in archive["adapter_result"]
            assert sorted(archived_requests) == sorted(
                json.dumps(call["payload"], sort_keys=True) for call in adapter.calls)
            if known_usage:
                assert sum(json.loads(r.usage_json)["total_tokens"] for r in runs) == 60
                people = list(session.scalars(select(BookCharacter)))
                assert len(people) == 1 and people[0].canonical_name == "路人"
                assert not people[0].user_confirmed
            else:
                assert not list(session.scalars(select(Annotation)))
    finally:
        engine.dispose()


@pytest.mark.parametrize("mode,legacy", [("initial", False), ("reread", False), ("initial", True)])
def test_actual_job_request_projects_restored_identity_and_future_changes_do_not_bust_cache(
    fake_provider_client, migrated_settings, mode, legacy,
):
    data = _import(fake_provider_client)
    profile = _fake_profile(fake_provider_client)
    engine, factory = _factory(migrated_settings)
    try:
        with transaction(factory) as session:
            version = session.scalar(select(BookVersion))
            cutoff = session.scalar(select(Quote).order_by(Quote.start_cp)).end_cp
            facts = [{"kind": kind, "value": value, "visible_from_cp": cp,
                      "canonical_sha256": version.canonical_sha256, "source": "user",
                      "source_ref": "manual", "accepted": True}
                     for kind, value, cp in [("designation", "少女", 0),
                                             ("name", "未来姓名", len(SAMPLE) - 1),
                                             ("alias", "未来别名", len(SAMPLE) - 1),
                                             ("description", "未来说明", len(SAMPLE) - 1),
                                             ("relation", "未来关系", len(SAMPLE) - 1)]]
            person = BookCharacter(book_version_id=version.id, canonical_name="未来姓名",
                                   aliases_json='["未来别名"]', description="未来说明",
                                   source="MODEL", user_confirmed=False,
                                   identity_facts_json=json.dumps(facts, ensure_ascii=False))
            session.add(person)
            session.flush()
            person_id = person.id
            original_state = SceneState(
                confirmed_characters=[ConfirmedCharacter(person.id, "未来姓名", ("未来别名",),
                                                          "未来说明")],
                participants=[SpeakerSlot("S1", "old", character_id=person.id,
                                          canonical_name="未来姓名", description="未来说明")],
                known_characters={"未来姓名": "未来说明"},
                recent_turns=[{"quote_id": "old", "speaker_ref": "S1", "speaker_name": "未来姓名"}],
            ).snapshot()

        def create(key):
            result = _create_job(fake_provider_client, data["book_id"], profile, key=key,
                                 range={"start_cp": 0, "end_cp": cutoff}, reading_mode=mode,
                                 visible_horizon_cp=cutoff)
            with transaction(factory) as session:
                job = session.get(Job, result["id"])
                stored = json.loads(job.range_json)
                assert stored["identity_input_version"] == "identity-input-1"
                if legacy:
                    stored.pop("identity_input_version")
                    job.range_json = json.dumps(stored)
                job.checkpoint_json = json.dumps({"scene_state": original_state}, ensure_ascii=False)
            return result

        job = create("projected-request")
        adapter = FakeProviderAdapter()
        result = _run_with_fake(migrated_settings, job["id"], adapter)
        assert result.state is JobState.COMPLETED, result.errors
        sent = json.dumps(adapter.calls[0]["payload"]["messages"], ensure_ascii=False)
        if mode == "initial" and not legacy:
            assert all(text not in sent for text in ("未来姓名", "未来别名", "未来说明", "未来关系"))
            assert "少女" in sent and person_id in sent
            with transaction(factory) as session:
                person = session.get(BookCharacter, person_id)
                assert person.canonical_name == "未来姓名"
                changed = json.loads(person.identity_facts_json)
                changed[1]["value"] = "另一个未来姓名"
                person.canonical_name = "另一个未来姓名"
                person.identity_facts_json = json.dumps(changed, ensure_ascii=False)
            cached = create("projected-cache")
            cached_result = _run_with_fake(migrated_settings, cached["id"], adapter)
            assert cached_result.cached_windows == 1 and cached_result.calls == 0
            assert len(adapter.calls) == 1
        else:
            assert all(text in sent for text in ("未来姓名", "未来别名", "未来说明"))
            if mode == "reread":
                assert "未来关系" in sent
        again = _create_job(fake_provider_client, data["book_id"], profile, key="projected-request",
                            range={"start_cp": 0, "end_cp": cutoff}, reading_mode=mode,
                            visible_horizon_cp=cutoff)
        assert again["id"] == job["id"]
        assert ("identity_input_version" in again["range"]) is not legacy
    finally:
        engine.dispose()


def test_full_window_review_filters_current_group_names_and_keeps_manual_answer(
    fake_provider_client, migrated_settings,
):
    data = _import(fake_provider_client)
    profile = _fake_profile(fake_provider_client)
    engine, factory = _factory(migrated_settings)
    try:
        with transaction(factory) as session:
            version = session.scalar(select(BookVersion))
            quote = session.scalar(select(Quote).order_by(Quote.start_cp))
            cutoff, quote_id = quote.end_cp, quote.id
            facts = [{"kind": kind, "value": value, "visible_from_cp": cp,
                      "canonical_sha256": version.canonical_sha256, "source": "user",
                      "source_ref": "manual", "accepted": True}
                     for kind, value, cp in [("designation", "少女", 0),
                                             ("name", "未来姓名", len(SAMPLE) - 1)]]
            person = BookCharacter(book_version_id=version.id, canonical_name="未来姓名",
                                   description="未来说明", source="USER", user_confirmed=True,
                                   identity_facts_json=json.dumps(facts, ensure_ascii=False))
            scene = Scene(book_version_id=version.id, start_cp=0)
            session.add_all([person, scene])
            session.flush()
            group = SpeakerGroup(scene_id=scene.id, first_quote_id=quote.id, display_label="S1",
                                 character_id=person.id, canonical_name="未来姓名",
                                 description="未来说明")
            session.add(group)
            session.flush()
            session.add(Annotation(quote_id=quote.id, scene_id=scene.id, speaker_id=group.id,
                                   kind=QuoteKind.SPEECH, source=AnnotationSource.USER,
                                   user_locked=True, status=AnnotationStatus.USER_CONFIRMED))
            group_id = group.id
            checkpoint = SceneState(scene_id=scene.id, participants=[SpeakerSlot(
                "S1", quote.id, group_id=group.id, character_id=person.id,
                canonical_name="未来姓名", description="未来说明")]).snapshot()
        job = _create_job(fake_provider_client, data["book_id"], profile, key="review-visible",
                          range={"start_cp": 0, "end_cp": cutoff}, visible_horizon_cp=cutoff,
                          budget={"max_input_tokens": 200_000, "max_recheck_rounds": 1})
        with transaction(factory) as session:
            session.get(Job, job["id"]).checkpoint_json = json.dumps({"scene_state": checkpoint})
        adapter = FakeProviderAdapter()
        result = _run_with_fake(migrated_settings, job["id"], adapter)
        assert result.state is JobState.COMPLETED, result.errors
        assert len(adapter.calls) == 2
        for call in adapter.calls:
            sent = json.dumps(call["payload"]["messages"], ensure_ascii=False)
            assert "未来姓名" not in sent and "未来说明" not in sent
            assert "少女" in sent
        with transaction(factory) as session:
            answer = session.scalar(select(Annotation).where(Annotation.quote_id == quote_id))
            assert answer.user_locked and answer.status is AnnotationStatus.USER_CONFIRMED
            assert answer.speaker_id == group_id
    finally:
        engine.dispose()


@pytest.mark.parametrize("during_call", [False, True])
def test_invalid_identity_input_stops_actual_job_without_unsafe_retry_or_partial_result(
    fake_provider_client, migrated_settings, during_call,
):
    data = _import(fake_provider_client)
    job = _create_job(fake_provider_client, data["book_id"], _fake_profile(fake_provider_client),
                      key="bad-identity-input")
    engine, factory = _factory(migrated_settings)
    try:
        with transaction(factory) as session:
            person = BookCharacter(book_version_id=data["book_version_id"], canonical_name="未来姓名",
                                   source="MODEL", identity_facts_json=(
                                       '[]' if during_call else '{"bad":"ledger"}'))
            session.add(person)
            session.flush()
            person_id = person.id

        class CorruptingFake(FakeProviderAdapter):
            def _next(self, *, kind, payload=None):
                response = super()._next(kind=kind, payload=payload)
                if during_call:
                    with transaction(factory) as session:
                        session.get(BookCharacter, person_id).identity_facts_json = '{"bad":"ledger"}'
                response["_usage"] = {"total_tokens": 37, "input_tokens": 30, "output_tokens": 7}
                return response

        adapter = CorruptingFake()
        result = _run_with_fake(migrated_settings, job["id"], adapter)
        assert result.state is JobState.FAILED
        assert len(adapter.calls) == (1 if during_call else 0)
        with factory() as session:
            assert "人物资料无法安全读取" in session.get(Job, job["id"]).last_error
            runs = list(session.scalars(select(InferenceRun)))
            assert not list(session.scalars(select(Annotation)))
            if during_call:
                assert len(runs) == 1 and runs[0].state is InferenceRunState.FAILED
                assert runs[0].error_code == "INVALID_IDENTITY_INPUT"
                assert json.loads(runs[0].usage_json)["total_tokens"] == 37
            else:
                assert not runs
    finally:
        engine.dispose()


def test_preview_then_process_reuses_cache_and_idempotency(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    """预览后处理同一范围命中缓存；重复点击开始返回同一任务。"""

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

    # 强制重做必须真正调用模型，而不是再次应用同一份缓存。
    forced = _create_job(
        fake_provider_client, book_id, profile_id,
        key="k-force-process", mode="process", force_reprocess=True,
    )
    third = _run_with_fake(migrated_settings, forced["id"], adapter)
    assert third.state is JobState.COMPLETED
    assert third.calls == 1
    assert third.cached_windows == 0
    assert len(adapter.calls) == 2


def test_failed_model_output_error_keeps_raw_snippet(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    """真实提供方返回空内容/坏结构时，任务错误信息必须带脱敏片段。"""

    data = _import(fake_provider_client)
    profile_id = _fake_profile(fake_provider_client)
    job = _create_job(fake_provider_client, data["book_id"], profile_id, key="k-snippet")

    failure = ProviderError(
        ProviderErrorKind.INVALID_OUTPUT,
        "模型返回空内容",
        details={"body": '{"choices": [{"finish_reason": "length", "message": {"content": ""}}]}'},
    )
    # 连续两次坏输出（首次 + 纠错重发各一次）才允许失败：证明重发是有上限的
    adapter = FakeProviderAdapter(script=[failure, failure])
    outcome = _run_with_fake(migrated_settings, job["id"], adapter)
    assert outcome.state is JobState.FAILED

    response = fake_provider_client.post(
        f"/api/books/{data['book_id']}/estimates",
        json={"range": {"start_cp": 0, "end_cp": len(SAMPLE)}},
    )
    window = response.json()["data"]["windows"][0]
    assert window["processing_status"] == "failed"
    assert window["processed_target_count"] == 0
    assert "原始输出片段" in window["last_error"]

    engine, factory = _factory(migrated_settings)
    try:
        with transaction(factory) as session:
            stored = session.get(Job, job["id"])
            assert stored is not None and stored.last_error is not None
            assert "原始输出片段" in stored.last_error
            assert "finish_reason" in stored.last_error
    finally:
        engine.dispose()


def test_truncated_output_retry_raises_max_tokens(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    """首次被输出上限截断时，纠错重发要同时提高 max_tokens，而不是用同样预算再赌一次。"""

    data = _import(fake_provider_client)
    profile_id = _fake_profile(fake_provider_client)
    job = _create_job(fake_provider_client, data["book_id"], profile_id, key="k-truncated")

    truncated = ProviderError(
        ProviderErrorKind.INVALID_OUTPUT,
        "模型输出不是合法 JSON（提供方因输出上限被截断；重试会自动提高 max_tokens）",
        details={"body": '{"schema_version":"1.0"', "finish_reason": "length"},
    )
    adapter = FakeProviderAdapter(script=[truncated], labeling_mode="deterministic")

    outcome = _run_with_fake(migrated_settings, job["id"], adapter)

    assert outcome.state is JobState.COMPLETED
    assert len(adapter.calls) == 2
    first = adapter.calls[0]["payload"]
    second = adapter.calls[1]["payload"]
    assert "max_tokens_override" not in first
    assert second["max_tokens_override"] == 32000  # 800 → max(2×, 32000)
    assert second["max_tokens_override"] > first["max_tokens"]


def test_invalid_output_triggers_one_repair_retry(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    """契约错误最多纠错重发一次：一次坏输出后第二次成功，窗口仍完成。"""

    data = _import(fake_provider_client)
    profile_id = _fake_profile(fake_provider_client)
    job = _create_job(fake_provider_client, data["book_id"], profile_id, key="k-retry")

    bad_output = {
        "schema_version": "1.0",
        "scene_updates": [],
        "gap_decisions": [],
        # 未声明就使用 NEW（真实模型常见错误）→ 触发一次纠错重发
        "new_speakers": [],
        "labels": [
            {
                "quote_id": "q-not-sent",
                "scene_ref": "scene_current",
                "kind": "speech",
                "assignment": "NEW",
                "speaker_ref": "new1",
                "basis": "DIRECT",
                "evidence_refs": [],
            }
        ],
    }
    adapter = FakeProviderAdapter(script=[bad_output], labeling_mode="deterministic")

    outcome = _run_with_fake(migrated_settings, job["id"], adapter)

    assert outcome.state is JobState.COMPLETED
    assert outcome.calls == 2  # 首次 + 一次纠错重发
    assert len(adapter.calls) == 2


@pytest.mark.parametrize("limit,bad_count,expected_calls,expected_state", [
    (0, 1, 1, JobState.FAILED),
    (2, 2, 3, JobState.COMPLETED),
    (2, 10, 3, JobState.FAILED),
    (5, 10, 6, JobState.FAILED),
])
@pytest.mark.parametrize("provider_error", [False, True])
def test_configured_validation_retry_limit_counts_every_attempt(
    fake_provider_client: TestClient, migrated_settings: Settings,
    limit: int, bad_count: int, expected_calls: int, expected_state: JobState, provider_error: bool,
) -> None:
    data = _import(fake_provider_client)
    job = _create_job(fake_provider_client, data["book_id"], _fake_profile(fake_provider_client),
                      key="configured-retries", budget={"max_format_retries": limit})
    bad = (ProviderError(ProviderErrorKind.INVALID_OUTPUT, "坏 JSON") if provider_error
           else {"schema_version": "1.0", "labels": []})
    adapter = FakeProviderAdapter(script=[bad] * bad_count, labeling_mode="deterministic",
                                  usage={"input_tokens": 10, "output_tokens": 20})
    outcome = _run_with_fake(migrated_settings, job["id"], adapter)
    assert outcome.state is expected_state
    assert len(adapter.calls) == expected_calls
    assert all("上一次输出无效" in call["payload"]["messages"][-1]["content"]
               for call in adapter.calls[1:])
    engine, factory = _factory(migrated_settings)
    try:
        with factory() as session:
            runs = list(session.execute(select(InferenceRun).where(
                InferenceRun.job_id == job["id"]).order_by(InferenceRun.created_at)).scalars())
            assert len(runs) == expected_calls
            assert all(run.state in {InferenceRunState.FAILED, InferenceRunState.SUCCEEDED} for run in runs)
            for run, call in zip(runs, adapter.calls, strict=True):
                snapshot = json.loads(run.profile_snapshot_json)
                assert run.request_fingerprint == fingerprint({
                    "version": "production-request-1", "request": call["payload"],
                    "model_configuration": {key: snapshot.get(key) for key in
                        ("protocol", "base_url", "model", "params", "inference_options")},
                })
            if expected_calls > 1:
                assert runs[0].request_fingerprint != runs[1].request_fingerprint
    finally:
        engine.dispose()


def test_transport_retries_freeze_the_actual_request_and_keep_its_fingerprint(
    fake_provider_client, migrated_settings,
):
    data = _import(fake_provider_client)
    job = _create_job(fake_provider_client, data["book_id"], _fake_profile(fake_provider_client),
                      key="frozen-rate-retry")
    captured = []

    class MutatingAdapter(FakeProviderAdapter):
        async def generate_labels(self, payload):
            captured.append(deepcopy(payload))
            try:
                return await super().generate_labels(payload)
            finally:
                payload["messages"][0]["content"] = "adapter-mutated"
                payload["target_quote_ids"].clear()

    migrated_settings.rate_limit_backoff_base_seconds = 0
    adapter = MutatingAdapter(script=[ProviderError(ProviderErrorKind.RATE_LIMITED, "限流")])
    outcome = _run_with_fake(migrated_settings, job["id"], adapter)
    assert outcome.state is JobState.COMPLETED
    assert len(captured) == 2 and captured[0] == captured[1]
    engine, factory = _factory(migrated_settings)
    try:
        with factory() as session:
            runs = list(session.scalars(select(InferenceRun).where(
                InferenceRun.job_id == job["id"]).order_by(InferenceRun.created_at)))
            assert [run.state for run in runs] == [InferenceRunState.FAILED, InferenceRunState.SUCCEEDED]
            assert runs[0].request_fingerprint == runs[1].request_fingerprint
            snapshot = json.loads(runs[0].profile_snapshot_json)
            assert runs[0].request_fingerprint == fingerprint({
                "version": "production-request-1", "request": captured[0],
                "model_configuration": {key: snapshot.get(key) for key in
                    ("protocol", "base_url", "model", "params", "inference_options")},
            })
    finally:
        engine.dispose()


def test_retry_respects_remaining_budget(fake_provider_client: TestClient, migrated_settings: Settings) -> None:
    data = _import(fake_provider_client)
    job = _create_job(fake_provider_client, data["book_id"], _fake_profile(fake_provider_client),
                      key="retry-budget", budget={"max_input_tokens": 200_000, "max_format_retries": 5})
    adapter = FakeProviderAdapter(script=[{"schema_version": "1.0", "labels": []}],
                                  usage={"input_tokens": 200_000, "output_tokens": 1})
    outcome = _run_with_fake(migrated_settings, job["id"], adapter)
    assert outcome.state is JobState.BUDGET_EXHAUSTED
    assert len(adapter.calls) == 1


def test_retry_count_does_not_retry_unknown_timeout(fake_provider_client: TestClient, migrated_settings: Settings) -> None:
    data = _import(fake_provider_client)
    job = _create_job(fake_provider_client, data["book_id"], _fake_profile(fake_provider_client),
                      key="no-timeout-retry", budget={"max_format_retries": 5})
    adapter = FakeProviderAdapter(script=[ProviderError(ProviderErrorKind.TIMEOUT, "结果未知")])
    assert _run_with_fake(migrated_settings, job["id"], adapter).state is JobState.NEEDS_RECONCILIATION
    assert len(adapter.calls) == 1

    engine, factory = _factory(migrated_settings)
    try:
        with transaction(factory) as session:
            runs = list(
                session.execute(
                    select(InferenceRun)
                    .where(InferenceRun.job_id == job["id"])
                    .order_by(InferenceRun.created_at)
                ).scalars()
            )
            assert [run.state for run in runs] == [
                InferenceRunState.UNKNOWN_OUTCOME,
            ]
    finally:
        engine.dispose()


def test_equivalent_active_jobs_with_fresh_keys_are_coalesced(
    fake_provider_client: TestClient,
) -> None:
    data = _import(fake_provider_client)
    profile_id = _fake_profile(fake_provider_client)

    first = _create_job(
        fake_provider_client,
        data["book_id"],
        profile_id,
        key="fresh-key-1",
    )
    second = _create_job(
        fake_provider_client,
        data["book_id"],
        profile_id,
        key="fresh-key-2",
    )

    assert second["id"] == first["id"]


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
    """usage 缺失不写 0；提供方给了 usage 时按口径结算。"""

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
    assert "cost" not in usage and "currency" not in usage

    # 提供方上报 usage 时按真实数字结算
    known_job = _create_job(
        fake_provider_client, book_id, profile_id, key="k-known", range={"start_cp": 0, "end_cp": 12}
    )
    adapter = FakeProviderAdapter(usage={"input_tokens": 30, "output_tokens": 10, "total_tokens": 40})
    outcome = _run_with_fake(migrated_settings, known_job["id"], adapter)
    assert outcome.state is JobState.COMPLETED
    detail = fake_provider_client.get(f"/api/jobs/{known_job['id']}").json()["data"]
    assert detail["usage"]["total_tokens"] == 40
    assert detail["usage"]["input_tokens"] == 30
    assert detail["usage"]["output_tokens"] == 10
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

    fallback_job = _create_job(
        fake_provider_client, book_id, profile_id, key="k-usage-fallback",
        range={"start_cp": 0, "end_cp": 12}, force_reprocess=True,
    )
    fallback = _run_with_fake(
        migrated_settings, fallback_job["id"],
        FakeProviderAdapter(usage={"input_tokens": 30, "output_tokens": 10}),
    )
    assert fallback.state is JobState.COMPLETED
    fallback_detail = fake_provider_client.get(f"/api/jobs/{fallback_job['id']}").json()["data"]
    assert fallback_detail["usage"]["total_tokens"] == 40
    usage3 = fake_provider_client.get(f"/api/books/{book_id}/usage").json()["data"]
    assert usage3["total_tokens"] == usage2["total_tokens"] + 40


def test_budget_exhaustion_stops_before_next_call(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    """预算到顶就停，不再追加调用，原文与已完成结果保持可读。"""

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
    """进程中断在请求发出后 → 标未知结果，不自动重发，等人工对账。"""

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


def test_estimate_endpoint_is_local_only(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
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
    assert len(payload["windows"]) == payload["window_count"]
    assert payload["windows"][0]["target_count"] > 0
    assert payload["windows"][0]["estimated_tokens"] > 0
    assert payload["windows"][0]["preview"]
    assert payload["windows"][0]["processing_status"] == "unprocessed"
    assert payload["windows"][0]["processed_target_count"] == 0
    assert payload["estimator"]["method"] == "heuristic-cjk"
    assert any("启发式" in note for note in payload["notes"])
    profile_id = _fake_profile(fake_provider_client, "窗口选择测试")
    selected = _create_job(
        fake_provider_client,
        data["book_id"],
        profile_id,
        key="selected-window",
        selected_window_ids=[payload["windows"][0]["window_id"]],
    )
    outcome = _run_with_fake(migrated_settings, selected["id"], FakeProviderAdapter())
    assert outcome.windows_total == 1
    restored = fake_provider_client.post(
        f"/api/books/{data['book_id']}/estimates",
        json={"range": {"start_cp": 0, "end_cp": len(SAMPLE)}},
    ).json()["data"]
    assert restored["windows"][0]["processing_status"] == "completed"
    assert restored["windows"][0]["processed_target_count"] == restored["windows"][0]["target_count"]
    # 未知任务仍保持标准 404 契约。
    assert fake_provider_client.get("/api/jobs/does-not-exist").status_code == 404
