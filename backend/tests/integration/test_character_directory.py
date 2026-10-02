from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from ndr.domain.enums import (
    AnnotationSource,
    AnnotationStatus,
    CharacterRosterStatus,
    JobKind,
    JobState,
    QuoteKind,
)
from ndr.jobs.scheduler import run_job
from ndr.llm.adapters.fake import FakeProviderAdapter
from ndr.llm.errors import ProviderError, ProviderErrorKind
from ndr.scenes.engine import _ensure_group
from ndr.scenes.state import SceneState, SpeakerSlot
from ndr.storage.models import (
    Annotation,
    BookCharacter,
    BookVersion,
    Chapter,
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


def test_directory_counts_current_appearances_and_sorts_main_characters(migrated_client, populated):
    from ndr.characters.auto_merge import _snapshot
    from ndr.characters.directory import directory

    client, ids = migrated_client, populated
    base = f"/api/books/{ids['book']}/character-directory"
    initial = client.get(base).json()["data"]
    assert [row["character_id"] for row in initial] == [
        ids["source"], ids["target"], f"speaker:{ids['unlinked']}",
    ]
    assert (initial[0]["chapter_count"], initial[0]["dialogue_count"]) == (1, 1)
    assert (initial[1]["chapter_count"], initial[1]["dialogue_count"]) == (1, 0)
    with transaction(client.app.state.session_factory) as session:
        version = session.get(BookVersion, ids["version"])
        before = _snapshot(session, version)
        roster = session.get(ChapterCharacterRoster, ids["roster"])
        chapter = Chapter(book_version_id=version.id, ordinal=999, start_cp=100, end_cp=200)
        session.add(chapter)
        session.flush()
        session.add(ChapterCharacterRoster(
            book_version_id=version.id, chapter_id=chapter.id,
            status=CharacterRosterStatus.CONFIRMED,
            confirmed_character_ids_json=json.dumps([ids["target"], ids["target"]]),
        ))
        group = session.get(SpeakerGroup, ids["linked"])
        second = SpeakerGroup(scene_id=group.scene_id, display_label="S3",
                              character_id=ids["source"], canonical_name="浅村悠太")
        session.add(second)
        session.flush()
        for index, (status, stale, kind, speaker) in enumerate([
            (AnnotationStatus.ACCEPTED, False, QuoteKind.SPEECH, second.id),
            (AnnotationStatus.PROVISIONAL, False, QuoteKind.SPEECH, second.id),
            (AnnotationStatus.ACCEPTED, True, QuoteKind.SPEECH, second.id),
            (AnnotationStatus.UNKNOWN, False, QuoteKind.SPEECH, second.id),
            (AnnotationStatus.ACCEPTED, False, QuoteKind.THOUGHT, second.id),
            (AnnotationStatus.ACCEPTED, False, QuoteKind.SPEECH, None),
            (AnnotationStatus.ACCEPTED, False, QuoteKind.SPEECH, ids["unlinked"]),
        ]):
            quote = Quote(book_version_id=version.id, chapter_id=roster.chapter_id,
                          start_cp=300 + index * 10, end_cp=305 + index * 10,
                          delimiter="「」", scanner_version="statistics-test")
            session.add(quote)
            session.flush()
            session.add(Annotation(quote_id=quote.id, scene_id=group.scene_id,
                                   speaker_id=speaker, kind=kind, source=AnnotationSource.MODEL,
                                   status=status, stale=stale))
        session.flush()
        assert _snapshot(session, version) == before  # Counts are not identity edits.
        statements = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(session.bind, "before_cursor_execute", capture)
        try:
            result = directory(session, version)
        finally:
            event.remove(session.bind, "before_cursor_execute", capture)
        assert len(statements) == 4  # Constant query budget, no per-person queries.
        assert [row.character_id for row in result] == [
            ids["target"], ids["source"], f"speaker:{ids['unlinked']}",
        ]
        assert [(row.chapter_count, row.dialogue_count) for row in result] == [
            (2, 0), (1, 3), (1, 1),
        ]


def test_directory_merge_deduplicates_shared_chapters(migrated_client, populated):
    client, ids = migrated_client, populated
    base = f"/api/books/{ids['book']}/character-directory"
    response = client.post(f"{base}/{ids['source']}/merge", json={
        "target_character_id": ids["target"], "expected_version": 1,
        "expected_target_version": 1,
    })
    assert response.status_code == 200, response.text
    listed = client.get(base).json()["data"]
    assert listed[0]["character_id"] == ids["target"]
    assert (listed[0]["chapter_count"], listed[0]["dialogue_count"]) == (1, 1)


@pytest.mark.parametrize("automatic", [False, True])
def test_merge_and_edit_preserve_initial_names_descriptions_and_separate_colors(
    migrated_client, populated, automatic,
):
    from ndr.characters.visibility import capture

    client, ids = migrated_client, populated
    base = f"/api/books/{ids['book']}/character-directory"
    with transaction(client.app.state.session_factory) as session:
        version = session.get(BookVersion, ids["version"])
        boundary = version.canonical_length_cp - 1
        source = session.get(BookCharacter, ids["source"])
        source.canonical_name = "女神"
        source.description = "早期身份"
        target = session.get(BookCharacter, ids["target"])
        target.canonical_name = "阿库娅"
        target.description = "最终身份"
        first = session.get(SpeakerGroup, ids["linked"])
        first.canonical_name = source.canonical_name
        first.description = source.description
        second = session.get(SpeakerGroup, ids["unlinked"])
        second.character_id = target.id
        second.canonical_name = target.canonical_name
        second.description = target.description
        chapter_id = session.get(ChapterCharacterRoster, ids["roster"]).chapter_id
        quote = Quote(book_version_id=version.id, chapter_id=chapter_id, start_cp=0,
                      end_cp=1, delimiter="「」", scanner_version="visibility-test")
        session.add(quote)
        session.flush()
        session.add(Annotation(quote_id=quote.id, scene_id=second.scene_id, speaker_id=second.id,
                               kind=QuoteKind.SPEECH, status=AnnotationStatus.ACCEPTED,
                               source=AnnotationSource.MODEL, visible_from_cp=0))
        for row in (source, target, first, second):
            capture(row, 0)
    if automatic:
        job, _ = _auto_job(client, ids)
        outcome, _ = _run_merge(client, job, [_merge_group(ids)], accept=False)
        assert outcome.state is JobState.COMPLETED
        response = client.post(f"{base}/auto-merge/{job['id']}/confirm", json={
            "selected_target_ids": [ids["target"]], "visible_from_cp": boundary,
        })
    else:
        response = client.post(f"{base}/{ids['source']}/merge", json={
            "target_character_id": ids["target"], "expected_version": 1,
            "expected_target_version": 1, "visible_from_cp": boundary,
        })
    assert response.status_code == 200, response.text
    url = f"/api/books/{ids['book']}/annotations"
    early = client.get(url, params={"reading_mode": "initial",
                                   "visible_horizon_cp": boundary - 1}).json()["data"]
    assert {item["label"] for item in early["items"]} == {"女神", "阿库娅"}
    assert len({item["color_index"] for item in early["items"]}) == 2
    goddess = next(item for item in early["items"] if item["label"] == "女神")
    assert goddess["speaker_description"] == "早期身份"
    late = client.get(url, params={"reading_mode": "initial",
                                  "visible_horizon_cp": boundary}).json()["data"]
    assert {item["label"] for item in late["items"]} == {"阿库娅"}
    assert len({item["color_index"] for item in late["items"]}) == 1
    target = next(row for row in client.get(base).json()["data"]
                  if row["character_id"] == ids["target"])
    response = client.put(f"{base}/{ids['target']}", json={
        "name": "后来的名称", "description": "后来的说明", "expected_version": target["version"],
    })
    assert response.status_code == 200, response.text
    assert {item["label"] for item in client.get(url, params={
        "reading_mode": "initial", "visible_horizon_cp": boundary,
    }).json()["data"]["items"]} == {"阿库娅"}
    final = client.get(url, params={"reading_mode": "reread"}).json()["data"]
    assert {item["label"] for item in final["items"]} == {"后来的名称"}
    assert all(item["speaker_description"] == "后来的说明" for item in final["items"])
    assert "presentation_history" not in json.dumps(early)


def test_legacy_visibility_migration_is_conservative_and_preserves_reread(
    migrated_client, populated,
):
    from alembic import command

    from ndr.storage.migrate import build_alembic_config, run_migrations

    client, ids = migrated_client, populated
    command.downgrade(build_alembic_config(client.app.state.settings), "0017")
    run_migrations(client.app.state.settings)
    with transaction(client.app.state.session_factory) as session:
        version = session.get(BookVersion, ids["version"])
        group = session.get(SpeakerGroup, ids["linked"])
        history = json.loads(group.presentation_history_json)
        assert history[0]["cp"] == version.canonical_length_cp
        assert group.canonical_name == "浅村悠太"
        horizon = version.canonical_length_cp - 1
    url = f"/api/books/{ids['book']}/annotations"
    early = client.get(url, params={"visible_horizon_cp": horizon}).json()["data"]
    assert all(item["label"] == "未确认说话人" for item in early["items"])
    assert all(item["speaker_description"] == "" for item in early["items"])
    reread = client.get(url, params={"reading_mode": "reread"}).json()["data"]
    assert all(item["label"] == "浅村悠太" for item in reread["items"])


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


def _auto_job(client, ids, **overrides):
    profile = ids.get("profile")
    if profile is None:
        profile = client.post(
            "/api/model-profiles",
            json={
                "name": "合并测试",
                "protocol": "chat-completions-compatible",
                "base_url": "http://127.0.0.1:1",
                "model": "test",
                "credential_mode": "none",
            },
        ).json()["data"]["id"]
        ids["profile"] = profile
    body = {
        "book_version_id": ids["version"],
        "profile_id": profile,
        "idempotency_key": f"auto-{ids['book']}",
        "run_now": False,
        **overrides,
    }
    response = client.post(f"/api/books/{ids['book']}/character-directory/auto-merge", json=body)
    assert response.status_code == 202, response.text
    return response.json()["data"], body


def _merge_group(ids, **overrides):
    return {
        "target_id": ids["target"],
        "source_ids": [ids["source"]],
        "confidence": 0.99,
        "reason": "姓名及哥哥别名指向同一人物",
        "merged_description": "悠太，别名哥哥，与原记录中的浅村悠太为同一人物。",
        **overrides,
    }


def _run_merge(client, job, groups, adapter=None, accept=True):
    adapter = adapter or FakeProviderAdapter(
        script=[{"groups": groups}], usage={"input_tokens": 30, "output_tokens": 10}
    )
    outcome = run_job(
        client.app.state.session_factory,
        client.app.state.settings,
        job_id=job["id"],
        adapter_factory=lambda *_: adapter,
    )
    if accept and outcome.state is JobState.COMPLETED:
        base = f"/api/books/{job['book_id']}/character-directory/auto-merge/{job['id']}"
        preview = client.get(base).json()["data"]
        if preview["phase"] == "awaiting_confirmation":
            response = client.post(
                f"{base}/confirm",
                json={
                    "selected_target_ids": [
                        group["target"]["character_id"] for group in preview["proposals"]
                    ]
                },
            )
            assert response.status_code == 200, response.text
    return outcome, adapter


@pytest.mark.parametrize("mode", ["empty", "low_confidence", "unnamed"])
def test_empty_auto_merge_finishes_without_confirmation_or_person_changes(
    migrated_client, populated, mode,
):
    client, ids = migrated_client, populated
    if mode == "unnamed":
        with transaction(client.app.state.session_factory) as session:
            for character_id in (ids["target"], ids["source"]):
                session.get(BookCharacter, character_id).canonical_name = ""
    before = client.get(f"/api/books/{ids['book']}/character-directory").json()["data"]
    job, payload = _auto_job(client, ids)
    groups = [] if mode == "empty" else [_merge_group(
        ids, confidence=0.9 if mode == "low_confidence" else 0.99,
    )]
    outcome, adapter = _run_merge(client, job, groups)
    assert outcome.state is JobState.COMPLETED and len(adapter.calls) == 1
    result = client.get(
        f"/api/books/{ids['book']}/character-directory/auto-merge/{job['id']}",
    ).json()["data"]
    assert result["phase"] == "no_suggestions" and result["proposals"] == []
    assert result["merged_count"] == 0 and result["usage"]["total_tokens"] == 40
    assert result["skipped_groups"] == (0 if mode == "empty" else 1)
    assert client.get(f"/api/books/{ids['book']}/character-directory").json()["data"] == before
    with transaction(client.app.state.session_factory) as session:
        row = session.get(Job, job["id"])
        assert json.loads(row.checkpoint_json)["phase"] == "no_suggestions"
        assert json.loads(row.progress_json)["stage"] == "no_suggestions"
    # A new explicitly requested analysis can be queued without discarding the empty one.
    response = client.post(f"/api/books/{ids['book']}/character-directory/auto-merge", json={
        **payload, "idempotency_key": f"{payload['idempotency_key']}-again",
    })
    assert response.status_code == 202, response.text
    assert response.json()["data"]["id"] != job["id"]


@pytest.mark.parametrize("state,phase,explicit_empty,expected", [
    (JobState.COMPLETED, "awaiting_confirmation", True, "no_suggestions"),
    (JobState.COMPLETED, "awaiting_confirmation", False, "awaiting_confirmation"),
    (JobState.RUNNING, "awaiting_confirmation", True, "awaiting_confirmation"),
    (JobState.FAILED, "awaiting_confirmation", True, "awaiting_confirmation"),
    (JobState.BUDGET_EXHAUSTED, "awaiting_confirmation", True, "awaiting_confirmation"),
    (JobState.COMPLETED, "applied", True, "applied"),
    (JobState.COMPLETED, "discarded", True, "discarded"),
])
def test_legacy_empty_plan_compatibility_is_read_only(
    migrated_client, populated, state, phase, explicit_empty, expected,
):
    client, ids = migrated_client, populated
    job, _ = _auto_job(client, ids)
    with transaction(client.app.state.session_factory) as session:
        row = session.get(Job, job["id"])
        row.state = state
        row.checkpoint_json = json.dumps({"phase": phase, "merge_proposal":
                                         {"groups": []} if explicit_empty else {}})
        session.flush()
        before = row.checkpoint_json, row.updated_at
    result = client.get(
        f"/api/books/{ids['book']}/character-directory/auto-merge/{job['id']}",
    )
    assert result.status_code == 200, result.text
    assert result.json()["data"]["phase"] == expected
    with transaction(client.app.state.session_factory) as session:
        row = session.get(Job, job["id"])
        assert (row.checkpoint_json, row.updated_at) == before


@pytest.mark.parametrize("decision", ["accept", "discard", "changed", "unknown"])
def test_auto_merge_preview_requires_explicit_confirmation(migrated_client, populated, decision):
    client, ids = migrated_client, populated
    job, _ = _auto_job(client, ids)
    outcome, adapter = _run_merge(client, job, [_merge_group(ids)], accept=False)
    assert outcome.state is JobState.COMPLETED
    base = f"/api/books/{ids['book']}/character-directory/auto-merge/{job['id']}"
    preview = client.get(base).json()["data"]
    assert preview["phase"] == "awaiting_confirmation" and preview["merged_count"] == 0
    assert preview["proposals"][0]["target"]["character_id"] == ids["target"]
    assert preview["proposals"][0]["sources"][0]["aliases"] == ["哥哥"]
    with transaction(client.app.state.session_factory) as session:
        assert session.get(BookCharacter, ids["source"]) is not None
        assert session.get(SpeakerGroup, ids["linked"]).character_id == ids["source"]
        if decision == "changed":
            session.get(BookCharacter, ids["source"]).description = "修改了人物资料"
    body = {
        "selected_target_ids": []
        if decision == "discard"
        else ["unknown" if decision == "unknown" else ids["target"]]
    }
    response = client.post(f"{base}/confirm", json=body)
    if decision in {"changed", "unknown"}:
        assert response.status_code in {400, 409, 422}
        assert client.get(base).json()["data"]["phase"] == "awaiting_confirmation"
    else:
        assert response.status_code == 200, response.text
        result = response.json()["data"]
        assert result["phase"] == ("applied" if decision == "accept" else "discarded")
        assert result["merged_count"] == (1 if decision == "accept" else 0)
        assert client.post(f"{base}/confirm", json=body).json()["data"] == result
        assert (
            client.post(
                f"{base}/confirm",
                json={"selected_target_ids": [ids["target"]] if decision == "discard" else []},
            ).status_code
            == 409
        )
    assert len(adapter.calls) == 1
    with transaction(client.app.state.session_factory) as session:
        assert (session.get(BookCharacter, ids["source"]) is None) == (decision == "accept")


@pytest.mark.parametrize("select_all", [False, True])
def test_auto_merge_confirmation_applies_only_selected_groups(
    migrated_client, populated, select_all
):
    client, ids = migrated_client, populated
    with transaction(client.app.state.session_factory) as session:
        target = BookCharacter(book_version_id=ids["version"], canonical_name="沙季")
        source = BookCharacter(book_version_id=ids["version"], canonical_name="绫濑沙季")
        session.add_all([target, source])
        session.flush()
        second_target, second_source = target.id, source.id
    job, _ = _auto_job(client, ids)
    second = _merge_group(ids, target_id=second_target, source_ids=[second_source])
    _run_merge(client, job, [_merge_group(ids), second], accept=False)
    response = client.post(
        f"/api/books/{ids['book']}/character-directory/auto-merge/{job['id']}/confirm",
        json={
            "selected_target_ids": [ids["target"], second_target] if select_all else [ids["target"]]
        },
    )
    assert response.status_code == 200 and response.json()["data"]["merged_count"] == (
        2 if select_all else 1
    )
    with transaction(client.app.state.session_factory) as session:
        assert session.get(BookCharacter, ids["source"]) is None
        assert (session.get(BookCharacter, second_source) is None) == select_all


@pytest.mark.parametrize("all_unnamed", [False, True])
def test_auto_merge_never_uses_an_unnamed_target(migrated_client, populated, all_unnamed):
    client, ids = migrated_client, populated
    with transaction(client.app.state.session_factory) as session:
        session.get(BookCharacter, ids["target"]).canonical_name = ""
        if all_unnamed:
            session.get(BookCharacter, ids["source"]).canonical_name = ""
    job, _ = _auto_job(client, ids)
    _run_merge(client, job, [_merge_group(ids)], accept=False)
    base = f"/api/books/{ids['book']}/character-directory/auto-merge/{job['id']}"
    preview = client.get(base).json()["data"]
    if all_unnamed:
        assert preview["proposals"] == [] and preview["skipped_groups"] == 1
    else:
        assert preview["proposals"][0]["target"]["character_id"] == ids["source"]
        response = client.post(f"{base}/confirm", json={"selected_target_ids": [ids["source"]]})
        assert response.status_code == 200 and response.json()["data"]["merged_count"] == 1
        with transaction(client.app.state.session_factory) as session:
            assert session.get(BookCharacter, ids["target"]) is None
            assert session.get(BookCharacter, ids["source"]).canonical_name == "浅村悠太"


def test_auto_merge_applies_and_preserves_names_descriptions_references_usage(
    migrated_client, populated
):
    client, ids = migrated_client, populated
    with transaction(client.app.state.session_factory) as session:
        session.get(BookCharacter, ids["target"]).description = "用户确认的主人公"
        session.get(BookCharacter, ids["source"]).description = "书店店员"
    job, payload = _auto_job(client, ids)
    # A queued merge locks manual writes and creates no provider usage yet.
    base = f"/api/books/{ids['book']}/character-directory"
    blocked = client.put(
        f"{base}/{ids['source']}",
        json={
            "name": "另一个名字",
            "expected_version": 1,
        },
    )
    assert blocked.status_code == 409
    summary = "悠太，故事的主人公，在书店打工，别名哥哥。"
    outcome, adapter = _run_merge(client, job, [_merge_group(ids, merged_description=summary)])
    assert outcome.state is JobState.COMPLETED
    result = client.get(f"{base}/auto-merge/{job['id']}").json()["data"]
    assert result["merged_count"] == 1
    assert result["merges"][0]["source_names"] == ["浅村悠太"]
    assert result["usage"]["total_tokens"] == 40
    assert client.get(f"{base}/auto-merge").json()["data"] == result
    rows = client.get(base).json()["data"]
    target = next(row for row in rows if row["character_id"] == ids["target"])
    assert target["description"] == summary
    assert {"浅村悠太", "哥哥"}.issubset(target["aliases"])
    with transaction(client.app.state.session_factory) as session:
        assert session.get(BookCharacter, ids["source"]) is None
        assert session.get(SpeakerGroup, ids["linked"]).character_id == ids["target"]
        assert session.get(ChapterCharacterRoster, ids["roster"]).pov_character_id == ids["target"]
    repeat = client.post(f"{base}/auto-merge", json=payload)
    assert repeat.json()["data"]["id"] == job["id"]
    _run_merge(client, job, [], adapter)
    assert len(adapter.calls) == 1
    assert adapter.calls[0]["payload"]["max_tokens_override"] == 4096


@pytest.mark.parametrize("case", ["unknown", "overlap", "self", "extra"])
def test_auto_merge_rejects_invalid_plan_atomically_and_keeps_usage(
    migrated_client, populated, case
):
    client, ids = migrated_client, populated
    job, _ = _auto_job(client, ids)
    groups = [_merge_group(ids)]
    if case == "unknown":
        groups.append(_merge_group(ids, target_id="not-a-character"))
    elif case == "overlap":
        groups.append(_merge_group(ids))
    elif case == "self":
        groups[0]["source_ids"] = [ids["target"]]
    else:
        groups[0]["rename_to"] = "不能修改姓名"
    outcome, _ = _run_merge(client, job, groups)
    assert outcome.state is JobState.FAILED
    result = client.get(
        f"/api/books/{ids['book']}/character-directory/auto-merge/{job['id']}"
    ).json()["data"]
    assert result["merged_count"] == 0 and result["usage"]["total_tokens"] == 40
    assert result["validation_issues"]
    detail = client.get(f"/api/jobs/{job['id']}").json()["data"]
    assert len(detail["checkpoint"]["model_groups"]) == len(groups)
    assert detail["checkpoint"]["character_refs"]
    assert detail["checkpoint"]["validation_issues"] == result["validation_issues"]
    if case != "extra":
        assert "第" in result["last_error"] and "未执行合并" in result["last_error"]
    with transaction(client.app.state.session_factory) as session:
        assert session.get(BookCharacter, ids["source"]) is not None


def test_auto_merge_short_refs_reach_preview_as_stable_ids(migrated_client, populated):
    client, ids = migrated_client, populated
    job, _ = _auto_job(client, ids)
    with transaction(client.app.state.session_factory) as session:
        entries = json.loads(session.get(Job, job["id"]).range_json)["entries"]
    refs = {row["character_id"]: f"C{i + 1}" for i, row in enumerate(entries)}
    outcome, adapter = _run_merge(client, job, [_merge_group(
        ids, target_id=refs[ids["target"]], source_ids=[refs[ids["source"]]],
    )], accept=False)
    assert outcome.state is JobState.COMPLETED
    payload = json.loads(adapter.calls[0]["payload"]["messages"][1]["content"])
    assert all(row["character_id"].startswith("C") for row in payload["characters"])
    base = f"/api/books/{ids['book']}/character-directory/auto-merge/{job['id']}"
    preview = client.get(base).json()["data"]
    assert preview["proposals"][0]["target"]["character_id"] == ids["target"]
    assert preview["proposals"][0]["sources"][0]["character_id"] == ids["source"]
    assert client.post(f"{base}/confirm", json={"selected_target_ids": [ids["target"]]}).status_code == 200
    assert len(adapter.calls) == 1


@pytest.mark.parametrize("role,name", [
    ("女神", "阿库娅"), ("女骑士", "达克妮丝"), ("无头骑士", "贝尔迪亚"),
])
def test_auto_merge_previews_alias_only_name_upgrade(migrated_client, populated, role, name):
    client, ids = migrated_client, populated
    with transaction(client.app.state.session_factory) as session:
        character = session.get(BookCharacter, ids["target"])
        character.canonical_name = role
        character.aliases_json = json.dumps([name, role], ensure_ascii=False)
    job, _ = _auto_job(client, ids)
    outcome, adapter = _run_merge(client, job, [_merge_group(
        ids, source_ids=[], preferred_name=name,
    )], accept=False)
    assert outcome.state is JobState.COMPLETED
    base = f"/api/books/{ids['book']}/character-directory/auto-merge/{job['id']}"
    proposal = client.get(base).json()["data"]["proposals"][0]
    assert proposal["preferred_name"] == name and proposal["sources"] == []
    with transaction(client.app.state.session_factory) as session:
        assert session.get(BookCharacter, ids["target"]).canonical_name == role
    response = client.post(f"{base}/confirm", json={"selected_target_ids": [ids["target"]]})
    assert response.status_code == 200, response.text
    with transaction(client.app.state.session_factory) as session:
        character = session.get(BookCharacter, ids["target"])
        assert character.canonical_name == name
        assert role in json.loads(character.aliases_json)
        assert name not in json.loads(character.aliases_json)
    assert len(adapter.calls) == 1


def test_auto_merge_previews_all_three_revealed_names_without_paid_retry(migrated_client, populated):
    client, ids = migrated_client, populated
    roles = ["女骑士", "女神", "无头骑士"]
    names = ["达克妮丝", "阿库娅", "贝尔迪亚"]
    with transaction(client.app.state.session_factory) as session:
        characters = [session.get(BookCharacter, ids[key]) for key in ("target", "source")]
        characters.append(BookCharacter(book_version_id=ids["version"]))
        session.add(characters[-1])
        for character, role, name in zip(characters, roles, names, strict=True):
            character.canonical_name = role
            character.aliases_json = json.dumps([role, name], ensure_ascii=False)
        session.flush()
        character_ids = [character.id for character in characters]
    job, _ = _auto_job(client, ids)
    with transaction(client.app.state.session_factory) as session:
        entries = json.loads(session.get(Job, job["id"]).range_json)["entries"]
    refs = {entry["character_id"]: f"C{i + 1}" for i, entry in enumerate(entries)}
    outcome, adapter = _run_merge(client, job, [
        _merge_group(ids, target_id=refs[key], source_ids=[], preferred_name=name,
                     reason=f"本组别名已提供真实姓名{name}",
                     merged_description=f"{name}，原称{role}。")
        for key, role, name in zip(character_ids, roles, names, strict=True)
    ], accept=False)
    assert outcome.state is JobState.COMPLETED
    base = f"/api/books/{ids['book']}/character-directory/auto-merge/{job['id']}"
    preview = client.get(base).json()["data"]
    assert preview["phase"] == "awaiting_confirmation"
    assert [row["preferred_name"] for row in preview["proposals"]] == names
    assert preview["validation_issues"] == []
    with transaction(client.app.state.session_factory) as session:
        assert [session.get(BookCharacter, key).canonical_name for key in character_ids] == roles
    response = client.post(f"{base}/confirm", json={"selected_target_ids": character_ids})
    assert response.status_code == 200, response.text
    assert {row["target_character_id"]: row["target_name"]
            for row in response.json()["data"]["merges"]} == dict(
        zip(character_ids, names, strict=True),
    )
    with transaction(client.app.state.session_factory) as session:
        for key, role, name in zip(character_ids, roles, names, strict=True):
            character = session.get(BookCharacter, key)
            assert character.canonical_name == name
            assert role in json.loads(character.aliases_json)
    assert len(adapter.calls) == 1


@pytest.mark.parametrize("locked,role,name,problem", [
    (True, "女神", "阿库娅", "由用户指定"),
    (False, "女神", "虚构姓名", "不在本组已提供"),
    (False, "贝尔迪亚", "阿库娅", "未被识别为身份代称"),
    (False, "女神", "无头骑士", "仍是身份代称"),
])
def test_auto_merge_rejects_invalid_rename_with_specific_reason(
    migrated_client, populated, locked, role, name, problem,
):
    client, ids = migrated_client, populated
    with transaction(client.app.state.session_factory) as session:
        character = session.get(BookCharacter, ids["target"])
        character.canonical_name = role
        character.aliases_json = '["阿库娅", "无头骑士"]'
        character.name_locked = locked
    job, _ = _auto_job(client, ids)
    outcome, _ = _run_merge(client, job, [_merge_group(
        ids, source_ids=[], preferred_name=name,
    )], accept=False)
    assert outcome.state is JobState.FAILED
    result = client.get(
        f"/api/books/{ids['book']}/character-directory/auto-merge/{job['id']}"
    ).json()["data"]
    assert problem in result["last_error"]
    assert result["validation_issues"][0]["code"] == "invalid_preferred_name"
    with transaction(client.app.state.session_factory) as session:
        assert session.get(BookCharacter, ids["target"]).canonical_name == role


def test_auto_merge_analyzes_single_character_for_name_upgrade(migrated_client):
    client = migrated_client
    imported = client.post("/api/books/import", files={
        "file": ("single.txt", "第一章\n「我是阿库娅。」".encode(), "text/plain"),
    }).json()["data"]
    with transaction(client.app.state.session_factory) as session:
        character = BookCharacter(
            book_version_id=imported["book_version_id"], canonical_name="女神",
            aliases_json='["阿库娅"]',
        )
        session.add(character)
        session.flush()
        ids = {"book": imported["book_id"], "version": imported["book_version_id"],
               "target": character.id, "source": character.id}
    job, _ = _auto_job(client, ids)
    outcome, adapter = _run_merge(client, job, [_merge_group(
        ids, source_ids=[], preferred_name="阿库娅",
    )])
    assert outcome.state is JobState.COMPLETED and len(adapter.calls) == 1
    with transaction(client.app.state.session_factory) as session:
        assert session.get(BookCharacter, ids["target"]).canonical_name == "阿库娅"


@pytest.mark.parametrize("case", ["edited", "new_job", "stop"])
def test_auto_merge_does_not_hold_transaction_during_model_call_and_rechecks_state(
    migrated_client, populated, case
):
    client, ids = migrated_client, populated
    job, _ = _auto_job(client, ids)

    class ChangingAdapter(FakeProviderAdapter):
        async def generate_labels(self, payload):
            raw = await super().generate_labels(payload)
            with transaction(client.app.state.session_factory) as session:
                if case == "edited":
                    person = session.get(BookCharacter, ids["source"])
                    person.description = "已经被更正"
                    person.version += 1
                elif case == "new_job":
                    session.add(
                        Job(
                            book_id=ids["book"],
                            book_version_id=ids["version"],
                            kind=JobKind.INFERENCE,
                            state=JobState.QUEUED,
                        )
                    )
                else:
                    session.get(Job, job["id"]).state = JobState.PAUSING
            return raw

    adapter = ChangingAdapter(script=[{"groups": [_merge_group(ids)]}], usage={"total_tokens": 40})
    outcome, _ = _run_merge(client, job, [], adapter)
    assert outcome.state is (JobState.PAUSED if case == "stop" else JobState.FAILED)
    with transaction(client.app.state.session_factory) as session:
        assert session.get(BookCharacter, ids["source"]) is not None
    result = client.get(
        f"/api/books/{ids['book']}/character-directory/auto-merge/{job['id']}"
    ).json()["data"]
    assert result["usage"]["total_tokens"] == 40


@pytest.mark.parametrize(
    "configured,limit,expected",
    [(128000, None, 128000), (2048, None, 2048), (None, None, 4096), (128000, 10000, None)],
)
def test_auto_merge_honours_profile_output_limit_and_optional_total_budget(
    migrated_client, populated, configured, limit, expected
):
    client, ids = migrated_client, populated
    job, _ = _auto_job(client, ids, max_total_tokens=limit)
    with transaction(client.app.state.session_factory) as session:
        stored = session.get(Job, job["id"])
        snapshot = json.loads(stored.profile_snapshot_json)
        snapshot["params"] = {"max_tokens": configured} if configured is not None else {}
        stored.profile_snapshot_json = json.dumps(snapshot)
    outcome, adapter = _run_merge(client, job, [])
    assert outcome.state is JobState.COMPLETED
    payload = adapter.calls[0]["payload"]
    if expected is None:
        assert 0 < payload["max_tokens_override"] < limit
        from ndr.context.budget import estimate_tokens

        assert (
            payload["max_tokens_override"]
            + sum(estimate_tokens(message["content"]) for message in payload["messages"])
            == limit
        )
    else:
        assert payload["max_tokens_override"] == expected
    assert payload["max_tokens"] == payload["max_tokens_override"]


def test_auto_merge_budget_prevents_call_and_low_confidence_is_not_merged(
    migrated_client, populated
):
    client, ids = migrated_client, populated
    job, _ = _auto_job(client, ids, max_total_tokens=1)
    outcome, adapter = _run_merge(client, job, [_merge_group(ids)])
    assert outcome.state is JobState.BUDGET_EXHAUSTED and not adapter.calls
    job, _ = _auto_job(client, ids, idempotency_key="low-confidence")
    outcome, _ = _run_merge(client, job, [_merge_group(ids, confidence=0.5)])
    assert outcome.state is JobState.COMPLETED
    result = client.get(
        f"/api/books/{ids['book']}/character-directory/auto-merge/{job['id']}"
    ).json()["data"]
    assert result["skipped_groups"] == 1 and result["merged_count"] == 0


def test_auto_merge_can_link_unassociated_speakers_and_never_replays_unknown(
    migrated_client, populated
):
    client, ids = migrated_client, populated
    job, _ = _auto_job(client, ids)
    group = _merge_group(ids, source_ids=[f"speaker:{ids['unlinked']}"])
    outcome, _ = _run_merge(client, job, [group])
    assert outcome.state is JobState.COMPLETED
    with transaction(client.app.state.session_factory) as session:
        assert session.get(SpeakerGroup, ids["unlinked"]).character_id == ids["target"]
    job, _ = _auto_job(client, ids, idempotency_key="timeout-merge")
    adapter = FakeProviderAdapter(script=[ProviderError(ProviderErrorKind.TIMEOUT, "请求超时")])
    outcome, _ = _run_merge(client, job, [], adapter)
    assert outcome.state is JobState.NEEDS_RECONCILIATION
    with transaction(client.app.state.session_factory) as session:
        session.get(Job, job["id"]).state = JobState.QUEUED
    outcome, _ = _run_merge(client, job, [], adapter)
    assert outcome.state is JobState.FAILED and len(adapter.calls) == 1


def test_auto_merge_promotes_unlinked_target_without_claiming_human_confirmation(
    migrated_client, populated
):
    client, ids = migrated_client, populated
    with transaction(client.app.state.session_factory) as session:
        target = session.get(SpeakerGroup, ids["unlinked"])
        source = SpeakerGroup(
            scene_id=target.scene_id,
            display_label="S3",
            canonical_name="轻浮男客",
            description="书店客人",
        )
        session.add(source)
        session.flush()
        source_id = source.id
    job, _ = _auto_job(client, ids)
    group = _merge_group(
        ids, target_id=f"speaker:{ids['unlinked']}", source_ids=[f"speaker:{source_id}"]
    )
    outcome, _ = _run_merge(client, job, [group])
    assert outcome.state is JobState.COMPLETED
    with transaction(client.app.state.session_factory) as session:
        target = session.get(SpeakerGroup, ids["unlinked"])
        assert target.character_id == session.get(SpeakerGroup, source_id).character_id
        person = session.get(BookCharacter, target.character_id)
        assert not person.user_confirmed
        assert "轻浮男客" in json.loads(person.aliases_json)


@pytest.mark.parametrize("unknown", [True, False])
def test_auto_merge_missing_or_excess_actual_usage_with_limit_does_not_apply(
    migrated_client, populated, unknown
):
    client, ids = migrated_client, populated
    job, _ = _auto_job(client, ids, max_total_tokens=100000)
    adapter = FakeProviderAdapter(
        script=[{"groups": [_merge_group(ids)]}],
        usage=None if unknown else {"total_tokens": 100001},
    )
    outcome, _ = _run_merge(client, job, [], adapter)
    assert outcome.state is JobState.BUDGET_EXHAUSTED
    with transaction(client.app.state.session_factory) as session:
        assert session.get(BookCharacter, ids["source"]) is not None


def test_auto_merge_synthesizes_long_original_descriptions_without_skipping(
    migrated_client, populated
):
    client, ids = migrated_client, populated
    with transaction(client.app.state.session_factory) as session:
        session.get(BookCharacter, ids["target"]).description = "甲" * 270
        session.get(BookCharacter, ids["source"]).description = "乙" * 270
    job, payload = _auto_job(client, ids)
    duplicate = client.post(
        f"/api/books/{ids['book']}/character-directory/auto-merge",
        json={**payload, "idempotency_key": "overlapping-merge"},
    )
    assert duplicate.status_code == 409
    summary = "人物的甲类特征与乙类经历共同构成同一身份。"
    outcome, adapter = _run_merge(client, job, [_merge_group(ids, merged_description=summary)],
                                  accept=False)
    assert outcome.state is JobState.COMPLETED
    result = client.get(
        f"/api/books/{ids['book']}/character-directory/auto-merge/{job['id']}"
    ).json()["data"]
    assert result["skipped_groups"] == 0 and result["merged_count"] == 0
    assert result["proposals"][0]["merged_description"] == summary
    # Preview leaves both original descriptions intact until explicit approval.
    with transaction(client.app.state.session_factory) as session:
        assert session.get(BookCharacter, ids["target"]).description == "甲" * 270
        assert session.get(BookCharacter, ids["source"]).description == "乙" * 270
    response = client.post(
        f"/api/books/{ids['book']}/character-directory/auto-merge/{job['id']}/confirm",
        json={"selected_target_ids": [ids["target"]]},
    )
    assert response.status_code == 200 and response.json()["data"]["merged_count"] == 1
    with transaction(client.app.state.session_factory) as session:
        assert session.get(BookCharacter, ids["target"]).description == summary
    assert len(adapter.calls) == 1
    recovery = client.get(f"/api/jobs/{job['id']}/recovery").json()["data"]
    assert recovery["actions"] == []


@pytest.mark.parametrize("summary", [None, "", "   ", "甲" * 513])
def test_auto_merge_rejects_invalid_synthesized_description_without_data_loss(
    migrated_client, populated, summary,
):
    client, ids = migrated_client, populated
    job, _ = _auto_job(client, ids)
    group = _merge_group(ids, merged_description=summary)
    if summary is None:
        group.pop("merged_description")
    outcome, adapter = _run_merge(client, job, [group])
    assert outcome.state is JobState.FAILED
    result = client.get(
        f"/api/books/{ids['book']}/character-directory/auto-merge/{job['id']}",
    ).json()["data"]
    assert "整理后人物说明" in result["last_error"]
    assert result["usage"]["total_tokens"] == 40
    with transaction(client.app.state.session_factory) as session:
        assert session.get(BookCharacter, ids["target"]).description == "目标说明"
        assert session.get(BookCharacter, ids["source"]) is not None
    assert len(adapter.calls) == 1


def test_old_merge_preview_requires_reanalysis_not_concatenation(migrated_client, populated):
    client, ids = migrated_client, populated
    job, _ = _auto_job(client, ids)
    _run_merge(client, job, [_merge_group(ids)], accept=False)
    with transaction(client.app.state.session_factory) as session:
        row = session.get(Job, job["id"])
        checkpoint = json.loads(row.checkpoint_json)
        checkpoint["merge_proposal"]["groups"][0].pop("merged_description")
        row.checkpoint_json = json.dumps(checkpoint)
    base = f"/api/books/{ids['book']}/character-directory/auto-merge/{job['id']}"
    assert client.get(base).json()["data"]["proposals"][0]["merged_description"] is None
    response = client.post(f"{base}/confirm", json={"selected_target_ids": [ids["target"]]})
    assert response.status_code == 422
    assert "重新分析" in response.text
    with transaction(client.app.state.session_factory) as session:
        assert session.get(BookCharacter, ids["source"]) is not None
    assert client.post(f"{base}/confirm", json={"selected_target_ids": []}).status_code == 200


def test_ten_fujinami_records_merge_with_one_synthesized_description(migrated_client, populated):
    client, ids = migrated_client, populated
    descriptions = [
        "暑期班坐在悠太隔壁的女生，身高约一百八十公分，在补习班认识；在高尔夫练习场再会并自我介绍为藤波夏帆。即前文名为高个子女生的同一人物。",
        "补习班学生，坐在自习室最后一排；读定时制高中，白天不去学校，喜欢打高尔夫；原文称她藤波同学，亦称夏帆同学，与浅村悠太一起买午餐并聊天。",
        "与浅村悠太深夜在涩谷散步并对话的女生；原文明示称呼为藤波同学，和悠太聊到涩谷暗巷、自己的过去等；在本片段中持续发言，是补习班认识的高个女生。",
        "藤波夏帆，补习班同学，邀悠太夜游；原文称藤波同学，全名藤波夏帆。此前被称作高个子女生，两人也在高尔夫练习场碰面；这里描述同一人的不同场景。",
        "在涩谷与浅村悠太对话，被称作藤波同学；双亲去世，曾被叔母抚养，现与养母同住，谈论心意与期待。与前文补习班坐在悠太旁边、喜欢高尔夫的女生对应。",
        "补习班坐在悠太旁边、让悠太觉得眼熟的高个女生，被叙述称为藤波同学，主动找悠太搭话并聊到涩谷与万圣夜。身份对应前文自报姓名的藤波夏帆。",
        "补习班同学，与悠太在教室对话；叙述明确说完，藤波同学扬起嘴角，指向其发言。她是早期的高个子女生，喜欢高尔夫，和悠太在涩谷有过夜游经历。",
        "藤波夏帆，补习班同学，带悠太在涩谷夜游；原文称藤波同学，也使用夏帆同学的称呼。她是定时制高中的学生，初见时原文只以高个子女生称呼她。",
    ]
    with transaction(client.app.state.session_factory) as session:
        target = session.get(BookCharacter, ids["target"])
        source = session.get(BookCharacter, ids["source"])
        target.canonical_name, target.description = "高个子女生", "补习班坐在悠太旁边的高个女生。"
        source.canonical_name, source.description = "藤波同学", "回忆中被称为藤波同学的女生。"
        scene_id = session.get(SpeakerGroup, ids["linked"]).scene_id
        groups = [SpeakerGroup(scene_id=scene_id, display_label=f"D{i}",
                               canonical_name="藤波夏帆" if i < 3 else "藤波同学",
                               description=description)
                  for i, description in enumerate(descriptions)]
        session.add_all(groups)
        relatives = [BookCharacter(book_version_id=target.book_version_id,
                                   canonical_name=name, aliases_json="[]")
                     for name in ["藤波夏帆的养母", "藤波夏帆的叔母"]]
        session.add_all(relatives)
        session.flush()
        source_ids = [ids["source"], *[f"speaker:{group.id}" for group in groups]]
        group_ids = [group.id for group in groups]
        relative_ids = [row.id for row in relatives]
    assert len("；".join(descriptions)) > 512
    summary = (
        "藤波夏帆，早期称高个子女生、藤波同学或夏帆同学。身高约一百八十公分，"
        "就读定时制高中，是浅村悠太的补习班同学，喜欢高尔夫，曾与悠太在涩谷夜游。"
        "双亲去世后曾由叔母抚养，现在与养母同住。"
    )
    job, _ = _auto_job(client, ids)
    outcome, adapter = _run_merge(client, job, [_merge_group(
        ids, source_ids=source_ids, merged_description=summary,
        reason="自我介绍及补习班、高尔夫、涩谷经历共同指向同一人物，亲属不是本人。",
    )], accept=False)
    assert outcome.state is JobState.COMPLETED
    base = f"/api/books/{ids['book']}/character-directory/auto-merge/{job['id']}"
    preview = client.get(base).json()["data"]
    assert len(preview["proposals"]) == 1 and preview["skipped_groups"] == 0
    assert len(preview["proposals"][0]["sources"]) == 9
    assert preview["proposals"][0]["merged_description"] == summary
    response = client.post(f"{base}/confirm", json={"selected_target_ids": [ids["target"]]})
    assert response.status_code == 200 and response.json()["data"]["merged_count"] == 9
    with transaction(client.app.state.session_factory) as session:
        assert session.get(BookCharacter, ids["target"]).description == summary
        assert all(session.get(SpeakerGroup, key).character_id == ids["target"] for key in group_ids)
        assert all(session.get(BookCharacter, key) is not None for key in relative_ids)
        assert len(json.loads(session.get(Job, job["id"]).checkpoint_json)["analysis_groups"]) == 1
    assert len(adapter.calls) == 1


@pytest.mark.parametrize(
    "state", [JobState.QUEUED, JobState.RUNNING, JobState.PARTIAL, JobState.COMPLETED]
)
def test_find_latest_auto_merge_without_client_storage(migrated_client, populated, state):
    client, ids = migrated_client, populated
    url = f"/api/books/{ids['book']}/character-directory/auto-merge"
    empty = client.get(url)
    assert empty.status_code == 200 and empty.json()["data"] is None
    job, _ = _auto_job(client, ids)
    with transaction(client.app.state.session_factory) as session:
        session.get(Job, job["id"]).state = state
        session.add(Job(kind=JobKind.EXPORT, book_id=ids["book"], book_version_id=ids["version"]))
    found = client.get(url).json()["data"]
    assert found["job_id"] == job["id"] and found["state"] == state.value
    assert client.get(url, params={"book_version_id": "not-this-book"}).status_code == 422
    with transaction(client.app.state.session_factory) as session:
        assert session.scalar(select(Job).where(Job.id == job["id"])).state is state


@pytest.mark.parametrize("kind", [JobKind.CHARACTER_ROSTER, JobKind.INFERENCE, JobKind.RECHECK])
def test_recent_jobs_restores_scoped_task_metadata_without_dispatch(
    migrated_client, populated, kind
):
    client, ids = migrated_client, populated
    with transaction(client.app.state.session_factory) as session:
        quote = session.scalar(select(Quote).where(Quote.book_version_id == ids["version"]))
        quote_id, chapter_id = quote.id, quote.chapter_id
        job = Job(
            kind=kind,
            book_id=ids["book"],
            book_version_id=ids["version"],
            state=JobState.RUNNING,
            idempotency_key="saved-request",
            range_json=json.dumps(
                {
                    "chapter_id": chapter_id,
                    "target_quote_id": quote_id,
                    "selected_window_ids": ["w1"],
                }
            ),
        )
        session.add(job)
        session.flush()
        job_id = job.id
    params = {
        "book_id": ids["book"],
        "book_version_id": ids["version"],
        "kind": kind.value,
        "chapter_id": chapter_id,
        "idempotency_key": "saved-request",
    }
    response = client.get("/api/jobs/recent", params=params)
    assert response.status_code == 200, response.text
    restored = response.json()["data"]
    assert len(restored) == 1 and restored[0]["id"] == job_id
    assert restored[0]["range"]["selected_window_ids"] == ["w1"]
    assert (
        client.get("/api/jobs/recent", params={"quote_id": quote_id, "kind": kind.value}).json()[
            "data"
        ][0]["id"]
        == job_id
    )
    assert (
        client.get("/api/jobs/recent", params={**params, "idempotency_key": "other"}).json()["data"]
        == []
    )
    assert (
        client.get("/api/jobs/recent", params={**params, "book_version_id": "wrong"}).status_code
        == 422
    )
    assert client.get("/api/jobs/recent", params={**params, "limit": 201}).status_code == 422
    assert client.get("/api/jobs/recent").status_code == 422
    with transaction(client.app.state.session_factory) as session:
        assert session.get(Job, job_id).state is JobState.RUNNING
