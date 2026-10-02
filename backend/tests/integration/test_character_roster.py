from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from fixtures.epub_factory import Document, EpubSpec, build_epub
from ndr.app import create_app
from ndr.characters.service import store_roster_candidates
from ndr.config import Settings
from ndr.domain.enums import CharacterSource, JobState
from ndr.jobs.roster import run_character_roster_job
from ndr.jobs.scheduler import run_job
from ndr.llm.adapters.fake import FakeProviderAdapter
from ndr.llm.schemas import RosterOutput
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.models import BookCharacter, BookVersion, Chapter, InferenceRun
from ndr.storage.transactions import transaction


@pytest.mark.parametrize("allow", [False, True])
def test_background_manual_correction_is_controlled_by_saved_job_setting(migrated_client, allow):
    client = migrated_client
    imported = client.post("/api/books/import", files={
        "file": ("names.txt", "第一章\n「我是阿库娅。」".encode(), "text/plain"),
    }).json()["data"]
    book_id = imported["book_id"]
    chapter_id = client.get(f"/api/books/{book_id}/chapters").json()["data"][0]["id"]
    with transaction(client.app.state.session_factory) as session:
        character = BookCharacter(book_version_id=imported["book_version_id"],
                                  canonical_name="阿库亚", description="人工旧说明",
                                  user_confirmed=True, name_locked=True,
                                  confirmation_source="manual", source=CharacterSource.USER)
        session.add(character)
        session.flush()
        character_id = character.id
        from ndr.characters.visibility import capture
        capture(character, 0)
    profile = client.post("/api/model-profiles", json={
        "name": "fake", "protocol": "fake-provider", "base_url": "http://127.0.0.1:1",
        "model": "fake", "credential_mode": "none",
    }).json()["data"]
    job = client.post(f"/api/books/{book_id}/chapters/{chapter_id}/character-roster/analyze",
                      json={"profile_id": profile["id"], "idempotency_key": "saved-policy",
                            "run_now": False, "allow_overwrite_manual": allow}).json()["data"]
    repeated = client.post(f"/api/books/{book_id}/chapters/{chapter_id}/character-roster/analyze",
                           json={"profile_id": profile["id"], "idempotency_key": "saved-policy",
                                 "run_now": False,
                                 **({"allow_overwrite_manual": True} if allow else {})})
    assert repeated.status_code == 202 and repeated.json()["data"]["id"] == job["id"]
    changed = client.post(f"/api/books/{book_id}/chapters/{chapter_id}/character-roster/analyze",
                          json={"profile_id": profile["id"], "idempotency_key": "saved-policy",
                                "run_now": False, "allow_overwrite_manual": not allow})
    assert changed.status_code == 409
    adapter = FakeProviderAdapter(script=[{"schema_version": "1.0", "characters": [{
        "temp_ref": "c1", "character_id": character_id, "name": "阿库娅",
        "real_name": "阿库娅", "description": "有原文依据的新说明", "aliases": [],
        "evidence_refs": ["L1"], "pov_candidate": True,
    }]}])
    outcome = run_character_roster_job(client.app.state.session_factory, client.app.state.settings,
                      job_id=job["id"], adapter_factory=lambda *_: adapter)
    assert outcome.state is JobState.COMPLETED
    path = f"/api/books/{book_id}/chapters/{chapter_id}/character-roster"
    roster = client.get(path).json()["data"]
    # Merely analyzing must not rewrite human data even when permission is enabled.
    with transaction(client.app.state.session_factory) as session:
        assert session.get(BookCharacter, character_id).canonical_name == "阿库亚"
    candidate = roster["candidates"][0]
    accepted = {key: candidate[key] for key in (
        "temp_ref", "character_id", "canonical_name", "aliases", "description",
    )}
    response = client.put(path, json={"candidates": [accepted], "pov_temp_ref": "c1",
                                      "expected_version": roster["version"],
                                      "confirmation_mode": "automatic"})
    assert response.status_code == 200, response.text
    with transaction(client.app.state.session_factory) as session:
        character = session.get(BookCharacter, character_id)
        assert character.canonical_name == ("阿库娅" if allow else "阿库亚")
        assert character.description == ("有原文依据的新说明" if allow else "人工旧说明")
        assert character.user_confirmed and character.name_locked
        assert character.confirmation_source == "manual"
        if allow:
            assert "阿库亚" in json.loads(character.aliases_json)
            history = json.loads(character.presentation_history_json)
            assert history[0]["name"] == "阿库亚"
            assert history[-1]["name"] == "阿库娅"
            assert history[-1]["cp"] == session.get(Chapter, chapter_id).end_cp



@pytest.mark.parametrize("body", ["", "<p>   </p>", '<img src="plate.png" alt="插图"/>'])
def test_textless_chapter_completes_without_model_and_persists(
    migrated_client: TestClient, migrated_settings: Settings, body: str,
) -> None:
    raw = build_epub(EpubSpec(
        documents=[Document("empty", "empty.xhtml", body)],
        resources={"plate.png": b"\x89PNG\r\n\x1a\nfixture"},
        nav=[("empty.xhtml", "插图")],
    ))
    response = migrated_client.post("/api/books/import", files={
        "file": ("empty.epub", raw, "application/epub+zip"),
    })
    assert response.status_code == 202, response.text
    data = response.json()["data"]
    base = f"/api/books/{data['book_id']}"
    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)
    try:
        chapters = migrated_client.get(f"{base}/chapters").json()["data"]
        if not chapters:
            # Current EPUB parser omits entirely empty files. Older imports may
            # still contain them; retain an empty chapter to exercise that path.
            with transaction(factory) as session:
                session.add(Chapter(
                    book_version_id=data["book_version_id"], ordinal=0, title="空白章",
                    start_cp=0, end_cp=0,
                ))
            chapters = migrated_client.get(f"{base}/chapters").json()["data"]
        else:
            assert chapters[0]["dialogue_processed"] is True
            initial_roster = migrated_client.get(
                f"{base}/chapters/{chapters[0]['id']}/character-roster",
            ).json()["data"]
            assert initial_roster["status"] == "CONFIRMED"
            assert initial_roster["candidates"] == []
        chapter = chapters[0]
        endpoint = f"{base}/chapters/{chapter['id']}/character-roster"
        # Emulate an obsolete failed/not-complete state.
        with transaction(factory) as session:
            session.get(Chapter, chapter["id"]).dialogue_processed = False
        profile = migrated_client.post("/api/model-profiles", json={
            "name": "不可调用的配置", "protocol": "fake-provider",
            "base_url": "http://127.0.0.1:1", "model": "fake-model",
            "credential_mode": "none",
        }).json()["data"]
        for attempt in range(2):
            created = migrated_client.post(f"{endpoint}/analyze", json={
                "book_version_id": data["book_version_id"], "profile_id": profile["id"],
                "idempotency_key": f"empty-roster-{attempt}", "run_now": False,
                "max_input_tokens": 1,
            })
            assert created.status_code == 202, created.text
            job_id = created.json()["data"]["id"]
            # Fake provider is disabled: constructing/calling it would fail.
            outcome = run_job(factory, migrated_settings, job_id=job_id)
            assert outcome.state is JobState.COMPLETED
            assert outcome.calls == 0
            detail = migrated_client.get(f"/api/jobs/{job_id}").json()["data"]
            assert detail["progress"]["skipped_reason"] == "no_text"
            assert detail["last_error"] is None
            assert detail["unknown_usage_runs"] == 0
            with factory() as session:
                assert list(session.scalars(select(InferenceRun).where(
                    InferenceRun.job_id == job_id,
                ))) == []
        assert migrated_client.get(f"{base}/chapters").json()["data"][0]["dialogue_processed"]
        roster = migrated_client.get(endpoint).json()["data"]
        assert roster["status"] == "CONFIRMED"
        assert roster["pov_character_id"] is None
    finally:
        engine.dispose()


@pytest.mark.parametrize(("text", "completed"), [(" \n\t\n", True), ("本章是没有对白的叙述。\n", False)])
def test_completion_requires_absent_text_not_absent_quotes(
    migrated_client: TestClient, text: str, completed: bool,
) -> None:
    response = migrated_client.post("/api/books/import", files={
        "file": ("chapter.txt", text.encode(), "text/plain"),
    })
    assert response.status_code == 202, response.text
    book_id = response.json()["data"]["book_id"]
    chapter = migrated_client.get(f"/api/books/{book_id}/chapters").json()["data"][0]
    assert chapter["dialogue_processed"] is completed
    roster = migrated_client.get(
        f"/api/books/{book_id}/chapters/{chapter['id']}/character-roster",
    ).json()["data"]
    assert roster["status"] == ("CONFIRMED" if completed else "DRAFT")


@pytest.mark.parametrize(("body", "completed"), [
    ("<h1>第一卷 插图</h1>", True),
    ("<h1>第一卷 插图</h1><p>这里有一段叙述。</p>", False),
    ("<p>第一卷 插图</p>", False),
])
def test_heading_only_chapters_complete_on_import_and_startup(
    migrated_client: TestClient, migrated_settings: Settings, body: str, completed: bool,
) -> None:
    raw = build_epub(EpubSpec(documents=[Document("chapter", "chapter.xhtml", body)],
                            nav=[("chapter.xhtml", "第一卷 插图")]))
    imported = migrated_client.post("/api/books/import", files={
        "file": ("chapter.epub", raw, "application/epub+zip"),
    }).json()["data"]
    path = f"/api/books/{imported['book_id']}/chapters"
    chapter = migrated_client.get(path).json()["data"][0]
    assert chapter["dialogue_processed"] is completed
    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            session.get(Chapter, chapter["id"]).dialogue_processed = False
        # No task creation or model calls are needed to repair legacy status.
        with TestClient(create_app(migrated_settings)) as restarted:
            assert restarted.get(path).json()["data"][0]["dialogue_processed"] is completed
        with TestClient(create_app(migrated_settings)) as restarted:
            assert restarted.get(path).json()["data"][0]["dialogue_processed"] is completed
    finally:
        engine.dispose()


@pytest.mark.parametrize("invalid", ["missing_file", "invalid_range"])
def test_startup_does_not_complete_unreadable_heading_chapters(
    migrated_client: TestClient, migrated_settings: Settings, invalid: str,
) -> None:
    imported = migrated_client.post("/api/books/import", files={
        "file": ("chapter.txt", "第一卷 插图\n".encode(), "text/plain"),
    }).json()["data"]
    path = f"/api/books/{imported['book_id']}/chapters"
    chapter = migrated_client.get(path).json()["data"][0]
    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            row = session.get(Chapter, chapter["id"])
            row.dialogue_processed = False
            if invalid == "invalid_range":
                row.end_cp += 1
            else:
                session.get(BookVersion, imported["book_version_id"]).canonical_path = "missing.txt"
        with TestClient(create_app(migrated_settings)) as restarted:
            assert restarted.get(path).json()["data"][0]["dialogue_processed"] is False
    finally:
        engine.dispose()


def test_roster_analysis_cannot_overwrite_user_confirmed_identity(
    migrated_client: TestClient,
    migrated_settings: Settings,
) -> None:
    imported = migrated_client.post(
        "/api/books/import",
        files={"file": ("chapter.txt", "第一章\n「你好。」".encode(), "text/plain")},
    )
    assert imported.status_code == 202, imported.text
    data = imported.json()["data"]

    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            version = session.get(BookVersion, data["book_version_id"])
            assert version is not None
            chapter = session.execute(
                select(Chapter).where(Chapter.book_version_id == version.id)
            ).scalars().first()
            assert chapter is not None
            confirmed = BookCharacter(
                book_version_id=version.id,
                canonical_name="浅村悠太",
                aliases_json=json.dumps(["悠太"], ensure_ascii=False),
                description="用户确认的本章主人公",
                source=CharacterSource.USER,
                user_confirmed=True,
                first_seen_cp=chapter.start_cp,
            )
            session.add(confirmed)
            session.flush()
            confirmed_id = confirmed.id

            output = RosterOutput.model_validate(
                {
                    "schema_version": "1.0",
                    "characters": [
                        {
                            "temp_ref": "C1",
                            "name": "悠太",
                            "aliases": ["错误别名"],
                            "description": "模型生成的冲突说明",
                            "evidence_refs": ["L1"],
                            "pov_candidate": True,
                        },
                        {
                            "temp_ref": "C2",
                            "name": "浅村悠太（本章第一人称叙述者，书店店员）",
                            "aliases": [],
                            "description": "同一人物的另一种模型说明",
                            "evidence_refs": ["L2"],
                            "pov_candidate": False,
                        },
                    ],
                }
            )
            roster = store_roster_candidates(
                session,
                version=version,
                chapter=chapter,
                output=output,
                job_id=None,
            )
            record = json.loads(roster.candidates_json)[0]
            assert len(json.loads(roster.candidates_json)) == 1
            assert record["evidence_refs"] == ["L1", "L2"]

            assert record["character_id"] == confirmed_id
            assert record["canonical_name"] == "浅村悠太"
            assert record["aliases"] == ["悠太"]
            assert record["description"] == "用户确认的本章主人公"
            assert record["pov_candidate"] is True

            persisted = session.get(BookCharacter, confirmed_id)
            assert persisted is not None
            assert persisted.canonical_name == "浅村悠太"
            assert json.loads(persisted.aliases_json) == ["悠太"]
            assert persisted.description == "用户确认的本章主人公"
            assert persisted.source is CharacterSource.USER
            assert persisted.user_confirmed is True
    finally:
        engine.dispose()


def test_roster_analysis_runs_offline_with_fake_provider(
    migrated_client: TestClient,
    migrated_settings: Settings,
) -> None:
    """离线跑通「分析本章人物 → 确认名单与主人公」。"""

    imported = migrated_client.post(
        "/api/books/import",
        files={
            "file": (
                "chapter.txt",
                "第一章 开场\n「你好。」\n「再见。」\n".encode(),
                "text/plain",
            )
        },
    )
    assert imported.status_code == 202, imported.text
    data = imported.json()["data"]
    book_id = data["book_id"]
    version_id = data["book_version_id"]

    profile = migrated_client.post(
        "/api/model-profiles",
        json={
            "name": "离线人物提供方",
            "protocol": "fake-provider",
            "base_url": "http://127.0.0.1:1",
            "model": "fake-model",
            "credential_mode": "none",
        },
    )
    assert profile.status_code == 201, profile.text
    profile_id = profile.json()["data"]["id"]

    chapters = migrated_client.get(f"/api/books/{book_id}/chapters").json()["data"]
    chapter_id = chapters[0]["id"]

    created = migrated_client.post(
        f"/api/books/{book_id}/chapters/{chapter_id}/character-roster/analyze",
        json={
            "book_version_id": version_id,
            "profile_id": profile_id,
            "idempotency_key": "roster-offline-1",
            "run_now": False,
        },
    )
    assert created.status_code == 202, created.text
    job_id = created.json()["data"]["id"]

    # 走真实的调度与适配器构建路径：仅测试用的 FakeProvider 在显式启用时给出确定性人物名单。
    run_settings = Settings(
        data_dir=migrated_settings.data_dir,
        credential_backend="session",
        allow_fake_provider=True,
        fake_provider_labels="deterministic",
    )
    engine = create_db_engine(run_settings)
    factory = create_session_factory(engine)
    try:
        outcome = run_job(factory, run_settings, job_id=job_id)
    finally:
        engine.dispose()
    assert outcome.state is JobState.COMPLETED, outcome
    assert outcome.calls == 1

    roster = migrated_client.get(
        f"/api/books/{book_id}/chapters/{chapter_id}/character-roster"
    ).json()["data"]
    assert roster["status"] == "DRAFT"
    assert [item["canonical_name"] for item in roster["candidates"]] == [
        "样例说话人甲",
        "样例说话人乙",
    ]
    assert roster["candidates"][0]["pov_candidate"] is True
    assert roster["candidates"][0]["character_id"]

    confirmed = migrated_client.put(
        f"/api/books/{book_id}/chapters/{chapter_id}/character-roster",
        json={
            "book_version_id": version_id,
            "candidates": [
                {
                    "temp_ref": "c1",
                    "accepted": True,
                    "character_id": roster["candidates"][0]["character_id"],
                    "canonical_name": "样例说话人甲",
                    "aliases": [],
                    "description": "",
                }
            ],
            "pov_temp_ref": "c1",
            "expected_version": roster["version"],
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()["data"]
    assert body["status"] == "CONFIRMED"
    assert body["pov_character_id"] == roster["candidates"][0]["character_id"]
    assert [item["name"] for item in body["confirmed_characters"]] == ["样例说话人甲"]

    dialogue = migrated_client.post(
        "/api/jobs",
        json={
            "book_id": book_id,
            "book_version_id": version_id,
            "profile_id": profile_id,
            "mode": "process",
            "range": {"chapter_id": chapter_id},
            "idempotency_key": "dialogue-full-chapter-1",
            "run_now": False,
        },
    )
    assert dialogue.status_code == 202, dialogue.text
    engine = create_db_engine(run_settings)
    factory = create_session_factory(engine)
    try:
        dialogue_outcome = run_job(
            factory,
            run_settings,
            job_id=dialogue.json()["data"]["id"],
            adapter_factory=lambda *_: FakeProviderAdapter(labeling_mode="deterministic"),
        )
    finally:
        engine.dispose()
    assert dialogue_outcome.state is JobState.COMPLETED, dialogue_outcome
    refreshed_chapter = migrated_client.get(f"/api/books/{book_id}/chapters").json()["data"][0]
    assert refreshed_chapter["dialogue_processed"] is True


def test_roster_analysis_respects_batch_token_limit(
    migrated_client: TestClient,
    migrated_settings: Settings,
) -> None:
    imported = migrated_client.post(
        "/api/books/import",
        files={"file": ("limited.txt", "第一章\n「你好。」".encode(), "text/plain")},
    ).json()["data"]
    profile = migrated_client.post(
        "/api/model-profiles",
        json={
            "name": "批量预算测试",
            "protocol": "fake-provider",
            "base_url": "http://127.0.0.1:1",
            "model": "fake-model",
            "credential_mode": "none",
        },
    ).json()["data"]
    chapter = migrated_client.get(
        f"/api/books/{imported['book_id']}/chapters"
    ).json()["data"][0]
    created = migrated_client.post(
        f"/api/books/{imported['book_id']}/chapters/{chapter['id']}/character-roster/analyze",
        json={
            "book_version_id": imported["book_version_id"],
            "profile_id": profile["id"],
            "idempotency_key": "roster-budget-1",
            "max_input_tokens": 1,
            "run_now": False,
        },
    ).json()["data"]

    run_settings = Settings(
        data_dir=migrated_settings.data_dir,
        credential_backend="session",
        allow_fake_provider=True,
        fake_provider_labels="deterministic",
    )
    engine = create_db_engine(run_settings)
    factory = create_session_factory(engine)
    try:
        outcome = run_job(factory, run_settings, job_id=created["id"])
    finally:
        engine.dispose()
    assert outcome.state is JobState.BUDGET_EXHAUSTED
    assert outcome.calls == 0
