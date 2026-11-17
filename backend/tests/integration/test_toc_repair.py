"""Offline metadata maintenance on disposable historical libraries only."""

import gzip
import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import select
from sqlalchemy.orm import Session

from fixtures.toc_epub import nested_epub
from ndr.ingest.document import ParsedChapter
from ndr.ingest.epub import parse_epub
from ndr.ingest.service import import_epub
from ndr.portable import LibraryInUseError, LibraryLock
from ndr.storage import toc_repair
from ndr.storage.engine import create_db_engine, head_revision
from ndr.storage.migrate import build_alembic_config
from ndr.storage.models import BookCharacter, Bookmark, Chapter


@pytest.fixture
def old_library(migrated_settings):
    settings = migrated_settings
    raw = nested_epub()
    parsed = parse_epub(raw)
    engine = create_db_engine(settings)
    try:
        with Session(engine) as session:
            outcome = import_epub(session, settings, filename="nested.epub", raw=raw)
            chapters = list(session.scalars(select(Chapter).where(
                Chapter.book_version_id == outcome.version.id,
            ).order_by(Chapter.ordinal)))
            by_id = {entry["id"]: entry for entry in parsed.toc}
            for entry in parsed.toc:
                ordinal = entry.get("chapter_ordinal")
                if ordinal is not None:
                    parent = by_id.get(entry.get("parent_id"))
                    chapters[ordinal].title = (
                        f'{parent["title"]} · {entry["title"]}' if parent else entry["title"]
                    )
            session.add(Bookmark(book_id=outcome.book.id, book_version_id=outcome.version.id,
                                 chapter_id=chapters[1].id, position_cp=chapters[1].start_cp,
                                 excerpt="第一节", note="保留书签"))
            session.add(BookCharacter(book_version_id=outcome.version.id,
                                      canonical_name="原创人物", user_confirmed=True))
            outcome.version.toc_json = None
            session.commit()
            book_id = outcome.book.id
    finally:
        engine.dispose()
    command.downgrade(build_alembic_config(settings), "0024")
    return settings, book_id, parsed


def _rows(settings, sql):
    with closing(sqlite3.connect(settings.database_path)) as db:
        return db.execute(sql).fetchall()


def test_preview_is_read_only_apply_upgrades_and_preserves_business_data(old_library):
    settings, book_id, _ = old_library
    before = settings.database_path.read_bytes()
    chapters = _rows(settings, "SELECT id,ordinal,start_cp,end_cp,source_href FROM chapters ORDER BY ordinal")
    protected = {table: _rows(settings, f"SELECT * FROM {table} ORDER BY id")
                 for table in ("bookmarks", "jobs", "book_characters", "quotes", "content_nodes")}
    files = {path: hashlib.sha256(path.read_bytes()).digest()
             for path in settings.data_dir.rglob("*")
             if path.is_file() and path != settings.database_path}
    plan = toc_repair.inspect_toc(settings, book_id)
    assert plan["changed"] and plan["root_count"] == 3 and plan["node_count"] == 6
    assert settings.database_path.read_bytes() == before
    assert not (settings.data_dir / "backups").exists()
    outcome = toc_repair.repair_toc(settings, book_id)
    assert outcome["business_data_unchanged"]
    assert _rows(settings, "SELECT version_num FROM alembic_version") == [(head_revision(),)]
    assert _rows(settings, "SELECT id,ordinal,start_cp,end_cp,source_href FROM chapters ORDER BY ordinal") == chapters
    for table, rows in protected.items():
        assert _rows(settings, f"SELECT * FROM {table} ORDER BY id") == rows
    assert all(hashlib.sha256(path.read_bytes()).digest() == digest for path, digest in files.items())
    backup = Path(outcome["backup_path"])
    with gzip.open(backup, "rb") as archive:
        assert archive.read() == before
    assert Path(outcome["metadata_backup_path"]).is_file()
    toc = json.loads(_rows(settings, "SELECT toc_json FROM book_versions")[0][0])
    assert {node["chapter_id"] for node in toc if node["chapter_id"]} == {row[0] for row in chapters}
    committed = settings.database_path.read_bytes()
    backups = set((settings.data_dir / "backups").iterdir())
    again = toc_repair.repair_toc(settings, book_id)
    assert not again["changed"]
    assert set((settings.data_dir / "backups").iterdir()) == backups
    assert settings.database_path.read_bytes() == committed


@pytest.mark.parametrize("state", ["QUEUED", "RUNNING", "PAUSING", "PAUSED", "PARTIAL", "BUDGET_EXHAUSTED"])
def test_repair_refuses_running_and_resumable_jobs(old_library, state):
    settings, book_id, _ = old_library
    with closing(sqlite3.connect(settings.database_path)) as db:
        db.execute("UPDATE jobs SET state=? WHERE book_id=?", (state, book_id))
        db.commit()
    before = settings.database_path.read_bytes()
    with pytest.raises(RuntimeError, match="任务"):
        toc_repair.repair_toc(settings, book_id)
    assert settings.database_path.read_bytes() == before
    assert not (settings.data_dir / "backups").exists()


def test_repair_refuses_application_and_sqlite_locks(old_library):
    settings, book_id, _ = old_library
    with LibraryLock(settings.data_dir), pytest.raises(LibraryInUseError):
        toc_repair.repair_toc(settings, book_id)
    with closing(sqlite3.connect(settings.database_path)) as reader:
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM books").fetchall()
        with pytest.raises(RuntimeError, match="locked"):
            toc_repair.repair_toc(settings, book_id)
        reader.rollback()
    assert not (settings.data_dir / "backups").exists()


def test_repair_does_not_overwrite_manual_titles(old_library):
    settings, book_id, _ = old_library
    with closing(sqlite3.connect(settings.database_path)) as db:
        db.execute("UPDATE chapters SET title='读者自定名称' WHERE ordinal=1")
        db.commit()
    before = settings.database_path.read_bytes()
    with pytest.raises(RuntimeError, match="人工改名"):
        toc_repair.repair_toc(settings, book_id)
    assert settings.database_path.read_bytes() == before
    assert not (settings.data_dir / "backups").exists()


def test_zero_length_extra_page_binds_to_existing_ranges_without_new_chapters(old_library, monkeypatch):
    settings, book_id, parsed = old_library
    extra = ParsedChapter(ordinal=0, title="彩页", start_cp=0, end_cp=0,
                          source_href="OEBPS/plate.xhtml")
    updated = replace(parsed, chapters=(extra, *(replace(c, ordinal=c.ordinal + 1)
                                               for c in parsed.chapters)),
                      toc=tuple({**entry, "chapter_ordinal": entry["chapter_ordinal"] + 1}
                                if entry.get("chapter_ordinal") is not None else entry
                                for entry in parsed.toc) + ({
                                    "id": "plate", "parent_id": None, "title": "彩页",
                                    "chapter_ordinal": 0, "target_cp": 0,
                                },))
    monkeypatch.setattr(toc_repair, "parse_epub", lambda _: updated)
    chapters = _rows(settings, "SELECT id,ordinal,start_cp,end_cp FROM chapters ORDER BY ordinal")
    plan = toc_repair.inspect_toc(settings, book_id)
    assert plan["preserved_empty_page_groups"] == 1
    nodes = json.loads(plan["after_toc"])
    assert nodes[-1]["chapter_id"] == chapters[0][0]
    toc_repair.repair_toc(settings, book_id)
    assert _rows(settings, "SELECT id,ordinal,start_cp,end_cp FROM chapters ORDER BY ordinal") == chapters


def test_nonempty_extra_chapter_is_rejected(old_library, monkeypatch):
    settings, book_id, parsed = old_library
    extra = ParsedChapter(ordinal=99, title="新增正文", start_cp=0, end_cp=1,
                          source_href="OEBPS/unexpected.xhtml")
    monkeypatch.setattr(toc_repair, "parse_epub", lambda _: replace(
        parsed, chapters=(*parsed.chapters, extra),
    ))
    with pytest.raises(RuntimeError, match="正文章节数量改变"):
        toc_repair.inspect_toc(settings, book_id)


def test_metadata_restore_preserves_business_data_and_advances_version(old_library):
    settings, book_id, _ = old_library
    chapter_sql = "SELECT id,title,ordinal,start_cp,end_cp,source_href FROM chapters ORDER BY ordinal"
    original_chapters = _rows(settings, chapter_sql)
    original_version = _rows(settings, "SELECT version FROM books")[0][0]
    protected = {table: _rows(settings, f"SELECT * FROM {table} ORDER BY id")
                 for table in ("bookmarks", "jobs", "book_characters", "quotes", "content_nodes")}
    outcome = toc_repair.repair_toc(settings, book_id)
    metadata = Path(outcome["metadata_backup_path"])
    before = settings.database_path.read_bytes()
    preview = toc_repair.inspect_toc(settings, book_id, restore_metadata=metadata)
    assert preview["restoring"] and preview["after_toc"] is None
    assert settings.database_path.read_bytes() == before
    restored = toc_repair.repair_toc(settings, book_id, restore_metadata=metadata)
    assert restored["business_data_unchanged"]
    assert _rows(settings, chapter_sql) == original_chapters
    assert _rows(settings, "SELECT toc_json FROM book_versions") == [(None,)]
    assert _rows(settings, "SELECT version FROM books") == [(original_version + 2,)]
    for table, rows in protected.items():
        assert _rows(settings, f"SELECT * FROM {table} ORDER BY id") == rows


@pytest.mark.parametrize("change", ["book_version", "chapter_title"])
def test_metadata_restore_refuses_edits_after_repair(old_library, change):
    settings, book_id, _ = old_library
    outcome = toc_repair.repair_toc(settings, book_id)
    with closing(sqlite3.connect(settings.database_path)) as db:
        if change == "book_version":
            db.execute("UPDATE books SET version=version+1 WHERE id=?", (book_id,))
        else:
            db.execute("UPDATE chapters SET title='修复后读者改名' WHERE ordinal=1")
        db.commit()
    before = settings.database_path.read_bytes()
    backups = set((settings.data_dir / "backups").iterdir())
    with pytest.raises(RuntimeError, match="变化"):
        toc_repair.repair_toc(settings, book_id,
                              restore_metadata=Path(outcome["metadata_backup_path"]))
    assert settings.database_path.read_bytes() == before
    assert set((settings.data_dir / "backups").iterdir()) == backups
