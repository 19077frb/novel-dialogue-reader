from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ndr.domain.enums import (
    AnnotationSource,
    AnnotationStatus,
    CharacterRosterStatus,
    JobKind,
    JobState,
    QuoteKind,
)
from ndr.scenes.engine import _ensure_group
from ndr.scenes.state import SceneState, SpeakerSlot
from ndr.storage.models import (
    Annotation,
    BookCharacter,
    ChapterCharacterRoster,
    Job,
    Quote,
    Scene,
    SpeakerGroup,
)
from ndr.storage.transactions import transaction


@pytest.fixture()
def populated(migrated_client: TestClient):
    client = migrated_client
    imported = client.post(
        "/api/books/import",
        files={
            "file": ("book.txt", "第一章\n「你好。」\n「再见。」".encode(), "text/plain"),
        },
    ).json()["data"]
    book_id, version_id = imported["book_id"], imported["book_version_id"]
    chapter = client.get(f"/api/books/{book_id}/chapters").json()["data"][0]
    with transaction(client.app.state.session_factory) as session:
        target = BookCharacter(
            book_version_id=version_id, canonical_name="悠太", description="目标说明"
        )
        source = BookCharacter(
            book_version_id=version_id, canonical_name="浅村悠太", aliases_json='["哥哥"]'
        )
        scene = Scene(book_version_id=version_id, start_cp=0)
        session.add_all([target, source, scene])
        session.flush()
        linked = SpeakerGroup(
            scene_id=scene.id,
            display_label="S1",
            character_id=source.id,
            canonical_name=source.canonical_name,
            description="原说明",
        )
        unlinked = SpeakerGroup(
            scene_id=scene.id, display_label="S2", canonical_name="男客", description="书店客人"
        )
        roster = ChapterCharacterRoster(
            chapter_id=chapter["id"],
            book_version_id=version_id,
            status=CharacterRosterStatus.CONFIRMED,
            pov_character_id=source.id,
            confirmed_character_ids_json=json.dumps([source.id, target.id]),
            candidates_json=json.dumps(
                [
                    {
                        "temp_ref": "C1",
                        "character_id": source.id,
                        "canonical_name": source.canonical_name,
                    }
                ]
            ),
        )
        session.add_all([linked, unlinked, roster])
        session.flush()
        quote = session.scalars(select(Quote).where(Quote.book_version_id == version_id)).first()
        if quote is None:
            quote = Quote(
                book_version_id=version_id,
                chapter_id=chapter["id"],
                start_cp=4,
                end_cp=9,
                delimiter="「」",
                scanner_version="directory-test",
            )
            session.add(quote)
            session.flush()
        linked.first_quote_id = quote.id
        session.add(
            Annotation(
                quote_id=quote.id,
                scene_id=scene.id,
                speaker_id=linked.id,
                kind=QuoteKind.SPEECH,
                source=AnnotationSource.USER,
                status=AnnotationStatus.USER_CONFIRMED,
            )
        )
        ids = {
            "book": book_id,
            "version": version_id,
            "target": target.id,
            "source": source.id,
            "linked": linked.id,
            "unlinked": unlinked.id,
            "roster": roster.id,
        }
    return ids


def test_edit_directory_promotes_speaker_and_updates_existing_groups(migrated_client, populated):
    client, ids = migrated_client, populated
    base = f"/api/books/{ids['book']}/character-directory"
    listed = client.get(base).json()["data"]
    assert len(listed) == 3
    assert any(row["character_id"] == f"speaker:{ids['unlinked']}" for row in listed)
    response = client.put(
        f"{base}/{ids['source']}",
        json={
            "name": "浅村优太",
            "aliases": ["悠太", "悠太", " "],
            "description": "用户说明",
            "expected_version": 1,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["aliases"] == ["悠太"]
    assert response.json()["data"]["version"] == 2
    with transaction(client.app.state.session_factory) as session:
        group = session.get(SpeakerGroup, ids["linked"])
        assert group.canonical_name == "浅村优太"
        assert group.description == "用户说明"
        roster = session.get(ChapterCharacterRoster, ids["roster"])
        assert json.loads(roster.candidates_json)[0]["canonical_name"] == "浅村优太"
        assert roster.version == 2
        # Even stale model slots cannot overwrite a user-edited book identity.
        slot = SpeakerSlot(
            display_label="S1",
            first_quote_id=group.first_quote_id,
            group_id=group.id,
            character_id=group.character_id,
            canonical_name="模型旧名",
            description="模型旧说明",
        )
        _ensure_group(session, state=SceneState(), slot=slot, scene_id=group.scene_id)
        assert group.canonical_name == "浅村优太" and group.description == "用户说明"
    response = client.get(f"/api/books/{ids['book']}/annotations?start_cp=0&end_cp=15")
    assert response.status_code == 200, response.text
    projection = response.json()["data"]
    assert projection["items"][0]["label"] == "浅村优太"
    assert projection["items"][0]["speaker_description"] == "用户说明"
    assert projection["legend"][0]["description"] == "用户说明"
    promoted = client.put(
        f"{base}/speaker:{ids['unlinked']}",
        json={
            "name": "轻浮男客",
            "aliases": [],
            "description": "男客说明",
            "expected_version": 1,
        },
    )
    assert promoted.status_code == 200, promoted.text
    with transaction(client.app.state.session_factory) as session:
        group = session.get(SpeakerGroup, ids["unlinked"])
        assert group.character_id == promoted.json()["data"]["character_id"]
    assert all(row["kind"] == "book" for row in client.get(base).json()["data"])


def test_merge_rewrites_roster_pov_groups_and_checkpoint(migrated_client, populated):
    client, ids = migrated_client, populated
    with transaction(client.app.state.session_factory) as session:
        job = Job(
            book_id=ids["book"],
            book_version_id=ids["version"],
            kind=JobKind.INFERENCE,
            state=JobState.PAUSED,
            checkpoint_json=json.dumps(
                {
                    "scene_state": {
                        "pov_character_id": ids["source"],
                        "participants": [
                            {
                                "character_id": ids["source"],
                                "canonical_name": "旧名",
                                "description": "旧说明",
                            },
                        ],
                    },
                }
            ),
        )
        session.add(job)
        session.flush()
        job_id = job.id
    base = f"/api/books/{ids['book']}/character-directory"
    response = client.post(
        f"{base}/{ids['source']}/merge",
        json={
            "target_character_id": ids["target"],
            "expected_version": 1,
            "expected_target_version": 1,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["aliases"] == ["浅村悠太", "哥哥"]
    with transaction(client.app.state.session_factory) as session:
        assert session.get(BookCharacter, ids["source"]) is None
        group = session.get(SpeakerGroup, ids["linked"])
        assert group.character_id == ids["target"]
        assert group.canonical_name == "悠太" and group.description == "目标说明"
        roster = session.get(ChapterCharacterRoster, ids["roster"])
        assert roster.pov_character_id == ids["target"]
        assert json.loads(roster.confirmed_character_ids_json) == [ids["target"]]
        checkpoint = json.loads(session.get(Job, job_id).checkpoint_json)["scene_state"]
        assert checkpoint["pov_character_id"] == ids["target"]
        assert checkpoint["participants"][0]["character_id"] == ids["target"]
    assert len(client.get(base).json()["data"]) == 2
    response = client.post(
        f"{base}/speaker:{ids['unlinked']}/merge",
        json={
            "target_character_id": ids["target"],
            "expected_version": 1,
            "expected_target_version": 2,
        },
    )
    assert response.status_code == 200, response.text
    assert len(client.get(base).json()["data"]) == 1


def test_directory_rejects_stale_cross_book_and_active_job_edits(migrated_client, populated):
    client, ids = migrated_client, populated
    base = f"/api/books/{ids['book']}/character-directory"
    payload = {"name": "新名", "expected_version": 99}
    assert client.put(f"{base}/{ids['source']}", json=payload).status_code == 409
    payload["expected_version"] = 1
    assert client.put(f"{base}/{ids['source']}", json={**payload, "name": " "}).status_code == 422
    assert (
        client.post(
            f"{base}/{ids['source']}/merge",
            json={
                "target_character_id": ids["source"],
                "expected_version": 1,
                "expected_target_version": 1,
            },
        ).status_code
        == 422
    )
    other = client.post(
        "/api/books/import",
        files={
            "file": ("other.txt", "另一章\n「你好。」".encode(), "text/plain"),
        },
    ).json()["data"]["book_id"]
    assert (
        client.put(
            f"/api/books/{other}/character-directory/{ids['source']}", json=payload
        ).status_code
        == 404
    )
    with transaction(client.app.state.session_factory) as session:
        session.add(
            Job(
                book_id=ids["book"],
                book_version_id=ids["version"],
                kind=JobKind.INFERENCE,
                state=JobState.RUNNING,
            )
        )
    response = client.put(f"{base}/{ids['source']}", json=payload)
    assert response.status_code == 409 and "停止任务" in response.json()["error"]["message"]
    with transaction(client.app.state.session_factory) as session:
        assert (
            session.scalar(
                select(BookCharacter).where(BookCharacter.id == ids["source"])
            ).canonical_name
            == "浅村悠太"
        )
