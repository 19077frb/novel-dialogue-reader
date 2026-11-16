from fixtures.toc_epub import nested_epub


def test_new_txt_with_title_separator_has_explicit_flat_navigation(migrated_client):
    client = migrated_client
    response = client.post("/api/books/import", files={
        "file": ("plain.txt", "第一章 风 · 雨\n「原创对白。」\n".encode(), "text/plain"),
    })
    assert response.status_code == 202, response.text
    imported = response.json()["data"]
    url = f"/api/books/{imported['book_id']}"
    book = client.get(url).json()["data"]
    assert book["active_version"]["toc"] == []
    chapters = client.get(f"{url}/chapters").json()["data"]
    assert chapters[0]["title"] == "第一章 风 · 雨"


def test_import_persists_tree_and_repairs_redirect_targets(migrated_client):
    client = migrated_client
    response = client.post("/api/books/import", files={"file": ("nested.epub", nested_epub())})
    assert response.status_code == 202, response.text
    imported = response.json()["data"]
    url = f"/api/books/{imported['book_id']}"
    version = client.get(url).json()["data"]["active_version"]
    original_hash = version["canonical_sha256"]
    toc = version["toc"]
    chapters = client.get(f"{url}/chapters").json()["data"]
    assert [n["chapter_id"] for n in toc[:4]] == [None, *(c["id"] for c in chapters)]
    response = client.post(f"{url}/chapter-repairs", json={
        "book_version_id": imported["book_version_id"], "repairs": [{
            "chapter_id": chapters[2]["id"], "expected_title": chapters[2]["title"],
            "title": "合并后的第一章", "merge_previous": True,
        }],
    })
    assert response.status_code == 200, response.text
    version = client.get(url).json()["data"]["active_version"]
    assert version["canonical_sha256"] == original_hash
    assert [n["target_cp"] for n in version["toc"]] == [n["target_cp"] for n in toc]
    assert version["toc"][3]["chapter_id"] == chapters[1]["id"]
    assert version["toc"][2]["title"] == "合并后的第一章"
    assert version["toc"][3]["title"] == "第二节"
    current = response.json()["data"][1]
    renamed = client.post(f"{url}/chapter-repairs", json={
        "book_version_id": imported["book_version_id"], "repairs": [{
            "chapter_id": current["id"], "expected_title": current["title"], "title": "新名字",
        }],
    })
    assert renamed.status_code == 200
    current_toc = client.get(url).json()["data"]["active_version"]["toc"]
    assert current_toc[2]["title"] == "新名字"
    assert current_toc[3]["title"] == "第二节"
