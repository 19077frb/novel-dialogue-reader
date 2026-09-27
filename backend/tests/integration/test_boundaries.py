"""T18 集成测试：密钥与资源边界。

门槛：越界（`..`、绝对路径、被篡改的库记录）一律**拒绝**，不返回 500、不回显磁盘路径，
也不把数据目录之外的文件当作资源/导出件下发。密钥相关断言见 `test_model_profiles.py`（此处不重复）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from fixtures.epub_factory import build_epub, ruby_and_image_spec
from ndr.config import Settings
from ndr.domain.enums import ExportFormat
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.models import BookVersion, ExportArtifact
from ndr.storage.transactions import transaction

IMAGE_BYTES = b"\x89PNG\r\n\x1a\nboundary-fixture-bytes"
TXT_SAMPLE = "第一章 雨夜\n「雨停了。」少女合上伞。\n"


def _import(client: TestClient, filename: str, raw: bytes, content_type: str) -> dict:
    response = client.post(
        "/api/books/import", files={"file": (filename, raw, content_type)}
    )
    assert response.status_code == 202, response.text
    return response.json()["data"]


def test_resource_route_rejects_traversal_like_ids(
    migrated_client: TestClient, migrated_settings: Settings
) -> None:
    data = _import(
        migrated_client, "boundary.epub", build_epub(ruby_and_image_spec(IMAGE_BYTES)),
        "application/epub+zip",
    )
    book_id = data["book_id"]
    data_dir = str(migrated_settings.data_dir)

    # 正对照：登记过的资源能读
    ok = migrated_client.get(f"/api/books/{book_id}/resources/r0001")
    assert ok.status_code == 200, ok.text
    assert ok.content == IMAGE_BYTES

    for resource_id in (
        "..%2F..%2Fsource.bin",
        "r0001%2F..%2F..%2Fsource.bin",
        "%2E%2E",
        "%2E%2E%2F%2E%2E%2Fsource.bin",
        "C:%5CWindows%5Cwin.ini",
        "r0001%00.png",
    ):
        response = migrated_client.get(f"/api/books/{book_id}/resources/{resource_id}")
        assert response.status_code == 404, (resource_id, response.status_code, response.text)
        assert response.json()["error"]["code"] == "NOT_FOUND"
        assert data_dir not in response.text  # 不回显磁盘路径

    # 未编码的 `..` 会被 ASGI 归一化成 `/api/books/{id}`（到不了资源处理器）：
    # 结果只会是书籍详情，绝不下发资源字节，也不回显数据目录。
    normalized = migrated_client.get(f"/api/books/{book_id}/resources/..")
    assert normalized.content != IMAGE_BYTES
    assert data_dir not in normalized.text


def test_resource_route_refuses_tampered_source_path(
    migrated_client: TestClient, migrated_settings: Settings
) -> None:
    data = _import(
        migrated_client, "tampered.epub", build_epub(ruby_and_image_spec(IMAGE_BYTES)),
        "application/epub+zip",
    )
    book_id = data["book_id"]

    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            version = session.execute(
                select(BookVersion).where(BookVersion.book_id == book_id)
            ).scalars().first()
            assert version is not None
            version.source_path = "../outside-data-dir.epub"
        response = migrated_client.get(f"/api/books/{book_id}/resources/r0001")
    finally:
        engine.dispose()

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert "outside-data-dir" not in response.text


def test_export_download_refuses_path_outside_data_dir(
    migrated_client: TestClient, migrated_settings: Settings
) -> None:
    data = _import(migrated_client, "boundary.txt", TXT_SAMPLE.encode("utf-8"), "text/plain")
    book_id = data["book_id"]
    preview = migrated_client.post(
        f"/api/books/{book_id}/exports/preview", json={"visibility_policy": "reread"}
    )
    assert preview.status_code == 200, preview.text
    snapshot_id = preview.json()["data"]["snapshot_id"]

    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            artifact = ExportArtifact(
                snapshot_id=snapshot_id,
                format=ExportFormat.EPUB,
                options_json="{}",
                exporter_version="test-boundary",
                fingerprint="f" * 64,
                state="COMPLETED",
                relative_path="../outside-data-dir.epub",
                byte_size=1,
                validation_json="{}",
            )
            session.add(artifact)
            session.flush()
            artifact_id = artifact.id
        response = migrated_client.get(f"/api/exports/{artifact_id}/download")
    finally:
        engine.dispose()

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert "outside-data-dir" not in response.text
