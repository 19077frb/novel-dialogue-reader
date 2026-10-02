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
from ndr.jobs.scheduler import run_job
from ndr.llm.adapters.fake import FakeProviderAdapter
from ndr.llm.errors import ProviderError, ProviderErrorKind
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
        **overrides,
    }


def _run_merge(client, job, groups, adapter=None):
    adapter = adapter or FakeProviderAdapter(
        script=[{"groups": groups}], usage={"input_tokens": 30, "output_tokens": 10}
    )
    outcome = run_job(
        client.app.state.session_factory,
        client.app.state.settings,
        job_id=job["id"],
        adapter_factory=lambda *_: adapter,
    )
    return outcome, adapter


def test_auto_merge_applies_and_preserves_names_descriptions_references_usage(
    migrated_client, populated
):
    client, ids = migrated_client, populated
    with transaction(client.app.state.session_factory) as session:
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
    outcome, adapter = _run_merge(client, job, [_merge_group(ids)])
    assert outcome.state is JobState.COMPLETED
    result = client.get(f"{base}/auto-merge/{job['id']}").json()["data"]
    assert result["merged_count"] == 1
    assert result["merges"][0]["source_names"] == ["浅村悠太"]
    assert result["usage"]["total_tokens"] == 40
    assert client.get(f"{base}/auto-merge").json()["data"] == result
    rows = client.get(base).json()["data"]
    target = next(row for row in rows if row["character_id"] == ids["target"])
    assert target["description"] == "目标说明；书店店员"
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
    with transaction(client.app.state.session_factory) as session:
        assert session.get(BookCharacter, ids["source"]) is not None


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


def test_auto_merge_retains_group_if_combined_description_would_lose_data(
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
    outcome, _ = _run_merge(client, job, [_merge_group(ids)])
    assert outcome.state is JobState.COMPLETED
    result = client.get(
        f"/api/books/{ids['book']}/character-directory/auto-merge/{job['id']}"
    ).json()["data"]
    assert result["skipped_groups"] == 1 and result["merged_count"] == 0
    recovery = client.get(f"/api/jobs/{job['id']}/recovery").json()["data"]
    assert recovery["actions"] == []


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
