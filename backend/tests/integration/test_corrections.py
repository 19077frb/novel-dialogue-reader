"""集成测试：人工更正、待确认队列与撤销。

覆盖：普通对白（含未处理）详情与主动标记、四种说话人更正、跨场景误关联拒绝、
并发旧版本冲突、局部更正不扩散、历史误触发清理、锁定保护、Gap BREAK 与撤销。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from fixtures.corrections import (
    CHAPTER_TWO_CP,
    annotations_of,
    create_fake_profile,
    import_sample,
    quote_ids,
    run_deterministic_job,
    session_scope,
)
from ndr.config import Settings
from ndr.corrections.history import annotation_snapshot
from ndr.corrections.invalidator import mark_stale, upsert_review_item
from ndr.domain.enums import (
    AnnotationSource,
    CorrectionAction,
    CorrectionTargetType,
    ReviewQueueStatus,
    ReviewReason,
)
from ndr.storage.models import (
    Annotation,
    AnnotationHistory,
    Correction,
    InferenceRun,
    ReviewItem,
    SpeakerGroup,
)
from ndr.storage.transactions import transaction


def _prepare(
    client: TestClient,
    settings: Settings,
    *,
    start_cp: int = 0,
    end_cp: int | None = None,
    key: str = "k-prepare",
) -> dict:
    data = import_sample(client)
    profile_id = create_fake_profile(client)
    run_deterministic_job(
        settings,
        client,
        book_id=data["book_id"],
        profile_id=profile_id,
        key=key,
        start_cp=start_cp,
        end_cp=end_cp,
    )
    return data


def _annotation_state(settings: Settings, quote_id: str) -> dict:
    """返回普通 dict（脱离 session 后仍可断言）。"""

    with session_scope(settings) as factory, transaction(factory) as session:
        row = session.execute(
            select(Annotation).where(Annotation.quote_id == quote_id)
        ).scalar_one()
        return {
            "id": row.id,
            "version": row.version,
            "scene_id": row.scene_id,
            "kind": row.kind.value,
            "assignment": row.assignment.value if row.assignment else None,
            "basis": row.basis.value if row.basis else None,
            "speaker_id": row.speaker_id,
            "status": row.status.value,
            "source": row.source.value,
            "stale": row.stale,
            "user_locked": row.user_locked,
        }


def _counts(settings: Settings) -> dict[str, int]:
    with session_scope(settings) as factory, transaction(factory) as session:
        return {
            "annotations": len(list(session.execute(select(Annotation)).scalars())),
            "history": len(list(session.execute(select(AnnotationHistory)).scalars())),
            "corrections": len(list(session.execute(select(Correction)).scalars())),
            "runs": len(list(session.execute(select(InferenceRun)).scalars())),
            "review_items": len(list(session.execute(select(ReviewItem)).scalars())),
        }


def _scene_state(settings: Settings, scene_id: str) -> dict:
    from ndr.storage.models import Scene

    with session_scope(settings) as factory, transaction(factory) as session:
        row = session.get(Scene, scene_id)
        assert row is not None
        return {
            "id": row.id,
            "status": row.status.value,
            "start_cp": row.start_cp,
            "end_cp": row.end_cp,
            "version": row.version,
        }


def _group_labels(settings: Settings, scene_id: str) -> dict[str, str]:
    from ndr.storage.models import SpeakerGroup

    with session_scope(settings) as factory, transaction(factory) as session:
        return {
            row.id: row.display_label
            for row in session.execute(
                select(SpeakerGroup).where(SpeakerGroup.scene_id == scene_id)
            ).scalars()
        }

def test_quote_detail_and_review_flag_work_for_unprocessed_quote(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    """只处理第一章时，第二章的对白也能取详情并主动标记（不要求已在队列里）。"""

    data = _prepare(
        fake_provider_client, migrated_settings, start_cp=0, end_cp=CHAPTER_TWO_CP
    )
    book_id = data["book_id"]
    ids = quote_ids(fake_provider_client, book_id)
    unprocessed = next(
        quote_id
        for quote_id in ids
        if quote_id
        not in {
            item["quote_id"]
            for item in annotations_of(
                fake_provider_client, book_id, start_cp=0, end_cp=CHAPTER_TWO_CP
            )["items"]
        }
    )

    detail = fake_provider_client.get(f"/api/quotes/{unprocessed}").json()["data"]
    assert detail["annotation"] is None  # 还没处理过，但详情可用
    assert detail["can_correct"] is True
    assert detail["review_items"] == []

    flagged = fake_provider_client.post(
        f"/api/quotes/{unprocessed}/review-items", json={"reason": "USER_FLAGGED", "note": "先标记"}
    )
    assert flagged.status_code == 201, flagged.text
    item = flagged.json()["data"]
    assert item["queue_status"] == "PENDING"
    assert item["reason"] == "USER_FLAGGED"

    # 幂等：重复标记返回同一条
    again = fake_provider_client.post(f"/api/quotes/{unprocessed}/review-items", json={})
    assert again.status_code == 201
    assert again.json()["data"]["id"] == item["id"]

    deferred = fake_provider_client.post(f"/api/review-items/{item['id']}/defer", json={"note": "稍后"})
    assert deferred.status_code == 200, deferred.text
    assert deferred.json()["data"]["queue_status"] == "DEFERRED"

    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        session.add(
            ReviewItem(
                target_type="quote",
                quote_id=unprocessed,
                reason="OTHER",
                queue_status="DEFERRED",
                candidates_json="{}",
            )
        )

    queue = fake_provider_client.get(f"/api/books/{book_id}/review-items").json()["data"]
    assert queue["counts"]["total"] >= 1
    assert queue["counts"]["by_status"]["DEFERRED"] >= 1
    assert item["id"] in {row["id"] for row in queue["items"]}
    queued_item = next(row for row in queue["items"] if row["id"] == item["id"])
    assert queued_item["target_text"].startswith(("「", "『", '"'))
    assert queued_item["target_text"] != "（对白）"
    assert queue["counts"]["targets_total"] < queue["counts"]["total"]
    assert (
        queue["counts"]["targets_by_status"]["DEFERRED"]
        < queue["counts"]["by_status"]["DEFERRED"]
    )

    detail_item = fake_provider_client.get(f"/api/review-items/{item['id']}").json()["data"]
    assert detail_item["item"]["id"] == item["id"]
    assert detail_item["annotation"] is None
    assert "assign_existing" in detail_item["allowed_actions"]
    assert detail_item["context_after"] or detail_item["context_before"]


def test_quote_detail_exposes_confirmed_speaker_names(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    data = _prepare(fake_provider_client, migrated_settings)
    annotation = annotations_of(fake_provider_client, data["book_id"])["items"][0]

    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        group = session.get(SpeakerGroup, annotation["speaker_group_id"])
        assert group is not None
        group.canonical_name = "绫濑沙季"

    detail = fake_provider_client.get(f"/api/quotes/{annotation['quote_id']}")
    assert detail.status_code == 200, detail.text
    groups = detail.json()["data"]["scene_groups"]
    current = next(
        group for group in groups if group["group_id"] == annotation["speaker_group_id"]
    )
    assert current["label"].startswith("S")
    assert current["canonical_name"] == "绫濑沙季"
    assert "description" in current

def test_assign_existing_locks_quote_without_model_call_or_invalidating_others(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    data = _prepare(fake_provider_client, migrated_settings)
    book_id = data["book_id"]
    projection = annotations_of(fake_provider_client, book_id)
    items = projection["items"]
    assert len(items) >= 3
    group_id = items[0]["speaker_group_id"]
    label = items[0]["label"]
    assert group_id and label and not label.startswith("S")

    second = items[1]
    before = _annotation_state(migrated_settings, second["quote_id"])
    other_states = {item["quote_id"]: _annotation_state(migrated_settings, item["quote_id"])
                    for item in items if item["quote_id"] != second["quote_id"]}
    counts_before = _counts(migrated_settings)

    response = fake_provider_client.post(
        f"/api/quotes/{second['quote_id']}/corrections",
        json={
            "action": "assign_existing",
            "speaker_ref": group_id,
            "expected_version": before["version"],
            "note": "人工确认",
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()["data"]
    assert payload["action"] == "assign_existing"
    assert payload["affected_quote_ids"] == [second["quote_id"]]
    assert payload["correction_id"] and payload["correction_ids"] == [payload["correction_id"]]
    assert payload["annotation_versions"][second["quote_id"]] == before["version"] + 1
    assert payload["updated_review_counts"]["total"] >= 0

    after = _annotation_state(migrated_settings, second["quote_id"])
    assert after["speaker_id"] == group_id
    assert after["status"] == "USER_CONFIRMED"
    assert after["source"] == "USER"
    assert after["user_locked"] is True
    assert after["assignment"] == "EXISTING"

    assert payload["stale_quote_ids"] == []
    assert payload["stale_window_ids"] == []
    for quote_id, old_state in other_states.items():
        assert _annotation_state(migrated_settings, quote_id) == old_state

    counts_after = _counts(migrated_settings)
    assert counts_after["runs"] == counts_before["runs"]  # 人工更正不产生模型调用
    assert counts_after["history"] == counts_before["history"] + 1
    assert counts_after["corrections"] == counts_before["corrections"] + 1

    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        history = list(
            session.execute(
                select(AnnotationHistory).where(
                    AnnotationHistory.annotation_id == after["id"]
                )
            ).scalars()
        )
    assert [row.revision for row in history] == [before["version"]]
    snapshot = json.loads(history[0].snapshot_json)
    assert snapshot["source"] == "MODEL" and snapshot["user_locked"] is False
    assert snapshot["status"] == before["status"]


@pytest.mark.parametrize("action, extra", [
    ("mark_unknown", {}), ("set_kind", {"kind": "thought"}),
    ("create_speaker", {"description": "另一个说话人"}),
])
def test_local_correction_and_undo_leave_other_quotes_unchanged(
    fake_provider_client, migrated_settings, action, extra,
):
    client = fake_provider_client
    data = _prepare(client, migrated_settings)
    ids = quote_ids(client, data["book_id"])
    before = {quote_id: _annotation_state(migrated_settings, quote_id) for quote_id in ids}
    result = client.post(f"/api/quotes/{ids[0]}/corrections", json={
        "action": action, "expected_version": before[ids[0]]["version"], **extra,
    })
    assert result.status_code == 201, result.text
    assert result.json()["data"]["stale_quote_ids"] == []
    undone = client.post(f"/api/corrections/{result.json()['data']['correction_id']}/undo")
    assert undone.status_code == 201, undone.text
    assert undone.json()["data"]["stale_quote_ids"] == []
    for quote_id in ids[1:]:
        assert _annotation_state(migrated_settings, quote_id) == before[quote_id]


def test_multi_quote_correction_only_changes_explicit_selection(
    fake_provider_client, migrated_settings,
):
    client = fake_provider_client
    data = _prepare(client, migrated_settings)
    ids = quote_ids(client, data["book_id"])
    before = {quote_id: _annotation_state(migrated_settings, quote_id) for quote_id in ids}
    selected = ids[:2]
    response = client.post(f"/api/quotes/{selected[0]}/corrections", json={
        "action": "mark_unknown", "quote_ids": selected,
        "expected_version": before[selected[0]]["version"],
    })
    assert response.status_code == 201, response.text
    result = response.json()["data"]
    assert set(result["affected_quote_ids"]) == set(selected)
    assert result["stale_quote_ids"] == []
    for quote_id in selected:
        assert _annotation_state(migrated_settings, quote_id)["user_locked"] is True
    for quote_id in ids[2:]:
        assert _annotation_state(migrated_settings, quote_id) == before[quote_id]


def test_cleanup_only_resolves_proven_legacy_cascades_and_is_idempotent(
    fake_provider_client, migrated_settings,
):
    client = fake_provider_client
    data = _prepare(client, migrated_settings)
    ids = quote_ids(client, data["book_id"])
    assert len(ids) == 8
    original = _annotation_state(migrated_settings, ids[0])
    corrected = client.post(f"/api/quotes/{ids[0]}/corrections", json={
        "action": "mark_unknown", "expected_version": original["version"],
    }).json()["data"]
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        annotations = {a.quote_id: a for a in session.scalars(select(Annotation))}
        structural = Correction(target_type=CorrectionTargetType.SCENE,
            target_id=annotations[ids[4]].scene_id, action=CorrectionAction.UNDO)
        session.add(structural)
        session.flush()
        session.add(AnnotationHistory(annotation_id=annotations[ids[4]].id,
            revision=annotations[ids[4]].version,
            snapshot_json=json.dumps(annotation_snapshot(annotations[ids[4]])),
            correction_id=structural.id))
        annotations[ids[4]].version += 1
        # Simulate an old local cascade overwriting a previous structural cause.
        mark_stale(session, [annotations[q] for q in ids[1:]],
                   correction_id=corrected["correction_id"])
        annotations[ids[2]].version += 1  # A newer result cannot be blindly restored.
        annotations[ids[3]].source = AnnotationSource.USER
        annotations[ids[6]].user_locked = True
        stale_items = {r.quote_id: r for r in session.scalars(select(ReviewItem).where(
            ReviewItem.reason == ReviewReason.STALE_DEPENDENCY))}
        stale_items[ids[5]].queue_status = ReviewQueueStatus.DEFERRED
        stale_items[ids[7]].candidates_json = '{"correction_id":"missing"}'
        for reason in (ReviewReason.LOW_CONFIDENCE, ReviewReason.UNKNOWN_SPEAKER,
                       ReviewReason.USER_FLAGGED):
            upsert_review_item(session, quote_id=ids[1], reason=reason,
                               candidates={"reason": "real uncertainty"})
    before = {quote_id: _annotation_state(migrated_settings, quote_id) for quote_id in ids}
    counts = _counts(migrated_settings)
    endpoint = f"/api/books/{data['book_id']}/review-items/cleanup-dependencies"
    response = client.post(endpoint)
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "resolved_records": 1, "restored_quotes": 1, "preserved_records": 6,
    }
    restored = _annotation_state(migrated_settings, ids[1])
    assert restored == {**before[ids[1]], "stale": False,
                        "version": before[ids[1]]["version"] + 1}
    for quote_id in [ids[0], *ids[2:]]:
        assert _annotation_state(migrated_settings, quote_id) == before[quote_id]
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        for reason in (ReviewReason.LOW_CONFIDENCE, ReviewReason.UNKNOWN_SPEAKER,
                       ReviewReason.USER_FLAGGED):
            row = session.scalar(select(ReviewItem).where(
                ReviewItem.quote_id == ids[1], ReviewItem.reason == reason))
            assert row.queue_status is ReviewQueueStatus.PENDING
    after = _counts(migrated_settings)
    assert after["runs"] == counts["runs"] and after["history"] == counts["history"] + 1
    repeated = client.post(endpoint)
    assert repeated.json()["data"] == {
        "resolved_records": 0, "restored_quotes": 0, "preserved_records": 6,
    }
    assert _counts(migrated_settings) == after


def test_cleanup_refuses_active_tasks_without_modifying_data(fake_provider_client, migrated_settings):
    client = fake_provider_client
    data = _prepare(client, migrated_settings)
    job = client.post("/api/jobs", json={"book_id": data["book_id"],
        "profile_id": create_fake_profile(client, name="cleanup-busy-profile"),
        "mode": "process", "range": {},
        "idempotency_key": "cleanup-busy", "run_now": False})
    assert job.status_code == 202, job.text
    before = _counts(migrated_settings)
    response = client.post(f"/api/books/{data['book_id']}/review-items/cleanup-dependencies")
    assert response.status_code == 409 and "任务" in response.json()["error"]["message"]
    assert _counts(migrated_settings) == before


def test_old_expected_version_is_rejected_and_changes_nothing(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    data = _prepare(fake_provider_client, migrated_settings)
    book_id = data["book_id"]
    ids = quote_ids(fake_provider_client, book_id)
    target = ids[0]
    before = _annotation_state(migrated_settings, target)
    counts_before = _counts(migrated_settings)

    response = fake_provider_client.post(
        f"/api/quotes/{target}/corrections",
        json={"action": "mark_unknown", "expected_version": before["version"] + 5},
    )
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "VERSION_CONFLICT"
    assert error["details"]["current_version"] == before["version"]

    assert _annotation_state(migrated_settings, target) == before
    assert _counts(migrated_settings) == counts_before

def test_undo_restores_previous_state_and_rejects_newer_revision(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    """撤销恢复旧状态；若已有更新的修订则返回冲突且不丢数据。"""

    data = _prepare(fake_provider_client, migrated_settings)
    book_id = data["book_id"]
    ids = quote_ids(fake_provider_client, book_id)
    target = ids[2]
    original = _annotation_state(migrated_settings, target)

    first = fake_provider_client.post(
        f"/api/quotes/{target}/corrections",
        json={"action": "mark_unknown", "expected_version": original["version"]},
    )
    assert first.status_code == 201, first.text
    correction_id = first.json()["data"]["correction_id"]
    locked = _annotation_state(migrated_settings, target)
    assert locked["status"] == "UNKNOWN"

    # 又做了一次更新的修订
    second = fake_provider_client.post(
        f"/api/quotes/{target}/corrections",
        json={"action": "create_speaker", "description": "人工新建", "expected_version": locked["version"]},
    )
    assert second.status_code == 201, second.text
    newest = _annotation_state(migrated_settings, target)
    assert newest["version"] == locked["version"] + 1

    # 撤销“越过”新修订 → 409，且当前状态不变
    stale_undo = fake_provider_client.post(f"/api/corrections/{correction_id}/undo")
    assert stale_undo.status_code == 409, stale_undo.text
    assert stale_undo.json()["error"]["code"] == "VERSION_CONFLICT"
    assert _annotation_state(migrated_settings, target) == newest

    # 撤销最新的那次 → 成功，恢复到“未知锁定”状态
    ok = fake_provider_client.post(f"/api/corrections/{second.json()['data']['correction_id']}/undo")
    assert ok.status_code == 201, ok.text
    restored = _annotation_state(migrated_settings, target)
    assert restored["status"] == locked["status"]
    assert restored["speaker_id"] == locked["speaker_id"]
    assert restored["version"] == newest["version"] + 1

    # 同一条更正不能重复撤销
    again = fake_provider_client.post(f"/api/corrections/{second.json()['data']['correction_id']}/undo")
    assert again.status_code == 409
    assert again.json()["error"]["details"]["reason"] == "ALREADY_UNDONE"


def test_mark_unknown_locks_unknown_without_creating_new_group(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    data = _prepare(fake_provider_client, migrated_settings)
    book_id = data["book_id"]
    ids = quote_ids(fake_provider_client, book_id)
    target = ids[1]

    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        from ndr.storage.models import SpeakerGroup

        groups_before = len(list(session.execute(select(SpeakerGroup)).scalars()))

    state = _annotation_state(migrated_settings, target)
    response = fake_provider_client.post(
        f"/api/quotes/{target}/corrections",
        json={"action": "mark_unknown", "expected_version": state["version"]},
    )
    assert response.status_code == 201, response.text
    after = _annotation_state(migrated_settings, target)
    assert after["status"] == "UNKNOWN"
    assert after["assignment"] == "UNKNOWN"
    assert after["speaker_id"] is None
    assert after["user_locked"] is True

    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        from ndr.storage.models import SpeakerGroup

        groups_after = len(list(session.execute(select(SpeakerGroup)).scalars()))
    assert groups_after == groups_before  # 未知不制造新人

    projection = annotations_of(fake_provider_client, book_id)
    item = next(row for row in projection["items"] if row["quote_id"] == target)
    assert item["color_index"] is None and item["label"] is None


def test_cross_scene_speaker_reference_is_rejected(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    """跨场景误关联必须被拒绝：两个独立任务各自开场景，编号只在场景内有意义。"""

    data = _prepare(
        fake_provider_client, migrated_settings, start_cp=0, end_cp=CHAPTER_TWO_CP
    )
    book_id = data["book_id"]
    profile_id = create_fake_profile(
        fake_provider_client, name="第二章提供方", model="fake-model-ch2"
    )
    run_deterministic_job(
        migrated_settings,
        fake_provider_client,
        book_id=book_id,
        profile_id=profile_id,
        key="k-second-scene",
        start_cp=CHAPTER_TWO_CP,
    )
    chapter_two = [
        item
        for item in annotations_of(fake_provider_client, book_id)["items"]
        if item["start_cp"] >= CHAPTER_TWO_CP
    ]
    assert chapter_two, "第二章应有标注"
    other_group = chapter_two[0]["speaker_group_id"]
    assert other_group is not None

    first_chapter = [
        item
        for item in annotations_of(fake_provider_client, book_id)["items"]
        if item["start_cp"] < CHAPTER_TWO_CP
    ]
    target = first_chapter[0]["quote_id"]
    state = _annotation_state(migrated_settings, target)

    response = fake_provider_client.post(
        f"/api/quotes/{target}/corrections",
        json={
            "action": "assign_existing",
            "speaker_ref": other_group,
            "expected_version": state["version"],
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "CROSS_SCENE_SPEAKER"
    assert _annotation_state(migrated_settings, target) == state

def test_model_result_does_not_overwrite_user_locked_quote(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    """模型响应晚于用户确认时，人工锁定不被覆盖。"""

    data = _prepare(fake_provider_client, migrated_settings)
    book_id = data["book_id"]
    target = quote_ids(fake_provider_client, book_id)[0]

    state = _annotation_state(migrated_settings, target)
    locked = fake_provider_client.post(
        f"/api/quotes/{target}/corrections",
        json={"action": "mark_unknown", "expected_version": state["version"]},
    )
    assert locked.status_code == 201, locked.text
    after_lock = _annotation_state(migrated_settings, target)

    # 换一个模型名 → 缓存键不同 → 真的会调用适配器（FakeProvider，离线）
    profile2 = create_fake_profile(
        fake_provider_client, name="第二个提供方", model="fake-model-2"
    )
    run_deterministic_job(
        migrated_settings,
        fake_provider_client,
        book_id=book_id,
        profile_id=profile2,
        key="k-second-run",
    )

    after_run = _annotation_state(migrated_settings, target)
    assert after_run["status"] == "UNKNOWN"
    assert after_run["user_locked"] is True
    assert after_run["version"] == after_lock["version"]
    assert after_run["speaker_id"] is None


def test_gap_break_splits_scene_and_undo_restores_membership(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    data = _prepare(fake_provider_client, migrated_settings)
    book_id = data["book_id"]
    gaps = fake_provider_client.get(f"/api/books/{book_id}/gaps", params={"limit": 50}).json()[
        "data"
    ]["items"]
    items = annotations_of(fake_provider_client, book_id)["items"]
    by_quote = {row["quote_id"]: row for row in items}
    # 取第一个「两侧都有标注且同属一个场景」的 Gap（与返回顺序无关，断言只用集合）
    gap = next(
        row
        for row in gaps
        if row["left_quote_id"] in by_quote
        and row["right_quote_id"] in by_quote
        and by_quote[row["left_quote_id"]]["scene_id"]
        == by_quote[row["right_quote_id"]]["scene_id"]
    )
    scene_id = by_quote[gap["left_quote_id"]]["scene_id"]
    assert scene_id
    before_scene = _scene_state(migrated_settings, scene_id)
    moved_before = sorted(
        row["quote_id"]
        for row in items
        if row["start_cp"] >= gap["start_cp"] and row["scene_id"] == scene_id
    )
    assert moved_before

    response = fake_provider_client.post(
        f"/api/gaps/{gap['gap_id']}/corrections",
        json={"decision": "BREAK", "expected_scene_version": before_scene["version"]},
    )
    assert response.status_code == 201, response.text
    payload = response.json()["data"]
    assert payload["previous_decision"] == "UNCERTAIN"
    assert payload["decision"] == "BREAK"
    assert payload["closed_scene_ids"] == [scene_id]
    assert payload["opened_scene_id"]
    assert sorted(payload["affected_quote_ids"]) == moved_before

    closed = _scene_state(migrated_settings, scene_id)
    assert closed["status"] == "CLOSED"
    assert closed["end_cp"] == gap["start_cp"]
    opened = _scene_state(migrated_settings, payload["opened_scene_id"])
    assert opened["status"] == "OPEN"

    after_items = {
        row["quote_id"]: row
        for row in annotations_of(fake_provider_client, book_id)["items"]
    }
    for quote_id in moved_before:
        row = after_items[quote_id]
        assert row["scene_id"] == payload["opened_scene_id"]
        assert row["stale"] is True
    # 新场景内的编号从 S1 重新开始
    labels = _group_labels(migrated_settings, payload["opened_scene_id"])
    assert "S1" in labels.values()

    undo = fake_provider_client.post(f"/api/corrections/{payload['correction_id']}/undo")
    assert undo.status_code == 201, undo.text
    assert _scene_state(migrated_settings, scene_id)["status"] == "OPEN"
    restored_items = {
        row["quote_id"]: row
        for row in annotations_of(fake_provider_client, book_id)["items"]
    }
    for quote_id in moved_before:
        assert restored_items[quote_id]["scene_id"] == scene_id

def test_gap_correction_uncertain_keeps_boundary_in_review_queue(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    data = _prepare(fake_provider_client, migrated_settings)
    book_id = data["book_id"]
    gaps = fake_provider_client.get(f"/api/books/{book_id}/gaps", params={"limit": 50}).json()[
        "data"
    ]["items"]
    gap = gaps[0]

    response = fake_provider_client.post(
        f"/api/gaps/{gap['gap_id']}/corrections", json={"decision": "UNCERTAIN"}
    )
    assert response.status_code == 201, response.text
    payload = response.json()["data"]
    assert payload["created_review_item_ids"]
    assert payload["updated_review_counts"]["SCENE_BOUNDARY"] >= 1

    # 之后确认 BREAK → 队列项被解决
    resolved = fake_provider_client.post(
        f"/api/gaps/{gap['gap_id']}/corrections", json={"decision": "CONTINUE"}
    )
    assert resolved.status_code == 201, resolved.text
    assert payload["created_review_item_ids"][0] in resolved.json()["data"]["resolved_review_item_ids"]


def test_recheck_creates_bounded_job_and_does_not_call_model_at_creation(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    """局部复核 = 真实的付费任务（与「展开原文」完全不同）：显式配置 + 预算 + 幂等键。"""

    data = _prepare(fake_provider_client, migrated_settings, key="k-recheck")
    book_id = data["book_id"]
    target = quote_ids(fake_provider_client, book_id)[0]
    profile_id = create_fake_profile(
        fake_provider_client, name="复核提供方", model="fake-model-recheck"
    )
    scene = _scene_state(
        migrated_settings, _annotation_state(migrated_settings, target)["scene_id"]
    )
    runs_before = _counts(migrated_settings)["runs"]

    response = fake_provider_client.post(
        f"/api/quotes/{target}/recheck",
        json={
            "profile_id": profile_id,
            "budget": {"max_input_tokens": 5000, "max_rechecks": 1},
            "idempotency_key": "k-recheck-1",
            "inference_options": {"thinking_mode": "enabled", "reasoning_effort": "high"},
            "run_now": False,
            "note": "人工触发",
        },
    )
    assert response.status_code == 202, response.text
    payload = response.json()["data"]
    assert payload["kind"] == "RECHECK"
    assert payload["state"] == "QUEUED"
    # 创建任务本身不调用模型（真正执行由后台/显式触发）
    assert _counts(migrated_settings)["runs"] == runs_before

    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        from ndr.storage.models import Job

        job = session.get(Job, payload["id"])
        assert job is not None
        range_payload = json.loads(job.range_json)
        params = json.loads(job.profile_snapshot_json)["params"]
        assert params["thinking"] == {"type": "enabled"}
        assert params["reasoning_effort"] == "high"
    assert range_payload["start_cp"] == scene["start_cp"]
    assert range_payload["end_cp"] == (scene["end_cp"] or range_payload["end_cp"])

    # 幂等：同键同摘要复用同一任务
    again = fake_provider_client.post(
        f"/api/quotes/{target}/recheck",
        json={
            "profile_id": profile_id,
            "budget": {"max_input_tokens": 5000, "max_rechecks": 1},
            "idempotency_key": "k-recheck-1",
            "inference_options": {"thinking_mode": "enabled", "reasoning_effort": "high"},
            "run_now": False,
            "note": "人工触发",
        },
    )
    assert again.status_code == 202
    assert again.json()["data"]["id"] == payload["id"]

    changed = fake_provider_client.post(
        f"/api/quotes/{target}/recheck",
        json={
            "profile_id": profile_id,
            "budget": {"max_input_tokens": 5000, "max_rechecks": 1},
            "idempotency_key": "k-recheck-1",
            "run_now": False,
            "inference_options": {"thinking_mode": "disabled"},
        },
    )
    assert changed.status_code == 409

    missing = fake_provider_client.post(
        f"/api/quotes/{target}/recheck",
        json={"profile_id": "does-not-exist", "idempotency_key": "k-recheck-2"},
    )
    assert missing.status_code == 404
