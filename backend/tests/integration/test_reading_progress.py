"""集成测试：阅读位置与阅读模式（不调用模型）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

SAMPLE = (
    "第一章 雨夜\n"
    "「雨停了。」少女合上伞。\n"
    "少年没有回答，只是把外套递了过去。\n"
    "第二章 名字\n"
    "「雨停了。」少女又说了一次。\n"
)


def _import(client: TestClient) -> dict:
    response = client.post(
        "/api/books/import",
        files={"file": ("sample.txt", SAMPLE.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 202
    return response.json()["data"]


def test_save_and_read_back_progress(migrated_client: TestClient) -> None:
    data = _import(migrated_client)
    book_id = data["book_id"]

    response = migrated_client.put(
        f"/api/books/{book_id}/reading-progress",
        json={
            "book_version_id": data["book_version_id"],
            "read_position_cp": 20,
            "reading_mode": "reread",
            "expected_version": 1,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["read_position_cp"] == 20
    assert payload["reading_mode"] == "reread"
    assert payload["version"] == 2

    book = migrated_client.get(f"/api/books/{book_id}").json()["data"]
    assert book["read_position_cp"] == 20
    assert book["reading_mode"] == "reread"
    assert book["version"] == 2


def test_stale_expected_version_returns_409(migrated_client: TestClient) -> None:
    data = _import(migrated_client)
    book_id = data["book_id"]
    body = {
        "book_version_id": data["book_version_id"],
        "read_position_cp": 5,
        "expected_version": 1,
    }
    assert migrated_client.put(f"/api/books/{book_id}/reading-progress", json=body).status_code == 200

    stale = migrated_client.put(f"/api/books/{book_id}/reading-progress", json=body)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"
    assert stale.json()["error"]["details"]["current_version"] == 2


def test_progress_rejects_foreign_version_and_out_of_range(migrated_client: TestClient) -> None:
    first = _import(migrated_client)
    second = migrated_client.post(
        "/api/books/import",
        files={"file": ("other.txt", SAMPLE.replace("雨停了", "风停了").encode("utf-8"), "text/plain")},
    ).json()["data"]

    foreign = migrated_client.put(
        f"/api/books/{first['book_id']}/reading-progress",
        json={
            "book_version_id": second["book_version_id"],
            "read_position_cp": 1,
        },
    )
    assert foreign.status_code == 422

    too_far = migrated_client.put(
        f"/api/books/{first['book_id']}/reading-progress",
        json={
            "book_version_id": first["book_version_id"],
            "read_position_cp": first["canonical_length_cp"] + 1,
        },
    )
    assert too_far.status_code == 422
    assert too_far.json()["error"]["code"] == "VALIDATION_ERROR"


def test_progress_on_unknown_book_returns_404(migrated_client: TestClient) -> None:
    response = migrated_client.put(
        "/api/books/nope/reading-progress",
        json={"book_version_id": "x", "read_position_cp": 0},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_progress_does_not_create_jobs_or_annotations(migrated_client: TestClient) -> None:
    data = _import(migrated_client)
    book_id = data["book_id"]
    job_id = data["job_id"]

    migrated_client.put(
        f"/api/books/{book_id}/reading-progress",
        json={"book_version_id": data["book_version_id"], "read_position_cp": 3},
    )

    # 只保存书签：不新增任务、也不产生任何识别结果。
    import_jobs = migrated_client.get(f"/api/jobs/{job_id}").json()["data"]
    assert import_jobs["kind"] == "IMPORT"
    content = migrated_client.get(f"/api/books/{book_id}/content").json()["data"]
    assert all("annotation" not in node for node in content["nodes"])
