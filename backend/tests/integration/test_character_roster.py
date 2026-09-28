from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from ndr.characters.service import store_roster_candidates
from ndr.config import Settings
from ndr.domain.enums import CharacterSource
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
