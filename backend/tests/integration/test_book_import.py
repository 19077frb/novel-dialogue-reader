"""T02 集成测试：通过 HTTP 导入 TXT 并完整读取原文（F01 / F02 / F11）。

覆盖：202 + IMPORT 任务、无模型读取全文、错误编码不静默丢字、重复导入复用版本、
换编码生成新版本、格式/大小限制、内容分页与范围校验、契约错误体。
"""

from __future__ import annotations

import hashlib
import json

from fastapi.testclient import TestClient

from ndr.app import create_app
from ndr.config import Settings

SAMPLE = (
    "第一章 雨夜\n"
    "「雨停了。」少女合上伞。\n"
    "少年没有回答。\n"
    "\n"
    "「……谢谢。」她低声说。\n"
    "第二章 转折\n"
    "𠮷野家的猫🐈跳上窗台。\n"
    "「雨停了。」少女合上伞。\n"
)


def _import(client: TestClient, raw: bytes, *, filename: str = "雨夜.txt", encoding: str | None = None):
    data = {"encoding": encoding} if encoding else {}
    return client.post(
        "/api/books/import",
        files={"file": (filename, raw, "text/plain")},
        data=data,
    )


def _lines(text: str) -> list[str]:
    return [line for line in text.split("\n") if line.strip()]


def test_import_txt_then_read_whole_book(migrated_client: TestClient) -> None:
    response = _import(migrated_client, SAMPLE.encode("utf-8"))
    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["request_id"] == response.headers["x-request-id"]

    data = payload["data"]
    assert data["import_status"] == "COMPLETED"
    assert data["encoding"] == "utf-8"
    assert data["chapter_count"] == 2
    assert data["canonical_length_cp"] == len(SAMPLE)
    assert data["reused_book"] is False and data["reused_version"] is False

    job = migrated_client.get(f"/api/jobs/{data['job_id']}").json()["data"]
    assert job["kind"] == "IMPORT"
    assert job["state"] == "COMPLETED"
    assert job["book_id"] == data["book_id"]

    books = migrated_client.get("/api/books").json()["data"]
    assert [item["id"] for item in books["items"]] == [data["book_id"]]
    assert books["next_cursor"] is None

    book = migrated_client.get(f"/api/books/{data['book_id']}").json()["data"]
    assert book["id"] == data["book_id"]
    assert book["format"] == "TXT"
    assert book["active_version"]["canonical_length_cp"] == len(SAMPLE)
    assert book["active_version"]["encoding"] == "utf-8"
    # 不向客户端暴露磁盘路径。
    assert "path" not in json.dumps(book)

    chapters = migrated_client.get(f"/api/books/{data['book_id']}/chapters").json()["data"]
    assert [(item["ordinal"], item["title"]) for item in chapters] == [
        (0, "第一章 雨夜"),
        (1, "第二章 转折"),
    ]

    content = migrated_client.get(f"/api/books/{data['book_id']}/content").json()["data"]
    assert content["canonical_length_cp"] == len(SAMPLE)
    assert [node["text"] for node in content["nodes"]] == _lines(SAMPLE)
    assert content["next_cursor"] is None
    # 原文没有丢字、没有替换符，节点范围能切回原文。
    assert all("\ufffd" not in node["text"] for node in content["nodes"])

    first_chapter = migrated_client.get(
        f"/api/books/{data['book_id']}/content", params={"chapter_id": chapters[0]["id"]}
    ).json()["data"]
    assert [node["text"] for node in first_chapter["nodes"]] == [
        "第一章 雨夜",
        "「雨停了。」少女合上伞。",
        "少年没有回答。",
        "「……谢谢。」她低声说。",
    ]


def test_wrong_encoding_is_reported_and_no_book_is_created(migrated_client: TestClient) -> None:
    raw = SAMPLE.encode("gb18030")

    response = _import(migrated_client, raw, encoding="utf-8")
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "VALIDATION_ERROR"
    details = payload["error"]["details"]
    assert details["preview_is_lossy"] is True
    assert details["preview"]
    assert any(
        candidate["encoding"] == "gb18030" and candidate["ok"] is True
        for candidate in details["candidates"]
    )
    assert migrated_client.get("/api/books").json()["data"]["items"] == []

    failed_job = migrated_client.get(f"/api/jobs/{details['job_id']}").json()["data"]
    assert failed_job["state"] == "FAILED"
    assert failed_job["last_error"]

    ok = _import(migrated_client, raw, encoding="gb18030")
    assert ok.status_code == 202
    assert ok.json()["data"]["encoding"] == "gb18030"

    book_id = ok.json()["data"]["book_id"]
    content = migrated_client.get(f"/api/books/{book_id}/content").json()["data"]
    assert [node["text"] for node in content["nodes"]] == _lines(SAMPLE)


def test_auto_detected_import_without_encoding(migrated_client: TestClient) -> None:
    response = _import(migrated_client, SAMPLE.encode("gb18030"))
    assert response.status_code == 202
    assert response.json()["data"]["encoding"] == "gb18030"


def test_repeated_import_reuses_book_and_version(migrated_client: TestClient) -> None:
    raw = SAMPLE.encode("utf-8")
    first = _import(migrated_client, raw).json()["data"]
    second = _import(migrated_client, raw).json()["data"]

    assert second["book_id"] == first["book_id"]
    assert second["book_version_id"] == first["book_version_id"]
    assert second["reused_book"] is True
    assert second["reused_version"] is True

    books = migrated_client.get("/api/books").json()["data"]["items"]
    assert len(books) == 1
    chapters = migrated_client.get(f"/api/books/{first['book_id']}/chapters").json()["data"]
    assert len(chapters) == 2  # 没有因为重复导入而重复插入章节


def test_changing_encoding_creates_new_version(migrated_client: TestClient) -> None:
    raw = "第一章\n雨停了。\n".encode("gb18030")

    first = _import(migrated_client, raw, encoding="gb18030").json()["data"]
    second = _import(migrated_client, raw, encoding="big5").json()["data"]

    assert first["book_id"] == second["book_id"]  # 同一份原始字节 → 同一本书
    assert first["book_version_id"] != second["book_version_id"]  # 换编码 → 新版本
    assert second["reused_book"] is True and second["reused_version"] is False

    book = migrated_client.get(f"/api/books/{first['book_id']}").json()["data"]
    assert book["active_version_id"] == second["book_version_id"]
    assert book["active_version"]["encoding"] == "big5"
    # 两个版本的 canonical 文本不同，但都指向同一份源文件。
    assert book["source_sha256"] == hashlib.sha256(raw).hexdigest()


def test_import_rejects_unsupported_extension(migrated_client: TestClient) -> None:
    response = _import(migrated_client, b"dummy", filename="book.md")
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_import_rejects_invalid_epub_payload(migrated_client: TestClient) -> None:
    """扩展名是 .epub 但内容不是合法容器 → 422 且给出具体原因。"""

    response = _import(migrated_client, b"definitely not a zip", filename="broken.epub")
    assert response.status_code == 422
    details = response.json()["error"]["details"]
    assert details["reason_code"] == "EPUB_NOT_A_ZIP"
    assert migrated_client.get("/api/books").json()["data"]["items"] == []


def test_import_rejects_empty_file(migrated_client: TestClient) -> None:
    response = _import(migrated_client, b"")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_import_rejects_oversized_file(tmp_settings: Settings) -> None:
    settings = Settings(data_dir=tmp_settings.data_dir, max_import_bytes=32)
    from ndr.storage.migrate import run_migrations

    run_migrations(settings)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/books/import",
            files={"file": ("big.txt", b"x" * 100, "text/plain")},
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_content_pagination_and_range_validation(migrated_client: TestClient) -> None:
    book_id = _import(migrated_client, SAMPLE.encode("utf-8")).json()["data"]["book_id"]

    first_page = migrated_client.get(
        f"/api/books/{book_id}/content", params={"limit": 2}
    ).json()["data"]
    assert len(first_page["nodes"]) == 2
    assert first_page["next_cursor"]

    second_page = migrated_client.get(
        f"/api/books/{book_id}/content",
        params={"limit": 2, "cursor": first_page["next_cursor"]},
    ).json()["data"]
    assert [node["text"] for node in second_page["nodes"]] == [
        "少年没有回答。",
        "「……谢谢。」她低声说。",
    ]

    invalid = migrated_client.get(
        f"/api/books/{book_id}/content", params={"start_cp": 10, "end_cp": 5}
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"

    too_far = migrated_client.get(
        f"/api/books/{book_id}/content", params={"end_cp": len(SAMPLE) + 1}
    )
    assert too_far.status_code == 422

    unknown_chapter = migrated_client.get(
        f"/api/books/{book_id}/content", params={"chapter_id": "missing"}
    )
    assert unknown_chapter.status_code == 404


def test_missing_book_and_job_use_contract_errors(migrated_client: TestClient) -> None:
    book = migrated_client.get("/api/books/nope")
    assert book.status_code == 404
    assert book.json()["error"]["code"] == "NOT_FOUND"

    job = migrated_client.get("/api/jobs/nope")
    assert job.status_code == 404
    assert job.json()["error"]["code"] == "NOT_FOUND"


def test_source_and_canonical_files_live_inside_data_dir(
    migrated_client: TestClient, migrated_settings: Settings
) -> None:
    raw = SAMPLE.encode("utf-8")
    data = _import(migrated_client, raw).json()["data"]

    book_dir = migrated_settings.data_dir / "books" / data["book_id"]
    source = next(book_dir.glob("source.*"))
    canonical = next(book_dir.glob("versions/*/canonical.txt"))

    assert hashlib.sha256(source.read_bytes()).hexdigest() == hashlib.sha256(raw).hexdigest()
    assert canonical.read_text(encoding="utf-8") == SAMPLE
    # 源文件保持原始字节（原文不可变）。
    assert source.read_bytes() == raw
