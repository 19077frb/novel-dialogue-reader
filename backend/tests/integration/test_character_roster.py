from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from ndr.characters.service import store_roster_candidates
from ndr.config import Settings
from ndr.domain.enums import CharacterSource, JobState
from ndr.jobs.scheduler import run_job
from ndr.llm.schemas import RosterOutput
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.models import BookCharacter, BookVersion, Chapter
from ndr.storage.transactions import transaction


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
                        }
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
