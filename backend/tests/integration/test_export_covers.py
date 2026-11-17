"""封面随冻结快照导出，回导保持正文与章节边界。"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from xml.etree import ElementTree as ET

import pytest
from sqlalchemy import select

from fixtures.epub_covers import BODY_IMAGE, COVER_IMAGE, build_cover_epub
from integration.test_exports import _export, _preview
from ndr.ingest.epub import parse_epub
from ndr.ingest.query import load_canonical_text
from ndr.storage.models import BookVersion, Chapter, ExportSnapshot, Resource
from ndr.storage.transactions import transaction

OPF_NS = {"opf": "http://www.idpf.org/2007/opf"}


def _import(client, raw: bytes) -> dict:
    response = client.post("/api/books/import", files={
        "file": ("cover.epub", raw, "application/epub+zip"),
    })
    assert response.status_code == 202, response.text
    return response.json()["data"]


def _download(client, book_id: str, snapshot_id: str, *, key: str) -> tuple[dict, bytes]:
    artifact = _export(client, book_id, snapshot_id, "epub", key=key)
    assert artifact["state"] == "COMPLETED", artifact
    response = client.get(f"/api/exports/{artifact['id']}/download")
    assert response.status_code == 200, response.text
    return artifact, response.content


def _cover_bytes(raw: bytes) -> bytes | None:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        opf = ET.fromstring(archive.read("OEBPS/content.opf"))
        covers = [item for item in opf.findall("opf:manifest/opf:item", OPF_NS)
                  if "cover-image" in item.get("properties", "").split()]
        assert len(covers) <= 1
        if not covers:
            return None
        assert covers[0].get("media-type") == "image/png"
        return archive.read("OEBPS/" + covers[0].get("href"))


@pytest.mark.parametrize("cover_image_id,expected_cover,image_count", [
    ("res2", COVER_IMAGE, 2),
    ("res1", BODY_IMAGE, 1),
])
def test_epub_cover_roundtrip_keeps_chapters_and_deduplicates_body_image(
    migrated_client, cover_image_id, expected_cover, image_count,
):
    client = migrated_client
    source = build_cover_epub(cover_image_id=cover_image_id)
    parsed_source = parse_epub(source)
    imported = _import(client, source)
    with transaction(client.app.state.session_factory) as session:
        chapter = session.scalar(select(Chapter).where(
            Chapter.book_version_id == imported["book_version_id"],
        ))
        chapter.dialogue_processed = True
    snapshot_id = _preview(client, imported["book_id"])["snapshot_id"]
    _, exported = _download(client, imported["book_id"], snapshot_id, key="cover-roundtrip")
    assert _cover_bytes(exported) == expected_cover
    with zipfile.ZipFile(io.BytesIO(exported)) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names))
        assert len([name for name in names if name.startswith("OEBPS/images/")]) == image_count
        opf = ET.fromstring(archive.read("OEBPS/content.opf"))
        assert len(opf.findall("opf:spine/opf:itemref", OPF_NS)) == len(parsed_source.chapters)
        assert len([name for name in names if name.startswith("OEBPS/text/")]) == len(
            parsed_source.chapters,
        )
    parsed_export = parse_epub(exported)
    assert parsed_export.canonical_text == parsed_source.canonical_text
    assert [(chapter.title, chapter.start_cp, chapter.end_cp) for chapter in parsed_export.chapters] == [
        (chapter.title, chapter.start_cp, chapter.end_cp) for chapter in parsed_source.chapters
    ]
    restored = _import(client, exported)
    with transaction(client.app.state.session_factory) as session:
        version = session.get(BookVersion, restored["book_version_id"])
        assert load_canonical_text(client.app.state.settings, version) == parsed_source.canonical_text
        cover = session.scalar(select(Resource).where(
            Resource.book_version_id == version.id,
            Resource.resource_id == version.cover_resource_id,
        ))
        assert cover is not None
        assert cover.sha256 == hashlib.sha256(expected_cover).hexdigest()
        chapters = list(session.scalars(select(Chapter).where(Chapter.book_version_id == version.id)))
        assert len(chapters) == len(parsed_source.chapters)
        assert all(chapter.dialogue_processed for chapter in chapters)


def test_cover_snapshot_freezes_reference_and_changes_export_fingerprint(migrated_client):
    client = migrated_client
    imported = _import(client, build_cover_epub())
    first = _preview(client, imported["book_id"])
    with transaction(client.app.state.session_factory) as session:
        snapshot = session.get(ExportSnapshot, first["snapshot_id"])
        frozen_json = snapshot.annotation_projection_json
        frozen_cover = json.loads(frozen_json)["cover"]
        version = session.get(BookVersion, imported["book_version_id"])
        body = session.scalar(select(Resource).where(
            Resource.book_version_id == version.id,
            Resource.sha256 == hashlib.sha256(BODY_IMAGE).hexdigest(),
        ))
        assert frozen_cover == {"resource_id": version.cover_resource_id, "media_type": "image/png"}
        version.cover_resource_id = body.resource_id
    second = _preview(client, imported["book_id"])
    with transaction(client.app.state.session_factory) as session:
        old = session.get(ExportSnapshot, first["snapshot_id"])
        fresh = session.get(ExportSnapshot, second["snapshot_id"])
        assert old.annotation_projection_json == frozen_json
        assert old.snapshot_hash != fresh.snapshot_hash
        assert old.source_revision != fresh.source_revision
        assert json.loads(fresh.annotation_projection_json)["cover"] != frozen_cover
    old_artifact, old_raw = _download(client, imported["book_id"], first["snapshot_id"], key="old-cover")
    new_artifact, new_raw = _download(client, imported["book_id"], second["snapshot_id"], key="new-cover")
    assert _cover_bytes(old_raw) == COVER_IMAGE
    assert _cover_bytes(new_raw) == BODY_IMAGE
    assert old_artifact["id"] != new_artifact["id"]
    repeated, repeated_raw = _download(
        client, imported["book_id"], first["snapshot_id"], key="old-cover-again",
    )
    assert repeated["id"] == old_artifact["id"]
    assert repeated_raw == old_raw


@pytest.mark.parametrize("legacy_missing", [True, False])
def test_missing_or_null_snapshot_cover_never_uses_live_version(migrated_client, legacy_missing):
    client = migrated_client
    imported = _import(client, build_cover_epub())
    preview = _preview(client, imported["book_id"])
    with transaction(client.app.state.session_factory) as session:
        snapshot = session.get(ExportSnapshot, preview["snapshot_id"])
        payload = json.loads(snapshot.annotation_projection_json)
        if legacy_missing:
            del payload["cover"]
            payload["snapshot_version"] = "export-snapshot-4"
        else:
            payload["cover"] = None
        snapshot.annotation_projection_json = json.dumps(payload)
        assert session.get(BookVersion, imported["book_version_id"]).cover_resource_id
    _, raw = _download(client, imported["book_id"], preview["snapshot_id"], key="no-frozen-cover")
    assert _cover_bytes(raw) is None
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        images = [name for name in archive.namelist() if name.startswith("OEBPS/images/")]
        assert len(images) == 1
        assert archive.read(images[0]) == BODY_IMAGE


def test_undeclared_epub_image_stays_out_of_cover_metadata(migrated_client):
    client = migrated_client
    imported = _import(client, build_cover_epub(cover_image_id=None))
    preview = _preview(client, imported["book_id"])
    with transaction(client.app.state.session_factory) as session:
        snapshot = session.get(ExportSnapshot, preview["snapshot_id"])
        assert json.loads(snapshot.annotation_projection_json)["cover"] is None
    _, raw = _download(client, imported["book_id"], preview["snapshot_id"], key="undeclared-cover")
    assert _cover_bytes(raw) is None


def test_unavailable_frozen_cover_does_not_create_dangling_manifest_entry(migrated_client):
    client = migrated_client
    imported = _import(client, build_cover_epub())
    preview = _preview(client, imported["book_id"])
    with transaction(client.app.state.session_factory) as session:
        version = session.get(BookVersion, imported["book_version_id"])
        cover = session.scalar(select(Resource).where(
            Resource.book_version_id == version.id,
            Resource.resource_id == version.cover_resource_id,
        ))
        cover.relative_path = "OEBPS/images/missing.png"
    _, raw = _download(client, imported["book_id"], preview["snapshot_id"], key="missing-cover")
    assert _cover_bytes(raw) is None
    assert parse_epub(raw).canonical_text == parse_epub(build_cover_epub()).canonical_text
