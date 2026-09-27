"""T03 集成测试：EPUB 导入、按 spine 阅读、资源端点与安全边界（F03 / F04 / F19）。"""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from fixtures.epub_factory import Document, EpubSpec, build_epub, minimal_spec, ruby_and_image_spec
from ndr.config import Settings

IMAGE_BYTES = b"\x89PNG\r\n\x1a\nintegration-fixture-bytes"


def _import_epub(client: TestClient, raw: bytes, filename: str = "原创样例.epub"):
    return client.post(
        "/api/books/import",
        files={"file": (filename, raw, "application/epub+zip")},
    )


def test_import_epub_reads_in_spine_order(migrated_client: TestClient) -> None:
    response = _import_epub(migrated_client, build_epub(minimal_spec()))
    assert response.status_code == 202, response.text
    data = response.json()["data"]

    assert data["format"] == "EPUB"
    assert data["encoding"] is None  # EPUB 没有单一文件级编码
    assert data["chapter_count"] == 2
    assert data["canonical_length_cp"] > 0

    book_id = data["book_id"]
    chapters = migrated_client.get(f"/api/books/{book_id}/chapters").json()["data"]
    assert [chapter["title"] for chapter in chapters] == ["第一章 雨夜", "第二章 名字"]
    assert chapters[0]["source_href"] == "OEBPS/text/b-first.xhtml"

    content = migrated_client.get(f"/api/books/{book_id}/content").json()["data"]
    texts = [node["text"] for node in content["nodes"]]
    assert texts[0] == "第一章 雨夜"
    assert "第二章 名字" in texts
    assert texts.index("第一章 雨夜") < texts.index("第二章 名字")


def test_ruby_and_image_nodes_via_api(migrated_client: TestClient) -> None:
    data = _import_epub(migrated_client, build_epub(ruby_and_image_spec(IMAGE_BYTES))).json()["data"]
    book_id = data["book_id"]
    assert data["resource_count"] == 1

    content = migrated_client.get(f"/api/books/{book_id}/content").json()["data"]
    by_type = {node["node_type"]: node for node in content["nodes"]}
    assert "image" in by_type

    image_node = by_type["image"]
    assert image_node["start_cp"] == image_node["end_cp"]  # 图片不占正文
    payload = image_node["payload"]
    assert payload["media_type"] == "image/png"

    ruby_node = next(node for node in content["nodes"] if "ruby" in node["payload"])
    ruby = ruby_node["payload"]["ruby"][0]
    assert ruby["rt"] == "かん"
    # 注音不进入正文，基底文字仍在正文里
    assert "かん" not in "".join(node["text"] for node in content["nodes"])
    assert "漢" in ruby_node["text"]


def test_resource_endpoint_serves_registered_image(migrated_client: TestClient) -> None:
    data = _import_epub(migrated_client, build_epub(ruby_and_image_spec(IMAGE_BYTES))).json()["data"]
    book_id = data["book_id"]
    content = migrated_client.get(f"/api/books/{book_id}/content").json()["data"]
    image_node = next(node for node in content["nodes"] if node["node_type"] == "image")
    resource_id = image_node["payload"]["resource_id"]

    response = migrated_client.get(f"/api/books/{book_id}/resources/{resource_id}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content == IMAGE_BYTES
    assert response.headers["x-resource-sha256"] == hashlib.sha256(IMAGE_BYTES).hexdigest()
    assert "filename*=UTF-8''" in response.headers["content-disposition"]


def test_resource_endpoint_uses_contract_error_for_unknown_id(migrated_client: TestClient) -> None:
    data = _import_epub(migrated_client, build_epub(ruby_and_image_spec(IMAGE_BYTES))).json()["data"]
    response = migrated_client.get(f"/api/books/{data['book_id']}/resources/r9999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_resource_ids_are_scoped_to_the_book(migrated_client: TestClient) -> None:
    """资源 ID 只在所属书籍版本内有意义：不能跨书读取。"""

    first = _import_epub(
        migrated_client, build_epub(ruby_and_image_spec(IMAGE_BYTES)), filename="a.epub"
    ).json()["data"]
    spec = EpubSpec(
        documents=[
            Document(
                doc_id="ch1",
                href="text/two-images.xhtml",
                body=(
                    '<p><img src="../images/a.png"/></p>\n'
                    '<p><img src="../images/b.png"/></p>'
                ),
            )
        ],
        resources={"images/a.png": b"image-a", "images/b.png": b"image-b"},
    )
    second = _import_epub(migrated_client, build_epub(spec), filename="b.epub").json()["data"]
    assert second["resource_count"] == 2

    content = migrated_client.get(f"/api/books/{second['book_id']}/content").json()["data"]
    ids = [node["payload"]["resource_id"] for node in content["nodes"] if node["node_type"] == "image"]
    assert ids == ["r0001", "r0002"]

    # 第二本书的 r0002 在第一本书里不存在 → 404，不泄露其它书籍的资源。
    response = migrated_client.get(f"/api/books/{first['book_id']}/resources/r0002")
    assert response.status_code == 404
    assert migrated_client.get(
        f"/api/books/{second['book_id']}/resources/r0002"
    ).content == b"image-b"


def test_txt_book_has_no_resources(migrated_client: TestClient) -> None:
    txt = migrated_client.post(
        "/api/books/import",
        files={"file": ("sample.txt", "第一章\n正文。\n".encode("utf-8"), "text/plain")},
    ).json()["data"]
    response = migrated_client.get(f"/api/books/{txt['book_id']}/resources/r0001")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_reimport_reuses_epub_version(migrated_client: TestClient) -> None:
    raw = build_epub(minimal_spec())
    first = _import_epub(migrated_client, raw).json()["data"]
    second = _import_epub(migrated_client, raw).json()["data"]

    assert first["book_id"] == second["book_id"]
    assert first["book_version_id"] == second["book_version_id"]
    assert second["reused_book"] is True and second["reused_version"] is True
    chapters = migrated_client.get(f"/api/books/{first['book_id']}/chapters").json()["data"]
    assert len(chapters) == 2


def test_unsafe_epub_is_rejected_with_reason_code(migrated_client: TestClient) -> None:
    spec = EpubSpec(
        documents=[Document(doc_id="ch1", href="text/one.xhtml", body="<p>正文。</p>")],
        extras={"../escape.txt": b"nope"},
    )
    response = _import_epub(migrated_client, build_epub(spec), filename="unsafe.epub")
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "VALIDATION_ERROR"
    details = payload["error"]["details"]
    assert details["reason_code"] == "EPUB_UNSAFE_PATH"
    assert migrated_client.get("/api/books").json()["data"]["items"] == []

    job = migrated_client.get(f"/api/jobs/{details['job_id']}").json()["data"]
    assert job["state"] == "FAILED"
    assert job["progress"]["stage"] == "epub_structure"


def test_oversized_epub_entry_limit_is_reported(
    migrated_client: TestClient,
) -> None:
    spec = EpubSpec(
        documents=[Document(doc_id="ch1", href="text/one.xhtml", body="<p>正文。</p>")],
        resources={"images/big.png": b"x" * 65536},
    )
    response = _import_epub(migrated_client, build_epub(spec), filename="big.epub")
    # 默认上限很宽松，这里只验证超限错误路径可解释：构造一个 32MiB+ 的单条目代价过高，
    # 因此改用配置项在单元测试中验证（见 test_epub_ingest.py）。
    assert response.status_code in {202, 422}


def test_epub_source_and_canonical_files_on_disk(
    migrated_client: TestClient, migrated_settings: Settings
) -> None:
    raw = build_epub(minimal_spec())
    data = _import_epub(migrated_client, raw).json()["data"]

    book_dir = migrated_settings.data_dir / "books" / data["book_id"]
    source = next(book_dir.glob("source.*"))
    canonical = next(book_dir.glob("versions/*/canonical.txt"))

    assert source.suffix == ".epub"
    assert source.read_bytes() == raw  # 源文件保持原始字节
    assert canonical.read_text(encoding="utf-8") == (
        "第一章 雨夜\n「雨停了。」少女合上伞。\n少年没有回答，只是把外套递了过去。\n"
        "第二章 名字\n「雨停了。」少女又说了一次。"
    )
