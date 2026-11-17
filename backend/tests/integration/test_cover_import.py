"""Cover persistence, repeated import and existing resource access."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from fixtures.epub_covers import COVER_IMAGE, build_cover_epub
from ndr.storage.models import Book, BookVersion, Chapter, Resource
from ndr.storage.transactions import transaction


def import_cover(client, raw=None):
    response = client.post("/api/books/import", files={
        "file": ("cover.epub", raw if raw is not None else build_cover_epub(), "application/epub+zip"),
    })
    assert response.status_code == 202, response.text
    return response.json()["data"]


def test_cover_is_available_in_detail_list_and_existing_resource_endpoint(migrated_client):
    imported = import_cover(migrated_client)
    book_id = imported["book_id"]
    detail = migrated_client.get(f"/api/books/{book_id}").json()["data"]
    listed = migrated_client.get("/api/books").json()["data"]["items"][0]
    assert detail["active_version"]["cover_resource_id"] == "r0002"
    assert listed["active_version"]["cover_resource_id"] == "r0002"
    image = migrated_client.get(f"/api/books/{book_id}/resources/r0002")
    assert image.status_code == 200 and image.content == COVER_IMAGE
    assert image.headers["content-type"].startswith("image/png")
    assert imported["resource_count"] == 3


def test_missing_cover_and_txt_return_null(migrated_client):
    missing = import_cover(migrated_client, build_cover_epub(cover_image_id=None))
    txt = migrated_client.post("/api/books/import", files={
        "file": ("plain.txt", "第一章\n「你好。」".encode(), "text/plain"),
    }).json()["data"]
    for imported in (missing, txt):
        detail = migrated_client.get(f"/api/books/{imported['book_id']}").json()["data"]
        assert detail["active_version"]["cover_resource_id"] is None


def test_reimport_fills_empty_cover_using_stored_resource_identity_and_preserves_progress(
    migrated_client,
):
    raw = build_cover_epub()
    imported = import_cover(migrated_client, raw)
    factory = migrated_client.app.state.session_factory
    with transaction(factory) as session:
        version = session.get(BookVersion, imported["book_version_id"])
        version.cover_resource_id = None
        cover = session.scalar(select(Resource).where(
            Resource.book_version_id == version.id, Resource.resource_id == "r0002",
        ))
        cover.resource_id = "stored-cover"
        book = session.get(Book, imported["book_id"])
        book.read_position_cp = 4
        book.read_position_version_id = version.id
        chapter = session.scalar(select(Chapter).where(Chapter.book_version_id == version.id))
        chapter.dialogue_processed = True
        chapter.processing_status_override = True
        chapter_id = chapter.id
        canonical = version.canonical_sha256
    repeated = import_cover(migrated_client, raw)
    assert repeated["reused_book"] is True and repeated["reused_version"] is True
    assert repeated["book_version_id"] == imported["book_version_id"]
    with factory() as session:
        version = session.get(BookVersion, imported["book_version_id"])
        assert version.cover_resource_id == "stored-cover"
        assert version.canonical_sha256 == canonical
        assert session.scalar(select(func.count()).select_from(BookVersion)) == 1
        assert session.scalar(select(func.count()).select_from(Resource)) == 3
        book = session.get(Book, imported["book_id"])
        assert book.read_position_cp == 4 and book.read_position_version_id == version.id
        chapter = session.get(Chapter, chapter_id)
        assert chapter.dialogue_processed is True and chapter.processing_status_override is True
    assert migrated_client.get(
        f"/api/books/{imported['book_id']}/resources/stored-cover",
    ).content == COVER_IMAGE


@pytest.mark.parametrize("change", ["existing_cover", "wrong_hash", "wrong_path", "other_version"])
def test_reimport_only_fills_empty_cover_with_matching_registered_resource(migrated_client, change):
    raw = build_cover_epub()
    imported = import_cover(migrated_client, raw)
    other = import_cover(migrated_client, build_cover_epub(cover_image_id="res1"))
    factory = migrated_client.app.state.session_factory
    with transaction(factory) as session:
        version = session.get(BookVersion, imported["book_version_id"])
        version.cover_resource_id = "r0001" if change == "existing_cover" else None
        resource = session.scalar(select(Resource).where(
            Resource.book_version_id == version.id, Resource.resource_id == "r0002",
        ))
        if change == "wrong_hash":
            resource.sha256 = "0" * 64
        elif change == "wrong_path":
            resource.relative_path = "OEBPS/images/different.png"
        elif change == "other_version":
            session.delete(resource)
    import_cover(migrated_client, raw)
    with factory() as session:
        version = session.get(BookVersion, imported["book_version_id"])
        assert version.cover_resource_id == ("r0001" if change == "existing_cover" else None)
        assert session.get(BookVersion, other["book_version_id"]).cover_resource_id == "r0001"


def test_deleting_cover_book_cleans_registered_resources_and_keeps_other_book(migrated_client):
    imported = import_cover(migrated_client)
    other = import_cover(migrated_client, build_cover_epub(cover_image_id="res1"))
    response = migrated_client.delete(f"/api/books/{imported['book_id']}")
    assert response.status_code == 204
    assert migrated_client.get(f"/api/books/{imported['book_id']}/resources/r0002").status_code == 404
    assert migrated_client.get(f"/api/books/{other['book_id']}/resources/r0002").content == COVER_IMAGE
    with migrated_client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Resource).where(
            Resource.book_version_id == imported["book_version_id"],
        )) == 0
