"""Import parsing must leave the shared writer available; all data stays atomic."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from sqlalchemy import func, select

from fixtures.epub_factory import build_epub, minimal_spec
from ndr.api import books
from ndr.ingest import service
from ndr.storage.models import Book, BookVersion, Chapter, Job
from ndr.storage.transactions import transaction


def upload(client, raw, suffix):
    return client.post("/api/books/import", files={"file": (f"isolated{suffix}", raw)})


@pytest.mark.parametrize("parser", ["parse_txt", "parse_epub", "parse_annotations_manifest"])
def test_other_writer_can_commit_while_import_parsing_is_paused(
    migrated_client, monkeypatch, parser,
):
    started, release = Event(), Event()
    original = getattr(books, parser)
    suffix = ".txt" if parser == "parse_txt" else ".epub"
    raw = "第一章\n「你好。」\n".encode() if suffix == ".txt" else build_epub(minimal_spec())

    def paused(*args, **kwargs):
        started.set()
        assert release.wait(10), "test did not release parser"
        return original(*args, **kwargs)

    monkeypatch.setattr(books, parser, paused)
    factory = migrated_client.app.state.session_factory

    def other_write():
        with transaction(factory) as session:
            job = service.record_failed_import(session, message="isolated writer", filename=None)
            return job.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        imported = pool.submit(upload, migrated_client, raw, suffix)
        try:
            assert started.wait(5)
            job_id = pool.submit(other_write).result(timeout=5)
            with factory() as session:
                assert session.get(Job, job_id) is not None
        finally:
            release.set()
        response = imported.result(timeout=10)
    assert response.status_code == 202, response.text


@pytest.mark.parametrize("suffix", [".txt", ".epub"])
def test_concurrent_parsed_imports_recheck_identity_inside_writer_transaction(
    migrated_client, monkeypatch, suffix,
):
    both_parsed = Barrier(2)
    parser = "parse_txt" if suffix == ".txt" else "parse_epub"
    original = getattr(books, parser)

    def synchronized(*args, **kwargs):
        parsed = original(*args, **kwargs)
        both_parsed.wait(timeout=10)
        return parsed

    monkeypatch.setattr(books, parser, synchronized)
    raw = "第一章\n「你好。」\n".encode() if suffix == ".txt" else build_epub(minimal_spec())
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(upload, migrated_client, raw, suffix) for _ in range(2)]
        responses = [future.result(timeout=15) for future in futures]
    assert all(response.status_code == 202 for response in responses)
    results = [response.json()["data"] for response in responses]
    assert results[0]["book_id"] == results[1]["book_id"]
    assert results[0]["book_version_id"] == results[1]["book_version_id"]
    assert sorted(row["reused_version"] for row in results) == [False, True]
    with migrated_client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Book)) == 1
        assert session.scalar(select(func.count()).select_from(BookVersion)) == 1
        assert session.scalar(select(func.count()).select_from(Job)) == 2


@pytest.mark.parametrize("manifest", [None, b"invalid JSON", b'{"manifest_version":"ndr-annotations-1","annotations":[]}'])
def test_epub_preparsed_manifest_preserves_repeated_import_and_never_reparses_in_transaction(
    migrated_client, monkeypatch, manifest,
):
    spec = minimal_spec()
    if manifest is not None:
        spec.extras["OEBPS/annotations.json"] = manifest
    raw = build_epub(spec)

    def unexpected_parse(*args, **kwargs):
        pytest.fail("manifest parsing inside persistence holds the writer")

    monkeypatch.setattr(service, "parse_annotations_manifest", unexpected_parse)
    responses = [upload(migrated_client, raw, ".epub") for _ in range(2)]
    assert all(response.status_code == 202 for response in responses)
    assert responses[1].json()["data"]["reused_version"] is True


def test_persistence_failure_still_rolls_back_rows_and_written_files(
    migrated_client, migrated_settings, monkeypatch,
):
    def fail_scan(*args, **kwargs):
        raise RuntimeError("isolated persistence failure")

    monkeypatch.setattr(service, "scan_and_store", fail_scan)
    with pytest.raises(RuntimeError, match="isolated persistence failure"):
        upload(migrated_client, "第一章\n「你好。」\n".encode(), ".txt")
    with migrated_client.app.state.session_factory() as session:
        for model in (Book, BookVersion, Job):
            assert session.scalar(select(func.count()).select_from(model)) == 0
    assert not [path for path in (migrated_settings.data_dir / "books").rglob("*") if path.is_file()]


def test_epub_parse_failure_still_records_failed_job(migrated_client):
    response = upload(migrated_client, b"not a zip", ".epub")
    assert response.status_code == 422
    job_id = response.json()["error"]["details"]["job_id"]
    job = migrated_client.get(f"/api/jobs/{job_id}").json()["data"]
    assert job["state"] == "FAILED"
    assert job["book_id"] is None


def test_direct_epub_service_keeps_manifest_restore_default(migrated_client, migrated_settings):
    spec = minimal_spec()
    spec.extras["OEBPS/annotations.json"] = (
        b'{"manifest_version":"ndr-annotations-1","annotations":[],"processed_chapter_indices":[0]}'
    )
    with transaction(migrated_client.app.state.session_factory) as session:
        outcome = service.import_epub(
            session, migrated_settings, filename="direct.epub", raw=build_epub(spec),
        )
        chapter = session.scalar(select(Chapter).where(
            Chapter.book_version_id == outcome.version.id, Chapter.ordinal == 0,
        ))
        assert chapter.dialogue_processed is True
