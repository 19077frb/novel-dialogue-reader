from fastapi.testclient import TestClient

from ndr.ingest.txt import parse_txt
from ndr.storage.models import Chapter


def test_txt_avoids_narrative_and_adjacent_duplicate_headings() -> None:
    novel = "第十卷 序章\n\n序章\n「你好。」\n第三节的体育课运气不好的午休时间、然後还有刚刚的大失败……\n正文\n第一章 开始\n「再见。」\n"
    parsed = parse_txt(novel.encode())
    assert [chapter.title for chapter in parsed.chapters] == ["第十卷 序章", "第一章 开始"]
    assert parsed.canonical_text == novel
    assert any("合并" in warning for warning in parsed.warnings)


def test_import_reports_automatic_preprocessing_and_preserves_edits_on_reimport(
    migrated_client: TestClient,
) -> None:
    client = migrated_client
    raw = "第十卷 序章\n\n序章\n“没有闭合\n“下一句。”\n".encode()
    imported = client.post("/api/books/import", files={"file": ("auto.txt", raw)}).json()["data"]
    assert imported["chapter_repairs_applied"] == 1
    assert imported["quote_repairs_applied"] == 1
    assert imported["chapter_count"] == 1
    url = f"/api/books/{imported['book_id']}"
    chapters = client.get(f"{url}/chapters").json()["data"]
    assert chapters[0]["title"] == "第十卷 序章"
    normalization = client.get(f"{url}/quote-normalizations").json()["data"]
    assert normalization[0]["status"] == "ACTIVE"
    again = client.post("/api/books/import", files={"file": ("auto.txt", raw)}).json()["data"]
    assert again["reused_version"]
    assert again["chapter_repairs_applied"] == again["quote_repairs_applied"] == 0


def test_suggest_rename_merge_preserves_text_quotes_and_bookmarks(
    migrated_client: TestClient,
) -> None:
    client = migrated_client
    text = "第十卷 序章\n「一。」\n序章\n「二。」\n第一章 开始\n「三。」\n"
    imported = client.post(
        "/api/books/import", files={"file": ("book.txt", text.encode(), "text/plain")}
    ).json()["data"]
    book = imported["book_id"]
    url = f"/api/books/{book}"
    chapters = client.get(f"{url}/chapters").json()["data"]
    assert len(chapters) == 3
    original_quotes = client.get(f"{url}/quotes").json()["data"]["items"]
    hash_before = client.get(url).json()["data"]["active_version"]["canonical_sha256"]
    mark = client.post(
        f"{url}/bookmarks",
        json={
            "book_version_id": imported["book_version_id"],
            "chapter_id": chapters[1]["id"],
            "position_cp": chapters[1]["start_cp"],
        },
    ).json()["data"]
    suggestions = client.get(f"{url}/chapter-repairs").json()["data"]
    assert len(suggestions) == 1 and suggestions[0]["merge_previous"]
    response = client.post(
        f"{url}/chapter-repairs",
        json={
            "book_version_id": imported["book_version_id"],
            "repairs": [
                {
                    "chapter_id": chapters[1]["id"],
                    "expected_title": "序章",
                    "title": "第十卷 序章",
                    "merge_previous": True,
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    merged = response.json()["data"]
    assert len(merged) == 2 and merged[0]["end_cp"] == chapters[1]["end_cp"]
    assert client.get(url).json()["data"]["active_version"]["canonical_sha256"] == hash_before
    content = client.get(f"{url}/content", params={"chapter_id": merged[0]["id"]}).json()["data"][
        "nodes"
    ]
    assert len({node["node_id"] for node in content}) == len(content)
    assert "「二。」" in [node["text"] for node in content]
    after_quotes = client.get(f"{url}/quotes").json()["data"]["items"]
    assert [quote["quote_id"] for quote in after_quotes] == [quote["quote_id"] for quote in original_quotes]
    assert (
        client.get(f"{url}/bookmarks").json()["data"]["items"][0]["chapter_id"] == chapters[0]["id"]
    )
    assert client.get(f"{url}/bookmarks").json()["data"]["items"][0]["id"] == mark["id"]
    bad = client.post(
        f"{url}/chapter-repairs",
        json={
            "book_version_id": imported["book_version_id"],
            "repairs": [
                {"chapter_id": merged[0]["id"], "expected_title": "旧标题", "title": "新标题"}
            ],
        },
    )
    assert bad.status_code == 409
    factory = client.app.state.session_factory
    with factory.begin() as session:
        session.get(Chapter, merged[0]["id"]).dialogue_processed = True
    conflict = client.post(
        f"{url}/chapter-repairs",
        json={
            "book_version_id": imported["book_version_id"],
            "repairs": [
                {
                    "chapter_id": merged[1]["id"],
                    "expected_title": merged[1]["title"],
                    "title": "合并",
                    "merge_previous": True,
                }
            ],
        },
    )
    assert conflict.status_code == 409
    assert len(client.get(f"{url}/chapters").json()["data"]) == 2
