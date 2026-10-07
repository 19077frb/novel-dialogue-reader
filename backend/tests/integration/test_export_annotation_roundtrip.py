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

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from fixtures.corrections import (
    annotations_of,
    create_fake_profile,
    import_sample,
    run_deterministic_job,
    session_scope,
)
from ndr.storage.models import Annotation, BookCharacter, Chapter, Quote, Scene, SpeakerGroup
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


@pytest.mark.parametrize("depth_metadata", ["current", "legacy", "invalid"])
def test_nested_thought_colors_and_owners_survive_epub_roundtrip(
    fake_provider_client, depth_metadata,
):
    client = fake_provider_client
    sample = "第一章\n「她想『明天见』，然后离开了。」\n『明天见』\n"
    data = client.post("/api/books/import", files={
        "file": ("nested-thought.txt", sample.encode(), "text/plain"),
    }).json()["data"]
    with transaction(client.app.state.session_factory) as session:
        quotes = list(session.scalars(select(Quote).where(
            Quote.book_version_id == data["book_version_id"],
        ).order_by(Quote.start_cp)))
        assert [quote.nesting_depth for quote in quotes] == [0, 1, 0]
        scene = Scene(book_version_id=data["book_version_id"], start_cp=0, end_cp=len(sample))
        session.add(scene)
        session.flush()
        for index, quote in enumerate(quotes):
            group = SpeakerGroup(scene_id=scene.id, display_label=f"S{index + 1}",
                                 canonical_name=f"人物{index + 1}", first_quote_id=quote.id)
            session.add(group)
            session.flush()
            session.add(Annotation(quote_id=quote.id, scene_id=scene.id, kind="thought",
                                   speaker_id=group.id, status="USER_CONFIRMED", source="USER",
                                   assignment="EXISTING", basis="DIRECT", visible_from_cp=0))
    original = annotations_of(client, data["book_id"], reading_mode="reread")["items"]
    raw = _export(client, data["book_id"], _preview(client, data["book_id"])["snapshot_id"])
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        manifest = json.loads(archive.read("OEBPS/annotations.json"))
        assert [entry.get("nesting_depth", 0) for entry in manifest["annotations"]] == [0, 1, 0]
    if depth_metadata != "current":
        def change_depth(manifest):  # noqa: ANN001 - 测试回调
            if depth_metadata == "legacy":
                manifest["annotations"] = [
                    entry for entry in manifest["annotations"] if not entry.get("nesting_depth", 0)
                ]
                for entry in manifest["annotations"]:
                    entry.pop("nesting_depth", None)
            else:
                manifest["annotations"][1]["nesting_depth"] = True
        raw = _rewrite_manifest(raw, change_depth)
    restored = client.post("/api/books/import", files={
        "file": ("nested-restored.epub", raw, "application/epub+zip"),
    })
    assert restored.status_code == 202, restored.text
    actual = annotations_of(client, restored.json()["data"]["book_id"], reading_mode="reread")["items"]
    fields = ("label", "color_index", "kind")
    if depth_metadata != "current":
        original = [row for row in original if row["label"] != "人物2"]
    assert sorted(tuple(row[key] for key in fields) for row in actual) == sorted(
        tuple(row[key] for key in fields) for row in original
    )


def test_revealed_names_and_merge_visibility_survive_epub_roundtrip(
    fake_provider_client, migrated_settings,
):
    client = fake_provider_client
    data = import_sample(client)
    profile = create_fake_profile(client)
    run_deterministic_job(migrated_settings, client, book_id=data["book_id"],
                          profile_id=profile, key="visibility-roundtrip")
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        chapters = list(session.scalars(select(Chapter).where(
            Chapter.book_version_id == data["book_version_id"],
        ).order_by(Chapter.ordinal)))
        target = BookCharacter(book_version_id=data["book_version_id"], canonical_name="阿库娅",
                               description="最终身份")
        session.add(target)
        session.flush()
        groups = list(session.scalars(select(SpeakerGroup).join(Scene).where(
            Scene.book_version_id == data["book_version_id"],
        )))
        assert groups
        extra = SpeakerGroup(scene_id=groups[0].scene_id, display_label="S-extra")
        session.add(extra)
        session.flush()
        annotations = list(session.scalars(select(Annotation).where(
            Annotation.speaker_id == groups[0].id,
        )))
        assert len(annotations) >= 2
        annotations[1].speaker_id = extra.id
        extra.first_quote_id = annotations[1].quote_id
        groups.append(extra)
        for index, group in enumerate(groups):
            group.character_id = target.id
            group.canonical_name = target.canonical_name
            group.description = target.description
            group.presentation_history_json = json.dumps([
                {"cp": 0, "identity": f"early:{index}", "name": f"早期人物{index}",
                 "description": f"早期说明{index}"},
                {"cp": chapters[-1].end_cp, "identity": f"character:{target.id}",
                 "name": "阿库娅", "description": "最终身份"},
            ], ensure_ascii=False)
        for annotation in session.scalars(select(Annotation).where(
            Annotation.speaker_id.in_([group.id for group in groups]),
        )):
            annotation.visible_from_cp = 0
    raw = _export(client, data["book_id"], _preview(client, data["book_id"])["snapshot_id"])
    imported = client.post("/api/books/import", files={
        "file": ("history.epub", raw, "application/epub+zip"),
    })
    assert imported.status_code == 202, imported.text
    book_id = imported.json()["data"]["book_id"]
    chapters = client.get(f"/api/books/{book_id}/chapters").json()["data"]
    early = client.get(f"/api/books/{book_id}/annotations", params={
        "reading_mode": "initial", "visible_horizon_cp": chapters[0]["end_cp"],
    }).json()["data"]
    assert all(item["label"].startswith("早期人物") for item in early["items"])
    assert len({item["color_index"] for item in early["items"]}) >= 2
    assert all(item["speaker_description"].startswith("早期说明") for item in early["items"])
    final = annotations_of(client, book_id, reading_mode="reread")
    assert {item["label"] for item in final["items"]} == {"阿库娅"}
    assert len({item["color_index"] for item in final["items"]}) == 1


def test_manual_chapter_status_survives_epub_roundtrip(fake_provider_client: TestClient) -> None:
    data = import_sample(fake_provider_client)
    book_id = data["book_id"]
    chapters = fake_provider_client.get(f"/api/books/{book_id}/chapters").json()["data"]
    for index, chapter in enumerate(chapters):
        response = fake_provider_client.put(
            f"/api/books/{book_id}/chapters/{chapter['id']}/processing-status",
            json={"book_version_id": data["book_version_id"], "dialogue_processed": index == 0},
        )
        assert response.status_code == 200, response.text
    raw = _export(fake_provider_client, book_id, _preview(fake_provider_client, book_id)["snapshot_id"])
    imported = fake_provider_client.post("/api/books/import", files={
        "file": ("manual.epub", raw, "application/epub+zip"),
    })
    assert imported.status_code == 202, imported.text
    restored = fake_provider_client.get(
        f"/api/books/{imported.json()['data']['book_id']}/chapters",
    ).json()["data"]
    assert [chapter["dialogue_processed"] for chapter in restored] == [True, False]
    assert [chapter["processing_status_override"] for chapter in restored] == [True, False]


def test_manual_status_changes_export_cache_key_but_not_saved_preview(fake_provider_client: TestClient) -> None:
    data = import_sample(fake_provider_client)
    book_id = data["book_id"]
    chapter = fake_provider_client.get(f"/api/books/{book_id}/chapters").json()["data"][0]
    before = _preview(fake_provider_client, book_id)
    raw_before = _export(fake_provider_client, book_id, before["snapshot_id"])
    fake_provider_client.put(f"/api/books/{book_id}/chapters/{chapter['id']}/processing-status", json={
        "book_version_id": data["book_version_id"], "dialogue_processed": True,
    })
    after = _preview(fake_provider_client, book_id)
    raw_after = _export(fake_provider_client, book_id, after["snapshot_id"])
    with zipfile.ZipFile(io.BytesIO(raw_before)) as archive:
        assert json.loads(archive.read("OEBPS/annotations.json"))["manual_processing_status"] == {}
    with zipfile.ZipFile(io.BytesIO(raw_after)) as archive:
        assert json.loads(archive.read("OEBPS/annotations.json"))["manual_processing_status"] == {"0": True}
    assert _export(fake_provider_client, book_id, before["snapshot_id"]) == raw_before


def test_exported_epub_roundtrip_restores_annotations(
    fake_provider_client: TestClient, migrated_settings
) -> None:
    data = import_sample(fake_provider_client)
    book_id = data["book_id"]
    profile_id = create_fake_profile(fake_provider_client, name="回环提供方")
    run_deterministic_job(
        migrated_settings, fake_provider_client, book_id=book_id, profile_id=profile_id, key="k-rt"
    )
    base = f"/api/books/{book_id}/character-directory"
    person = fake_provider_client.get(base).json()["data"][0]
    if person["kind"] == "speaker":
        person = fake_provider_client.put(f"{base}/{person['character_id']}", json={
            "name": person["name"], "description": person["description"],
            "expected_version": person["version"],
        }).json()["data"]
    colored = fake_provider_client.put(f"{base}/{person['character_id']}/color", json={
        "color_index": 24, "expected_version": person["version"],
    })
    assert colored.status_code == 200, colored.text
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
    assert any(item["color_index"] == 24 for item in styled)

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


def test_manual_expression_owners_survive_epub_roundtrip(fake_provider_client, migrated_settings):
    client = fake_provider_client
    data = import_sample(client)
    profile = create_fake_profile(client)
    run_deterministic_job(migrated_settings, client, book_id=data["book_id"],
                          profile_id=profile, key="manual-expression-roundtrip")
    items = annotations_of(client, data["book_id"], reading_mode="reread")["items"]
    assert len(items) >= 3
    for item, kind in zip(items[:3], ("speech", "thought", "quotation"), strict=True):
        assert item["speaker_group_id"]
        response = client.post(f"/api/quotes/{item['quote_id']}/corrections", json={
            "action": "set_kind", "kind": kind,
        })
        assert response.status_code == 201, response.text
    original_by_mode = {
        mode: annotations_of(client, data["book_id"], reading_mode=mode)
        for mode in ("initial", "reread")
    }
    raw = _export(client, data["book_id"], _preview(client, data["book_id"])["snapshot_id"])
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        manifest = json.loads(archive.read("OEBPS/annotations.json"))
    assert [(r["kind"], r["source"]) for r in manifest["annotations"][:3]] == [
        (kind, "USER") for kind in ("speech", "thought", "quotation")
    ]
    restored = client.post("/api/books/import", files={
        "file": ("manual-expressions.epub", raw, "application/epub+zip"),
    })
    assert restored.status_code == 202, restored.text
    book_id = restored.json()["data"]["book_id"]
    for mode in ("initial", "reread"):
        expected = [(r["kind"], r["label"], r["color_index"], r["status"], r["source"])
                    for r in original_by_mode[mode]["items"][:3]]
        actual = annotations_of(client, book_id, reading_mode=mode)["items"][:3]
        assert [(r["kind"], r["label"], r["color_index"], r["status"], r["source"])
                for r in actual] == expected
        assert all(r["user_locked"] and r["speaker_group_id"] and not r["withheld"] for r in actual)


def test_css_normalized_epub_roundtrip_preserves_text_annotations_and_processed_state(
    fake_provider_client: TestClient, migrated_settings,
) -> None:
    from xml.sax.saxutils import escape

    from fixtures.corrections import SAMPLE
    from fixtures.epub_factory import Document, EpubSpec, build_epub

    body = (
        '<div style="float:right"><p>原</p><p>创</p><p>者</p></div>'
        + "".join(f"<p>{escape(line)}</p>" for line in SAMPLE.splitlines())
    )
    source = build_epub(EpubSpec(documents=[Document("one", "text/one.xhtml", body)]))
    response = fake_provider_client.post(
        "/api/books/import",
        files={"file": ("decorative.epub", source, "application/epub+zip")},
    )
    assert response.status_code == 202, response.text
    data = response.json()["data"]
    book_id = data["book_id"]
    content = fake_provider_client.get(f"/api/books/{book_id}/content").json()["data"]
    assert content["nodes"][0]["text"] == "原创者"
    profile_id = create_fake_profile(fake_provider_client, name="竖排回环提供方")
    run_deterministic_job(
        migrated_settings, fake_provider_client, book_id=book_id,
        profile_id=profile_id, key="k-css-roundtrip",
    )
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        chapter = session.scalars(select(Chapter).where(
            Chapter.book_version_id == data["book_version_id"],
        )).one()
        chapter.dialogue_processed = True
    original = annotations_of(fake_provider_client, book_id, reading_mode="reread")
    epub_bytes = _export(fake_provider_client, book_id, _preview(
        fake_provider_client, book_id,
    )["snapshot_id"])
    response = fake_provider_client.post(
        "/api/books/import",
        files={"file": ("roundtrip.epub", epub_bytes, "application/epub+zip")},
    )
    assert response.status_code == 202, response.text
    restored_id = response.json()["data"]["book_id"]
    restored_content = fake_provider_client.get(
        f"/api/books/{restored_id}/content",
    ).json()["data"]
    assert [n["text"] for n in restored_content["nodes"]] == [n["text"] for n in content["nodes"]]
    restored = annotations_of(fake_provider_client, restored_id, reading_mode="reread")
    assert [(n["label"], n["color_index"]) for n in restored["items"]] == [
        (n["label"], n["color_index"]) for n in original["items"]
    ]
    chapters = fake_provider_client.get(f"/api/books/{restored_id}/chapters").json()["data"]
    assert len(chapters) == 1
    assert chapters[0]["dialogue_processed"] is True


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
