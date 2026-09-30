from fastapi.testclient import TestClient
from sqlalchemy import func, select

from ndr.storage.models import Bookmark


def fixture(
    client: TestClient, text: str = "第一章\n「😀你好。」\n第二章\n正文\n"
) -> tuple[dict, dict]:
    data = client.post(
        "/api/books/import",
        files={
            "file": ("book.txt", text.encode(), "text/plain"),
        },
    ).json()["data"]
    chapter = client.get(f"/api/books/{data['book_id']}/chapters").json()["data"][0]
    return data, chapter


def test_bookmarks_crud_pagination_and_progress_independence(migrated_client: TestClient) -> None:
    client = migrated_client
    data, chapter = fixture(client)
    url = f"/api/books/{data['book_id']}/bookmarks"
    body = {
        "book_version_id": data["book_version_id"],
        "chapter_id": chapter["id"],
        "position_cp": chapter["start_cp"],
        "note": "想再看",
    }
    result = client.post(url, json=body)
    assert result.status_code == 201, result.text
    mark = result.json()["data"]
    assert mark["excerpt"].startswith("第一章")
    assert client.get(f"/api/books/{data['book_id']}").json()["data"]["version"] == 1
    assert client.post(url, json=body).status_code == 201
    first = client.get(url, params={"limit": 1}).json()["data"]
    assert len(first["items"]) == 1 and first["next_cursor"]
    second = client.get(url, params={"limit": 1, "cursor": first["next_cursor"]}).json()["data"]
    assert second["items"][0]["id"] != first["items"][0]["id"]
    edit = client.patch(f"{url}/{mark['id']}", json={"note": "新版备注", "expected_version": 1})
    assert edit.status_code == 200 and edit.json()["data"]["version"] == 2
    assert (
        client.patch(
            f"{url}/{mark['id']}", json={"note": "旧版", "expected_version": 1}
        ).status_code
        == 409
    )
    assert client.delete(f"{url}/{mark['id']}?expected_version=1").status_code == 409
    assert client.delete(f"{url}/{mark['id']}?expected_version=2").status_code == 204
    assert client.delete(f"/api/books/{data['book_id']}").status_code == 204
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Bookmark)) == 0


def test_bookmarks_validate_versions_chapters_positions(migrated_client: TestClient) -> None:
    data, chapter = fixture(migrated_client)
    other, foreign = fixture(migrated_client, "其它章\n正文\n")
    url = f"/api/books/{data['book_id']}/bookmarks"
    body = {
        "book_version_id": data["book_version_id"],
        "chapter_id": chapter["id"],
        "position_cp": chapter["start_cp"],
    }
    for patch in [
        {"book_version_id": other["book_version_id"]},
        {"chapter_id": foreign["id"]},
        {"position_cp": chapter["end_cp"]},
        {"note": "长" * 513},
    ]:
        assert migrated_client.post(url, json=body | patch).status_code in (400, 422)


def test_resume_content_returns_whole_anchor_paragraph(migrated_client: TestClient) -> None:
    data, chapter = fixture(migrated_client)
    url = f"/api/books/{data['book_id']}/content"
    params = {"chapter_id": chapter["id"], "start_cp": 6}
    response = migrated_client.get(url, params=params)
    assert response.status_code == 200, response.text
    nodes = response.json()["data"]["nodes"]
    assert nodes[0]["text"] == "「😀你好。」"
    assert (
        migrated_client.get(url, params=params | {"start_cp": chapter["end_cp"]}).status_code == 422
    )
