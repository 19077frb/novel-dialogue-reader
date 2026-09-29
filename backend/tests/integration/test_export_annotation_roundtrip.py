"""集成测试：导出 EPUB → 重新导入 → 标注（颜色/标签）恢复。

覆盖：

- EPUB 包内含机器可读标注清单且 OPF 声明了私有属性；
- 回导后按「章节 + 对白原文」恢复出相同的说话人标签；
- 恢复结果 user_locked，且在初读/重读投影下都不被遮断；
- 非 NDR 导出的 EPUB（无清单）行为不变。
"""

from __future__ import annotations

import io
import json
import zipfile

from fastapi.testclient import TestClient
from sqlalchemy import select

from fixtures.corrections import (
    annotations_of,
    create_fake_profile,
    import_sample,
    run_deterministic_job,
    session_scope,
)
from ndr.storage.models import Chapter
from ndr.storage.transactions import transaction


def _preview(client: TestClient, book_id: str) -> dict:
    response = client.post(
        f"/api/books/{book_id}/exports/preview",
        json={"visibility_policy": "reread", "style": {"preset": "color_and_label"}},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _export(client: TestClient, book_id: str, snapshot_id: str) -> bytes:
    response = client.post(
        f"/api/books/{book_id}/exports",
        json={
            "snapshot_id": snapshot_id,
            "format": "epub",
            "style": {"preset": "color_and_label"},
            "idempotency_key": "k-roundtrip",
        },
    )
    assert response.status_code == 201, response.text
    artifact = response.json()["data"]
    assert artifact["state"] == "COMPLETED", artifact
    download = client.get(f"/api/exports/{artifact['id']}/download")
    assert download.status_code == 200
    return download.content


def _rewrite_manifest(epub_bytes: bytes, mutate) -> bytes:  # noqa: ANN001 - 测试回调
    source = zipfile.ZipFile(io.BytesIO(epub_bytes))
    manifest = json.loads(source.read("OEBPS/annotations.json"))
    mutate(manifest)
    output = io.BytesIO()
    with source, zipfile.ZipFile(output, "w") as target:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == "OEBPS/annotations.json":
                payload = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
            target.writestr(info, payload)
    return output.getvalue()


def test_exported_epub_roundtrip_restores_annotations(
    fake_provider_client: TestClient, migrated_settings
) -> None:
    data = import_sample(fake_provider_client)
    book_id = data["book_id"]
    profile_id = create_fake_profile(fake_provider_client, name="回环提供方")
    run_deterministic_job(
        migrated_settings, fake_provider_client, book_id=book_id, profile_id=profile_id, key="k-rt"
    )
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        first_chapter = session.execute(
            select(Chapter).where(Chapter.book_version_id == data["book_version_id"])
            .order_by(Chapter.ordinal)
        ).scalars().first()
        assert first_chapter is not None
        first_chapter.dialogue_processed = True

    preview = _preview(fake_provider_client, book_id)
    original = annotations_of(fake_provider_client, book_id, reading_mode="reread")
    styled = [
        item for item in original["items"] if item["label"] and item["color_index"] is not None
    ]
    assert styled

    epub_bytes = _export(fake_provider_client, book_id, preview["snapshot_id"])

    # 包内有清单，OPF 声明私有属性
    with zipfile.ZipFile(io.BytesIO(epub_bytes)) as archive:
        names = archive.namelist()
        assert "OEBPS/annotations.json" in names
        opf = archive.read("OEBPS/content.opf").decode("utf-8")
        assert 'properties="ndr:annotations"' in opf
        assert 'prefix="ndr: ' in opf
        assert "epub:prefix=" not in opf

    # 重新导入导出的 EPUB
    reimport = fake_provider_client.post(
        "/api/books/import",
        files={"file": ("roundtrip.epub", epub_bytes, "application/epub+zip")},
    )
    assert reimport.status_code == 202, reimport.text
    new_book_id = reimport.json()["data"]["book_id"]
    assert new_book_id != book_id
    restored_chapters = fake_provider_client.get(
        f"/api/books/{new_book_id}/chapters"
    ).json()["data"]
    assert restored_chapters[0]["dialogue_processed"] is True

    restored = annotations_of(fake_provider_client, new_book_id, reading_mode="reread")

    original_content = fake_provider_client.get(f"/api/books/{book_id}/content").json()["data"]
    restored_content = fake_provider_client.get(f"/api/books/{new_book_id}/content").json()["data"]
    assert [node["text"] for node in restored_content["nodes"] if node["text"]] == [
        node["text"] for node in original_content["nodes"] if node["text"]
    ]

    # 同一批对白都恢复了标签
    def label_map(payload: dict) -> dict[str, str]:
        return {
            item["label"]: item["quote_id"]
            for item in payload["items"]
            if item["label"] and not item["withheld"]
        }

    assert len(restored["items"]) == len(original["items"])
    assert {item["label"] for item in restored["items"]} == {
        item["label"] for item in original["items"] if item["label"]
    }
    original_colors = [item["color_index"] for item in original["items"] if item["label"]]
    restored_colors = [item["color_index"] for item in restored["items"] if item["label"]]
    assert restored_colors == original_colors
    # 同一标签 → 同一颜色（恢复后跨场景也保持一致）
    color_by_label: dict[str, int | None] = {}
    for item in restored["items"]:
        if item["label"]:
            previous = color_by_label.setdefault(item["label"], item["color_index"])
            assert previous == item["color_index"]

    # 恢复结果不被初读 horizon 遮断（阅读位置从 0 开始）
    initial = fake_provider_client.get(
        f"/api/books/{new_book_id}/annotations", params={"reading_mode": "initial"}
    )
    assert initial.status_code == 200
    assert initial.json()["data"]["counts"]["withheld"] == 0

    # 引语级别：恢复的 quote 数与原来一致（顶层）
    assert label_map(restored).keys() == label_map(original).keys()


def test_plain_epub_import_is_unaffected(migrated_client: TestClient) -> None:
    from fixtures.epub_factory import build_epub, ruby_and_image_spec

    response = migrated_client.post(
        "/api/books/import",
        files={"file": ("plain.epub", build_epub(ruby_and_image_spec()), "application/epub+zip")},
    )
    assert response.status_code == 202, response.text
    data = response.json()["data"]
    assert data["import_status"] == "COMPLETED"


def test_restore_keeps_same_named_identities_separate_and_skips_unmatched(
    fake_provider_client: TestClient, migrated_settings
) -> None:
    data = import_sample(fake_provider_client)
    book_id = data["book_id"]
    profile_id = create_fake_profile(fake_provider_client, name="同名人物回环提供方")
    run_deterministic_job(
        migrated_settings,
        fake_provider_client,
        book_id=book_id,
        profile_id=profile_id,
        key="k-same-name",
    )
    preview = _preview(fake_provider_client, book_id)
    epub_bytes = _export(fake_provider_client, book_id, preview["snapshot_id"])

    def mutate(manifest: dict) -> None:
        assert len(manifest["annotations"]) >= 2
        first = manifest["speakers"][0]
        manifest["speakers"].append(
            {
                "key": "same-name-second",
                "color_index": 1,
                "label": first["label"],
                "description": "同名但不同的第二个人物",
            }
        )
        manifest["annotations"][1]["speaker"] = "same-name-second"
        manifest["annotations"].insert(
            0,
            {
                **manifest["annotations"][0],
                "quote_text": "这句对白不存在，不能吞掉后续匹配位置",
            },
        )

    modified = _rewrite_manifest(epub_bytes, mutate)
    reimport = fake_provider_client.post(
        "/api/books/import",
        files={"file": ("same-name.epub", modified, "application/epub+zip")},
    )
    assert reimport.status_code == 202, reimport.text
    restored = annotations_of(
        fake_provider_client,
        reimport.json()["data"]["book_id"],
        reading_mode="reread",
    )
    visible = [item for item in restored["items"] if item["label"]]
    assert len(visible) >= 2
    labels = {item["label"] for item in visible}
    colors = {item["color_index"] for item in visible}
    assert len(labels) == 1
    assert len(colors) >= 2
