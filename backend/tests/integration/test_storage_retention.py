"""Offline retention operates only on disposable original fixtures with complete backups."""

from __future__ import annotations

import gzip
import json
import sqlite3
from collections import namedtuple
from contextlib import closing
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from ndr.characters.visibility import capture
from ndr.ingest.service import import_txt
from ndr.portable import LibraryInUseError, LibraryLock
from ndr.storage.engine import create_db_engine
from ndr.storage.history import share_history
from ndr.storage.maintenance import _logical_digest
from ndr.storage.migrate import run_migrations
from ndr.storage.models import (
    Book,
    BookCharacter,
    InferenceRun,
    Job,
    JobWindow,
    PresentationHistoryBaseline,
    ResultCache,
    Scene,
    SpeakerGroup,
)
from ndr.storage.retention import apply_retention, inspect_retention


def _seed(settings):
    run_migrations(settings)
    engine = create_db_engine(settings)
    try:
        with Session(engine) as session:
            imported = import_txt(session, settings, filename="retention-original.txt",
                                  raw="第一章\n\n「早安。」\n".encode())
            person = BookCharacter(book_version_id=imported.version.id, canonical_name="林舟",
                                   identity_facts_json='[{"kind":"original-fixture"}]')
            session.add(person)
            session.flush()
            rows = [{"cp": 0, "identity": f"character:{person.id}", "name": "林舟",
                     "description": "原创描述", "extra": [1, 2]}]
            for index in range(2):
                scene = Scene(book_version_id=imported.version.id, start_cp=index)
                session.add(scene)
                session.flush()
                session.add(SpeakerGroup(scene_id=scene.id, display_label="A",
                    character_id=person.id, presentation_history_json=json.dumps(rows)))
            for state in ("COMPLETED", "PAUSED", "FAILED"):
                job = Job(kind="INFERENCE", purpose="process", book_id=imported.book.id,
                    book_version_id=imported.version.id, state=state,
                    checkpoint_json=json.dumps({"scene_state": {"large": "材料" * 100},
                                                "auxiliary_warnings": ["保留"],
                                                "expression_reviews": {"q": {"identity_feedback": "保留反馈"}}}))
                session.add(job)
                session.flush()
                session.add(JobWindow(job_id=job.id, window_id=f"w-{state}", state="COMPLETED"))
                for total in (10, None):
                    session.add(InferenceRun(job_id=job.id, state="SUCCEEDED",
                        profile_snapshot_json="{}", request_fingerprint="a" * 64,
                        usage_json=json.dumps({"total_tokens": total}), call_archive=b"fixture-archive"))
            session.add(ResultCache(cache_key="unknown", schema_version="1", result_json="{}"))
            session.commit()
            return imported.book.id
    finally:
        engine.dispose()


def _digest(settings):
    with closing(sqlite3.connect(settings.database_path)) as db:
        return _logical_digest(db)


def test_retention_preview_is_read_only_and_apply_keeps_business_results(tmp_settings, tmp_path, monkeypatch):
    book_id = _seed(tmp_settings)
    before = _digest(tmp_settings)
    original_file = tmp_settings.database_path.read_bytes()
    planned = inspect_retention(tmp_settings)
    assert planned["preview"]
    assert planned["completed_checkpoints_to_trim"] == 1
    assert planned["successful_archives_to_remove"] == 1
    assert planned["history_groups_to_share"] == 2
    assert planned["unowned_cache_rows_preserved"] == 1
    assert tmp_settings.database_path.read_bytes() == original_file
    assert not (tmp_settings.data_dir / "backups").exists()
    locks_checked = []

    def progress(message):
        with closing(sqlite3.connect(tmp_settings.database_path, timeout=0)) as other, pytest.raises(
            sqlite3.OperationalError, match="locked",
        ):
            other.execute("SELECT count(*) FROM books").fetchone()
        locks_checked.append(message)

    statements = []
    original_connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        db = original_connect(*args, **kwargs)
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr("ndr.storage.retention.sqlite3.connect", traced_connect)
    outcome = apply_retention(tmp_settings, progress=progress)
    assert outcome["business_data_unchanged"]
    assert outcome["shared_history_groups"] == 2
    assert not any("VACUUM" in statement.upper() for statement in statements)
    assert "before-retention-" in outcome["backup_path"]
    assert len(locks_checked) == 2
    restored = tmp_path / "retention-restored.sqlite3"
    with gzip.open(outcome["backup_path"], "rb") as archive:
        restored.write_bytes(archive.read())
    with closing(sqlite3.connect(restored)) as db:
        assert _logical_digest(db) == before
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    engine = create_db_engine(tmp_settings)
    try:
        with Session(engine) as session:
            assert session.get(Book, book_id).read_position_cp == 0
            assert session.scalar(select(BookCharacter)).identity_facts_json == '[{"kind":"original-fixture"}]'
            assert session.scalar(select(ResultCache)).cache_key == "unknown"
            for group in session.scalars(select(SpeakerGroup)):
                assert group.presentation_baseline_id
                assert json.loads(group.presentation_history_json)[0]["extra"] == [1, 2]
            for job in session.scalars(select(Job).where(Job.kind == "INFERENCE")):
                checkpoint = json.loads(job.checkpoint_json)
                assert ("scene_state" in checkpoint) == (job.state.value != "COMPLETED")
                assert checkpoint["auxiliary_warnings"] == ["保留"]
                runs = list(session.scalars(select(InferenceRun).where(InferenceRun.job_id == job.id)))
                for run in runs:
                    removable = job.state.value == "COMPLETED" and json.loads(run.usage_json)["total_tokens"] == 10
                    assert (run.call_archive is None) == removable
    finally:
        engine.dispose()
    again = inspect_retention(tmp_settings)
    assert again["completed_checkpoints_to_trim"] == 0
    assert again["successful_archives_to_remove"] == 0
    assert again["history_groups_to_share"] == 0


@pytest.mark.parametrize("state", ["QUEUED", "RUNNING", "PAUSING"])
def test_retention_refuses_active_jobs(tmp_settings, state):
    _seed(tmp_settings)
    with closing(sqlite3.connect(tmp_settings.database_path)) as db:
        db.execute("UPDATE jobs SET state=? WHERE state='PAUSED'", (state,))
        db.commit()
    before = _digest(tmp_settings)
    with pytest.raises(RuntimeError, match="运行或排队"):
        apply_retention(tmp_settings)
    assert _digest(tmp_settings) == before
    assert not (tmp_settings.data_dir / "backups").exists()


def test_retention_refuses_missing_old_schema_and_library_lock(tmp_settings):
    with pytest.raises(RuntimeError, match="不存在"):
        inspect_retention(tmp_settings)
    assert not tmp_settings.data_dir.exists()
    run_migrations(tmp_settings, revision="0023")
    with pytest.raises(RuntimeError, match="版本"):
        inspect_retention(tmp_settings)
    with pytest.raises(RuntimeError, match="版本"):
        apply_retention(tmp_settings)
    run_migrations(tmp_settings)
    with LibraryLock(tmp_settings.data_dir), pytest.raises(LibraryInUseError):
        apply_retention(tmp_settings)
    assert not (tmp_settings.data_dir / "backups").exists()


@pytest.mark.parametrize("journal", ["DELETE", "WAL"])
def test_retention_refuses_concurrent_sqlite_readers(tmp_settings, journal):
    _seed(tmp_settings)
    with closing(sqlite3.connect(tmp_settings.database_path)) as reader:
        reader.execute(f"PRAGMA journal_mode={journal}")
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM books").fetchall()
        with pytest.raises(RuntimeError, match="locked"):
            apply_retention(tmp_settings)
    assert not (tmp_settings.data_dir / "backups").exists()


@pytest.mark.parametrize("failure", ["space", "backup", "integrity"])
def test_retention_preparation_failure_preserves_database(tmp_settings, monkeypatch, failure):
    _seed(tmp_settings)
    before = _digest(tmp_settings)
    module = "ndr.storage.retention."
    if failure == "space":
        usage = namedtuple("usage", "total used free")(100, 100, 0)
        monkeypatch.setattr(module + "shutil.disk_usage", lambda _: usage)
    elif failure == "backup":
        monkeypatch.setattr(module + "_backup", lambda _: (_ for _ in ()).throw(OSError("backup")))
    else:
        monkeypatch.setattr(module + "_check", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("integrity")))
    with pytest.raises(RuntimeError):
        apply_retention(tmp_settings)
    assert _digest(tmp_settings) == before


@pytest.mark.parametrize("field", ["book", "facts", "usage", "history", "feedback"])
def test_retention_rolls_back_unexpected_business_changes(tmp_settings, monkeypatch, field):
    from ndr.storage import retention

    _seed(tmp_settings)
    before = _digest(tmp_settings)
    original_apply = retention._apply_retention

    def corrupt(session, plan, **kwargs):
        result = original_apply(session, plan, **kwargs)
        statements = {
            "book": "UPDATE books SET read_position_cp=1",
            "facts": "UPDATE book_characters SET identity_facts_json='[]'",
            "usage": "UPDATE inference_runs SET usage_json='{}'",
            "history": "UPDATE presentation_history_baselines SET history_json='[]'",
            "feedback": "UPDATE jobs SET checkpoint_json='{}' WHERE kind='INFERENCE' AND state='COMPLETED'",
        }
        session.connection().exec_driver_sql(statements[field])
        return result

    monkeypatch.setattr(retention, "_apply_retention", corrupt)
    with pytest.raises(RuntimeError, match="业务结果或必要历史"):
        apply_retention(tmp_settings)
    assert _digest(tmp_settings) == before
    assert len(list((tmp_settings.data_dir / "backups").glob("*.sqlite3.gz"))) == 1


def test_retention_honors_debug_archive_retention(tmp_settings):
    _seed(tmp_settings)
    settings = tmp_settings.model_copy(update={"retain_completed_call_details": True})
    assert inspect_retention(settings)["successful_archives_to_remove"] == 0
    outcome = apply_retention(settings)
    assert outcome["completed_checkpoints_to_trim"] == 1
    with closing(sqlite3.connect(settings.database_path)) as db:
        assert db.execute("SELECT count(*) FROM inference_runs WHERE call_archive IS NULL").fetchone() == (0,)


def test_retention_refuses_redirected_path_and_uninitialized_database(tmp_settings, tmp_path, monkeypatch):
    outside = tmp_path / "elsewhere.sqlite3"
    outside.touch()
    with pytest.raises(RuntimeError, match="重定向"):
        inspect_retention(SimpleNamespace(database_path=outside, data_dir=tmp_settings.data_dir))
    tmp_settings.ensure_data_dir()
    tmp_settings.database_path.touch()
    with pytest.raises(RuntimeError, match="尚未初始化"):
        apply_retention(tmp_settings)
    run_migrations(tmp_settings)
    original_is_symlink = type(tmp_settings.data_dir).is_symlink

    def is_symlink(path):
        return path.name == "portable.lock" or original_is_symlink(path)

    monkeypatch.setattr(type(tmp_settings.data_dir), "is_symlink", is_symlink)
    with pytest.raises(RuntimeError, match="锁文件包含重定向"):
        apply_retention(tmp_settings)


def test_retention_rebases_existing_baseline_delta_without_changing_timestamps(tmp_settings):
    _seed(tmp_settings)
    engine = create_db_engine(tmp_settings)
    try:
        with Session(engine) as session:
            group = session.scalar(select(SpeakerGroup))
            share_history(group, json.loads(group.presentation_history_json))
            group.canonical_name = "后来的称呼"
            capture(group, 5)
            session.commit()
            group_id = group.id
            original_history = json.loads(group.presentation_history_json)
            original_updated_at = group.updated_at
            original_version = group.version
        engine.dispose()
        result = apply_retention(tmp_settings)
        assert result["business_data_unchanged"]
        engine = create_db_engine(tmp_settings)
        with Session(engine) as session:
            group = session.get(SpeakerGroup, group_id)
            assert json.loads(group.presentation_history_json) == original_history
            assert group._presentation_history_json == "[]"
            assert group.updated_at == original_updated_at
            assert group.version == original_version
    finally:
        engine.dispose()


def test_retention_removes_only_unreferenced_baselines(tmp_settings):
    _seed(tmp_settings)
    engine = create_db_engine(tmp_settings)
    try:
        with Session(engine) as session:
            scene = session.scalar(select(Scene))
            session.add(PresentationHistoryBaseline(id="orphan", book_version_id=scene.book_version_id,
                digest="a" * 64, history_json='[{"cp":0,"name":"unused"}]'))
            session.commit()
    finally:
        engine.dispose()
    assert inspect_retention(tmp_settings)["unreferenced_baselines"] == 1
    result = apply_retention(tmp_settings)
    assert result["removed_unreferenced_baselines"] == 1
    engine = create_db_engine(tmp_settings)
    try:
        with Session(engine) as session:
            assert session.get(PresentationHistoryBaseline, "orphan") is None
            assert all(group.presentation_baseline is not None
                       for group in session.scalars(select(SpeakerGroup)))
    finally:
        engine.dispose()


def test_retention_preview_uses_one_physical_read_snapshot(tmp_settings, monkeypatch):
    from ndr.storage import retention

    _seed(tmp_settings)
    original_plan = retention._plan_retention

    def concurrent_write(session, **kwargs):
        with closing(sqlite3.connect(tmp_settings.database_path)) as writer:
            writer.execute("INSERT INTO result_cache (id,cache_key,schema_version,result_json,created_at,updated_at) VALUES ('concurrent','concurrent','1','{}','2026-11-13','2026-11-13')")
            writer.commit()
        return original_plan(session, **kwargs)

    monkeypatch.setattr(retention, "_plan_retention", concurrent_write)
    assert inspect_retention(tmp_settings)["unowned_cache_rows_preserved"] == 1
    with closing(sqlite3.connect(tmp_settings.database_path)) as db:
        assert db.execute("SELECT count(*) FROM result_cache").fetchone() == (2,)
