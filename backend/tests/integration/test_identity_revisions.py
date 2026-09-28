"""集成测试：场景内说话人分组的 merge / split 与撤销。

覆盖：同场景合并、拆成多个新分组、跨场景引用拒绝、混合分组拆分拒绝、
场景版本冲突、历史只追加（不硬删除）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from fixtures.corrections import (
    annotation_state,
    annotations_of,
    count_rows,
    create_fake_profile,
    import_sample,
    quote_ids,
    run_deterministic_job,
    scene_groups,
    scene_state,
    session_scope,
)
from ndr.config import Settings
from ndr.storage.models import Annotation, AnnotationHistory, Correction, IdentityRevision
from ndr.storage.transactions import transaction


def _prepare(client: TestClient, settings: Settings, *, key: str = "k-identity") -> dict:
    data = import_sample(client)
    profile_id = create_fake_profile(client)
    run_deterministic_job(
        settings, client, book_id=data["book_id"], profile_id=profile_id, key=key
    )
    return data


def _speaker_ids_by_quote(settings: Settings, scene_id: str) -> dict[str, str | None]:
    with session_scope(settings) as factory, transaction(factory) as session:
        return {
            row.quote_id: row.speaker_id
            for row in session.execute(
                select(Annotation).where(Annotation.scene_id == scene_id)
            ).scalars()
        }


def test_merge_groups_rewrites_projection_and_is_undoable(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    data = _prepare(fake_provider_client, migrated_settings)
    book_id = data["book_id"]
    ids = quote_ids(fake_provider_client, book_id)
    scene_id = annotation_state(migrated_settings, ids[0])["scene_id"]
    assert scene_id

    groups = scene_groups(migrated_settings, scene_id)
    first_group = next(group_id for group_id, label in groups.items() if label == "S1")

    # 用人工更正造出第二个分组（同一场景）
    target = ids[-1]
    state = annotation_state(migrated_settings, target)
    created = fake_provider_client.post(
        f"/api/quotes/{target}/corrections",
        json={"action": "create_speaker", "description": "人工新建", "expected_version": state["version"]},
    )
    assert created.status_code == 201, created.text
    second_group = annotation_state(migrated_settings, target)["speaker_id"]
    assert second_group and second_group != first_group

    scene_before = scene_state(migrated_settings, scene_id)
    history_before = count_rows(migrated_settings, AnnotationHistory)
    revisions_before = count_rows(migrated_settings, IdentityRevision)

    response = fake_provider_client.post(
        f"/api/scenes/{scene_id}/speaker-revisions",
        json={
            "operation": "MERGE",
            "source_group_ids": [first_group, second_group],
            "expected_scene_version": scene_before["version"],
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()["data"]
    assert payload["operation"] == "MERGE"
    assert payload["revision_id"] and payload["correction_id"]
    assert payload["empty_group_ids"] == [second_group]
    assert target in payload["affected_quote_ids"]

    scene_after = scene_state(migrated_settings, scene_id)
    assert scene_after["version"] == scene_before["version"] + 1
    assert annotation_state(migrated_settings, target)["speaker_id"] == first_group
    assert count_rows(migrated_settings, AnnotationHistory) > history_before
    assert count_rows(migrated_settings, IdentityRevision) == revisions_before + 1

    # 撤销：按 quote_id 精确还原
    undo = fake_provider_client.post(f"/api/corrections/{payload['correction_id']}/undo")
    assert undo.status_code == 201, undo.text
    assert annotation_state(migrated_settings, target)["speaker_id"] == second_group
    assert scene_state(migrated_settings, scene_id)["version"] == scene_after["version"] + 1


def test_split_creates_new_groups_and_is_undoable(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    data = _prepare(fake_provider_client, migrated_settings, key="k-split")
    book_id = data["book_id"]
    ids = quote_ids(fake_provider_client, book_id)
    scene_id = annotation_state(migrated_settings, ids[0])["scene_id"]
    assert scene_id
    groups_before = scene_groups(migrated_settings, scene_id)
    source_group = next(group_id for group_id, label in groups_before.items() if label == "S1")
    assert annotation_state(migrated_settings, ids[0])["speaker_id"] == source_group
    assert annotation_state(migrated_settings, ids[1])["speaker_id"] == source_group

    scene_before = scene_state(migrated_settings, scene_id)
    response = fake_provider_client.post(
        f"/api/scenes/{scene_id}/speaker-revisions",
        json={
            "operation": "SPLIT",
            "buckets": [[ids[0]], [ids[1]]],
            "expected_scene_version": scene_before["version"],
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()["data"]
    assert len(payload["created_group_ids"]) == 2
    assert payload["affected_quote_ids"] == [ids[0], ids[1]]

    first_new = annotation_state(migrated_settings, ids[0])["speaker_id"]
    second_new = annotation_state(migrated_settings, ids[1])["speaker_id"]
    assert first_new and second_new and first_new != second_new
    assert first_new != source_group and second_new != source_group
    groups_after = scene_groups(migrated_settings, scene_id)
    assert groups_after[first_new] == "S2"
    assert groups_after[second_new] == "S3"
    # 未列出的引语仍属于原分组
    assert annotation_state(migrated_settings, ids[2])["speaker_id"] == source_group

    undo = fake_provider_client.post(f"/api/corrections/{payload['correction_id']}/undo")
    assert undo.status_code == 201, undo.text
    assert annotation_state(migrated_settings, ids[0])["speaker_id"] == source_group
    assert annotation_state(migrated_settings, ids[1])["speaker_id"] == source_group

def test_cross_scene_group_is_rejected(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    """merge 只能在本场景内进行；引用别的场景的分组一律 422。"""

    from fixtures.corrections import CHAPTER_TWO_CP

    data = import_sample(fake_provider_client)
    book_id = data["book_id"]
    profile_id = create_fake_profile(fake_provider_client)
    run_deterministic_job(
        migrated_settings,
        fake_provider_client,
        book_id=book_id,
        profile_id=profile_id,
        key="k-scene-a",
        start_cp=0,
        end_cp=CHAPTER_TWO_CP,
    )
    run_deterministic_job(
        migrated_settings,
        fake_provider_client,
        book_id=book_id,
        profile_id=profile_id,
        key="k-scene-b",
        start_cp=CHAPTER_TWO_CP,
    )
    items = annotations_of(fake_provider_client, book_id)["items"]
    first = next(row for row in items if row["start_cp"] < CHAPTER_TWO_CP)
    second = next(row for row in items if row["start_cp"] >= CHAPTER_TWO_CP)
    scene_a = first["scene_id"]
    assert scene_a and second["scene_id"] and scene_a != second["scene_id"]

    response = fake_provider_client.post(
        f"/api/scenes/{scene_a}/speaker-revisions",
        json={
            "operation": "MERGE",
            "source_group_ids": [first["speaker_group_id"], second["speaker_group_id"]],
            "expected_scene_version": scene_state(migrated_settings, scene_a)["version"],
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "CROSS_SCENE_SPEAKER"


def test_split_with_mixed_groups_is_rejected(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    data = _prepare(fake_provider_client, migrated_settings, key="k-split-mixed")
    book_id = data["book_id"]
    ids = quote_ids(fake_provider_client, book_id)
    scene_id = annotation_state(migrated_settings, ids[0])["scene_id"]
    assert scene_id

    # 先造出第二个分组
    state = annotation_state(migrated_settings, ids[1])
    created = fake_provider_client.post(
        f"/api/quotes/{ids[1]}/corrections",
        json={"action": "create_speaker", "expected_version": state["version"]},
    )
    assert created.status_code == 201, created.text

    response = fake_provider_client.post(
        f"/api/scenes/{scene_id}/speaker-revisions",
        json={"operation": "SPLIT", "buckets": [[ids[0]], [ids[1]]]},
    )
    assert response.status_code == 422, response.text
    details = response.json()["error"]["details"]
    assert details["reason"] == "SPLIT_MIXED_GROUPS"


def test_scene_version_conflict_is_rejected(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    data = _prepare(fake_provider_client, migrated_settings, key="k-version")
    book_id = data["book_id"]
    ids = quote_ids(fake_provider_client, book_id)
    scene_id = annotation_state(migrated_settings, ids[0])["scene_id"]
    assert scene_id
    groups = scene_groups(migrated_settings, scene_id)
    source_group = next(group_id for group_id, label in groups.items() if label == "S1")
    scene = scene_state(migrated_settings, scene_id)
    history_before = count_rows(migrated_settings, AnnotationHistory)

    response = fake_provider_client.post(
        f"/api/scenes/{scene_id}/speaker-revisions",
        json={
            "operation": "SPLIT",
            "buckets": [[ids[0]], [ids[1]]],
            "expected_scene_version": scene["version"] + 3,
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "VERSION_CONFLICT"
    assert scene_state(migrated_settings, scene_id)["version"] == scene["version"]
    assert count_rows(migrated_settings, AnnotationHistory) == history_before
    assert annotation_state(migrated_settings, ids[0])["speaker_id"] == source_group


def test_history_is_append_only_no_rows_deleted(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    """更正与撤销只追加历史：`annotations` 行数不变，历史与更正记录只增不减。"""

    data = _prepare(fake_provider_client, migrated_settings, key="k-history")
    book_id = data["book_id"]
    ids = quote_ids(fake_provider_client, book_id)
    scene_id = annotation_state(migrated_settings, ids[0])["scene_id"]
    assert scene_id
    groups = scene_groups(migrated_settings, scene_id)
    source_group = next(group_id for group_id, label in groups.items() if label == "S1")

    annotations_before = count_rows(migrated_settings, Annotation)
    history_before = count_rows(migrated_settings, AnnotationHistory)
    corrections_before = count_rows(migrated_settings, Correction)

    revision = fake_provider_client.post(
        f"/api/scenes/{scene_id}/speaker-revisions",
        json={"operation": "SPLIT", "buckets": [[ids[0]], [ids[1]]]},
    )
    assert revision.status_code == 201, revision.text
    correction_id = revision.json()["data"]["correction_id"]
    assert count_rows(migrated_settings, Annotation) == annotations_before
    assert count_rows(migrated_settings, AnnotationHistory) > history_before
    assert count_rows(migrated_settings, Correction) == corrections_before + 1

    undo = fake_provider_client.post(f"/api/corrections/{correction_id}/undo")
    assert undo.status_code == 201, undo.text
    assert count_rows(migrated_settings, Annotation) == annotations_before
    assert count_rows(migrated_settings, Correction) == corrections_before + 2
    assert annotation_state(migrated_settings, ids[0])["speaker_id"] == source_group
