"""Identity reuse survives a late name reveal and subsequent chapters."""

import json

import pytest
from sqlalchemy import select

from ndr.characters.identity import supplement_aliases
from ndr.characters.service import (
    confirm_roster,
    existing_characters_for_prompt,
    store_roster_candidates,
)
from ndr.domain.characters import RosterConfirmIn
from ndr.domain.enums import CharacterSource
from ndr.llm.schemas import NewSpeaker, RosterOutput
from ndr.scenes.engine import _discover_character
from ndr.scenes.state import ConfirmedCharacter, SceneState
from ndr.speakers.groups import SpeakerRegistry
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.models import BookCharacter, BookVersion, Chapter
from ndr.storage.transactions import transaction


@pytest.mark.parametrize("role,name", [("女神", "阿库娅"), ("女骑士", "达克妮丝")])
@pytest.mark.parametrize("locked", [False, True])
def test_roster_name_reveal_is_applied_at_confirmation(
    migrated_client, migrated_settings, role, name, locked,
):
    imported = migrated_client.post("/api/books/import", files={
        "file": ("reveal.txt", f"第一章\n「我是{name}。」".encode(), "text/plain"),
    }).json()["data"]
    engine = create_db_engine(migrated_settings)
    try:
        with transaction(create_session_factory(engine)) as session:
            version = session.get(BookVersion, imported["book_version_id"])
            chapter = session.scalar(select(Chapter).where(Chapter.book_version_id == version.id))
            character = BookCharacter(
                book_version_id=version.id, canonical_name=role, user_confirmed=True,
                aliases_json=json.dumps([name]), name_locked=locked,
            )
            session.add(character)
            session.flush()
            output = RosterOutput.model_validate({"characters": [{
                "temp_ref": "c1", "character_id": character.id, "name": role,
                "real_name": name, "aliases": [name], "evidence_refs": ["L1"],
            }]})
            roster = store_roster_candidates(
                session, version=version, chapter=chapter, output=output, job_id=None,
            )
            assert character.canonical_name == role
            record = json.loads(roster.candidates_json)[0]
            expected = role if locked else name
            assert record["canonical_name"] == expected
            payload = RosterConfirmIn(
                expected_version=roster.version, pov_temp_ref="c1",
                candidates=[{key: record[key] for key in (
                    "temp_ref", "character_id", "canonical_name", "aliases", "description",
                )}],
            )
            confirm_roster(session, version=version, chapter=chapter, payload=payload)
            assert character.canonical_name == expected
            assert character.id == record["character_id"]
            if not locked:
                assert role in json.loads(character.aliases_json)
    finally:
        engine.dispose()


def test_dialogue_explicit_name_reveal_upgrades_same_identity(migrated_client, migrated_settings):
    imported = migrated_client.post("/api/books/import", files={
        "file": ("reveal.txt", "第一章\n「我是阿库娅。」".encode(), "text/plain"),
    }).json()["data"]
    engine = create_db_engine(migrated_settings)
    try:
        with transaction(create_session_factory(engine)) as session:
            character = BookCharacter(
                book_version_id=imported["book_version_id"], canonical_name="女神",
                aliases_json='["阿库娅"]', user_confirmed=True,
            )
            session.add(character)
            session.flush()
            state = SceneState()
            declaration = NewSpeaker(
                character_id=character.id, name="阿库娅", real_name="阿库娅", aliases=["女神"],
                temp_ref="new1", scene_ref="scene_current", first_quote_id="q1",
                description="女神自报姓名", evidence_refs=["q1"],
            )
            assert _discover_character(session, state, imported["book_version_id"],
                                       declaration, 0) == character.id
            assert character.canonical_name == "阿库娅"
            assert json.loads(character.aliases_json) == ["女神"]
            assert state.book_characters[0].canonical_name == "阿库娅"
    finally:
        engine.dispose()


def test_late_name_reuses_stable_identity(migrated_client, migrated_settings):
    imported = migrated_client.post("/api/books/import", files={
        "file": ("identity.txt", "第一章\n「我是藤波夏帆。」".encode(), "text/plain"),
    }).json()["data"]
    engine = create_db_engine(migrated_settings)
    try:
        with transaction(create_session_factory(engine)) as session:
            version = session.get(BookVersion, imported["book_version_id"])
            chapter = session.scalar(select(Chapter).where(Chapter.book_version_id == version.id))
            character = BookCharacter(
                book_version_id=version.id, canonical_name="高个子女生",
                aliases_json='["补习班女生"]', description="用户确认的描述",
                source=CharacterSource.USER, user_confirmed=True,
            )
            session.add(character)
            session.flush()
            character_id = character.id
            output = RosterOutput.model_validate({"characters": [{
                "temp_ref": "c1", "character_id": character_id, "name": "藤波夏帆",
                "aliases": ["藤波同学"], "description": "新说明", "evidence_refs": ["L1"],
            }]})
            roster = store_roster_candidates(
                session, version=version, chapter=chapter, output=output, job_id=None,
            )
            assert json.loads(roster.candidates_json)[0]["character_id"] == character_id
            assert character.canonical_name == "高个子女生"
            assert character.description == "用户确认的描述"
            assert set(json.loads(character.aliases_json)) == {"补习班女生", "藤波夏帆", "藤波同学"}
            state = SceneState(confirmed_characters=[ConfirmedCharacter(
                character_id, character.canonical_name,
                tuple(json.loads(character.aliases_json)), character.description,
            )])
            discovery = NewSpeaker(
                character_id=character_id, name="夏帆同学", aliases=["藤波夏帆"],
                temp_ref="new1", scene_ref="scene_current", first_quote_id="q1",
                description="高个子女生自报姓名", evidence_refs=["q1"],
            )
            assert _discover_character(session, state, version.id, discovery, 0) == character_id
            registry = SpeakerRegistry(state)
            first = registry.register_temp_speaker(
                temp_ref="new1", first_quote_id="q1", canonical_name="夏帆同学",
                character_id=character_id,
            )
            second = registry.register_temp_speaker(
                temp_ref="new2", first_quote_id="q2", canonical_name="藤波夏帆",
                character_id=character_id,
            )
            assert first is second
            assert first.character_id == character_id
            assert len(list(session.scalars(select(BookCharacter)))) == 1
            inherited = existing_characters_for_prompt(session, version)
            assert "夏帆同学" in inherited[0]["aliases"]
            # Roster omission does not prevent book-level lookup.
            other_chapter = SceneState(book_characters=state.book_characters)
            assert other_chapter._confirmed_by_name("藤波同学").character_id == character_id
            with pytest.raises(ValueError, match="未提供"):
                store_roster_candidates(session, version=version, chapter=chapter,
                                        output=RosterOutput.model_validate({"characters": [{
                                            "temp_ref": "c2", "character_id": "foreign",
                                            "name": "藤波夏帆",
                                        }]}), job_id=None)
    finally:
        engine.dispose()


def test_attribution_discoveries_reach_next_roster_without_overwriting(migrated_client, migrated_settings):
    imported = migrated_client.post("/api/books/import", files={
        "file": ("identity.txt", "第一章\n「你好。」".encode(), "text/plain"),
    }).json()["data"]
    engine = create_db_engine(migrated_settings)
    try:
        with transaction(create_session_factory(engine)) as session:
            version = session.get(BookVersion, imported["book_version_id"])
            state = SceneState(confirmed_characters=[ConfirmedCharacter("pov", "浅村悠太")])
            discovery = NewSpeaker(
                name="藤波夏帆", aliases=["藤波同学"], temp_ref="new1",
                scene_ref="scene_current", first_quote_id="q1", description="自我介绍的女生",
                evidence_refs=["q1"],
            )
            first_id = _discover_character(session, state, version.id, discovery, 10)
            assert first_id
            assert _discover_character(session, state, version.id, discovery, 20) == first_id
            person = session.get(BookCharacter, first_id)
            assert person.first_seen_cp == 10
            assert "藤波同学" in existing_characters_for_prompt(session, version)[0]["aliases"]
            other = BookCharacter(book_version_id=version.id, canonical_name="藤波同学",
                                  aliases_json="[]")
            session.add(other)
            session.flush()
            supplement_aliases(session, other, ["藤波夏帆", "同学", "浅村悠太的父亲"])
            assert "藤波夏帆" not in json.loads(other.aliases_json)
            assert "同学" not in json.loads(other.aliases_json)
    finally:
        engine.dispose()
