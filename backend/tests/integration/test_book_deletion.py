from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from fixtures.corrections import create_fake_profile, run_deterministic_job
from ndr.api.errors import ApiError
from ndr.config import Settings
from ndr.domain.enums import (
    BookFormat,
    CorrectionAction,
    CorrectionTargetType,
    ExportFormat,
    JobState,
)
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.models import (
    Annotation,
    Book,
    BookVersion,
    Correction,
    ExportArtifact,
    ExportSnapshot,
    Job,
    Quote,
)
from ndr.storage.transactions import transaction


def import_book(client: TestClient, body: str) -> dict:
    response = client.post(
        "/api/books/import",
        files={"file": ("同名书.txt", body.encode(), "text/plain")},
        data={"title": "同名书"},
    )
    assert response.status_code == 202, response.text
    return response.json()["data"]


def test_delete_only_selected_book_with_annotations_and_exports(
    fake_provider_client: TestClient, migrated_settings: Settings,
) -> None:
    client = fake_provider_client
    first = import_book(client, "第一章\n「你好。」\n「再见。」")
    other = import_book(client, "第一章\n「另一本书。」")
    profile = create_fake_profile(client)
    run_deterministic_job(
        migrated_settings, client, book_id=first["book_id"], profile_id=profile,
        key="delete-annotated",
    )
    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            quote_id = session.execute(select(Quote.id).where(
                Quote.book_version_id == first["book_version_id"],
            )).scalars().first()
            correction = Correction(
                target_type=CorrectionTargetType.QUOTE,
                target_id=quote_id,
                action=CorrectionAction.MARK_UNKNOWN,
            )
            session.add(correction)
            snapshot = ExportSnapshot(
                book_id=first["book_id"], book_version_id=first["book_version_id"],
                source_revision="revision", visibility_policy="reread", snapshot_hash="a" * 64,
            )
            session.add(snapshot)
            session.flush()
            artifact = ExportArtifact(
                snapshot_id=snapshot.id, format=ExportFormat.HTML,
                exporter_version="test", fingerprint="b" * 64,
            )
            session.add(artifact)
            session.flush()
            artifact_id, correction_id = artifact.id, correction.id

        root = migrated_settings.data_dir
        export_dir = root / "exports" / artifact_id
        export_dir.mkdir(parents=True)
        (export_dir / "book.html").write_text("test", encoding="utf-8")
        response = client.delete(f"/api/books/{first['book_id']}")
        assert response.status_code == 204, response.text
        assert response.content == b""
        assert client.get(f"/api/books/{first['book_id']}").status_code == 404
        assert client.get(f"/api/jobs/{first['job_id']}").status_code == 404
        assert client.get(f"/api/books/{other['book_id']}").status_code == 200
        assert [item["id"] for item in client.get("/api/books").json()["data"]["items"]] == [
            other["book_id"],
        ]
        assert not (root / "books" / first["book_id"]).exists()
        assert (root / "books" / other["book_id"]).exists()
        assert not export_dir.exists()
        assert list((root / "trash").iterdir()) == []
        with transaction(factory) as session:
            assert session.get(BookVersion, first["book_version_id"]) is None
            assert session.get(Correction, correction_id) is None
            assert session.get(ExportArtifact, artifact_id) is None
            assert session.execute(select(Annotation)).first() is None

        # 删除后重新导入不复用已删除记录。
        again = import_book(client, "第一章\n「你好。」\n「再见。」")
        assert again["book_id"] != first["book_id"]
    finally:
        engine.dispose()


@pytest.mark.parametrize("state", [JobState.QUEUED, JobState.RUNNING, JobState.PAUSING])
def test_delete_rejects_active_tasks(
    migrated_client: TestClient, migrated_settings: Settings, state: JobState,
) -> None:
    imported = import_book(migrated_client, "第一章\n「你好。」")
    engine = create_db_engine(migrated_settings)
    try:
        with transaction(create_session_factory(engine)) as session:
            session.get(Job, imported["job_id"]).state = state
        response = migrated_client.delete(f"/api/books/{imported['book_id']}")
        assert response.status_code == 409
        assert "任务" in response.json()["error"]["message"]
        assert (migrated_settings.data_dir / "books" / imported["book_id"]).exists()
        assert migrated_client.get(f"/api/books/{imported['book_id']}").status_code == 200
    finally:
        engine.dispose()


def test_delete_missing_book(migrated_client: TestClient) -> None:
    assert migrated_client.delete("/api/books/missing").status_code == 404


def test_file_move_failure_keeps_book_and_rolls_back_records(
    migrated_client: TestClient, migrated_settings: Settings, monkeypatch,
) -> None:
    imported = import_book(migrated_client, "第一章\n「你好。」")
    engine = create_db_engine(migrated_settings)
    try:
        with transaction(create_session_factory(engine)) as session:
            snapshot = ExportSnapshot(
                book_id=imported["book_id"], book_version_id=imported["book_version_id"],
                source_revision="revision", visibility_policy="reread", snapshot_hash="a" * 64,
            )
            session.add(snapshot)
            session.flush()
            artifact = ExportArtifact(
                snapshot_id=snapshot.id, format=ExportFormat.HTML,
                exporter_version="test", fingerprint="b" * 64,
            )
            session.add(artifact)
            session.flush()
            artifact_id = artifact.id
    finally:
        engine.dispose()
    export_dir = migrated_settings.data_dir / "exports" / artifact_id
    export_dir.mkdir(parents=True)
    original_rename = Path.rename
    def fail_move(path, target):
        # 文件开始移动时，数据库清理已完成（仍未提交，对其他连接不可见）。
        # 独立读取仍应见到原书，失败后则必须回滚全部记录和已移动文件。
        if path == export_dir:
            raise OSError("文件被占用")
        return original_rename(path, target)
    monkeypatch.setattr(Path, "rename", fail_move)
    response = migrated_client.delete(f"/api/books/{imported['book_id']}")
    assert response.status_code == 503
    assert "无法移动书籍文件" in response.json()["error"]["message"]
    assert migrated_client.get(f"/api/books/{imported['book_id']}").status_code == 200
    assert (migrated_settings.data_dir / "books" / imported["book_id"]).exists()
    assert export_dir.exists()


def test_database_delete_precedes_file_move(
    migrated_client: TestClient, migrated_settings: Settings, monkeypatch,
) -> None:
    from sqlalchemy import event

    from ndr.ingest.deletion import delete_book

    imported = import_book(migrated_client, "第一章\n「你好。」")
    engine = create_db_engine(migrated_settings)
    deleted = []

    @event.listens_for(engine, "after_cursor_execute")
    def track_delete(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("DELETE FROM books"):
            deleted.append(True)

    original_rename = Path.rename

    def check_move(path, target):
        assert deleted, "正文文件不可先于数据库清理移走"
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", check_move)
    try:
        factory = create_session_factory(engine)
        delete_book(factory, migrated_settings, imported["book_id"])
        with transaction(factory) as session:
            assert session.get(Book, imported["book_id"]) is None
    finally:
        engine.dispose()


def test_successful_delete_preserves_previous_trash_and_external_upload(
    migrated_client: TestClient, migrated_settings: Settings, tmp_path: Path,
) -> None:
    imported = import_book(migrated_client, "第一章\n「你好。」")
    previous = migrated_settings.data_dir / "trash" / "previous-deletion" / "source.txt"
    previous.parent.mkdir(parents=True)
    previous.write_text("以前留下的文件", encoding="utf-8")
    upload = tmp_path / "original.txt"
    upload.write_text("外部源文件", encoding="utf-8")

    response = migrated_client.delete(f"/api/books/{imported['book_id']}")

    assert response.status_code == 204, response.text
    assert previous.read_text(encoding="utf-8") == "以前留下的文件"
    assert upload.read_text(encoding="utf-8") == "外部源文件"
    assert list(previous.parent.parent.iterdir()) == [previous.parent]


def test_commit_failure_restores_files_without_running_cleanup(
    migrated_client: TestClient, migrated_settings: Settings, monkeypatch,
) -> None:
    from sqlalchemy import event

    from ndr.ingest import deletion

    imported = import_book(migrated_client, "第一章\n「提交失败仍可读。」")
    source = migrated_settings.data_dir / "books" / imported["book_id"] / "source.txt"
    original = source.read_bytes()
    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)

    def fail_commit(session):
        assert not source.exists(), "本测试必须在文件移到暂存后触发提交失败"
        raise RuntimeError("模拟事务提交失败")

    def reject_cleanup(*args):
        pytest.fail("提交失败不得永久清理暂存文件")

    monkeypatch.setattr(deletion, "_remove_staged_files", reject_cleanup)
    event.listen(factory, "before_commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="模拟事务提交失败"):
            deletion.delete_book(factory, migrated_settings, imported["book_id"])
        assert source.read_bytes() == original
        assert migrated_client.get(f"/api/books/{imported['book_id']}").status_code == 200
    finally:
        event.remove(factory, "before_commit", fail_commit)
        engine.dispose()


def test_cleanup_failure_keeps_success_response_and_logs_residual(
    migrated_client: TestClient, migrated_settings: Settings, monkeypatch, caplog,
) -> None:
    from ndr.ingest import deletion

    imported = import_book(migrated_client, "第一章\n「清理失败。」")
    root = migrated_settings.data_dir

    def fail_cleanup(path):
        engine = create_db_engine(migrated_settings)
        try:
            with transaction(create_session_factory(engine)) as session:
                assert session.get(Book, imported["book_id"]) is None
        finally:
            engine.dispose()
        assert path.parent == root / "trash"
        raise PermissionError("模拟文件占用")

    monkeypatch.setattr(deletion.shutil, "rmtree", fail_cleanup)
    with caplog.at_level("WARNING", logger="ndr.diagnostics"):
        response = migrated_client.delete(f"/api/books/{imported['book_id']}")

    assert response.status_code == 204, response.text
    assert migrated_client.get(f"/api/books/{imported['book_id']}").status_code == 404
    assert list((root / "trash").glob(f"*/books/{imported['book_id']}/source.txt"))
    assert "book_delete_cleanup_pending" in caplog.text
    assert "PermissionError" in caplog.text
    assert "模拟文件占用" not in caplog.text


def directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("redirect", ["book", "trash"])
@pytest.mark.parametrize("outside", [False, True])
def test_redirected_owned_directory_is_rejected_without_deleting_data(
    migrated_client: TestClient, migrated_settings: Settings, tmp_path: Path,
    redirect: str, outside: bool,
) -> None:
    imported = import_book(migrated_client, "第一章\n「路径保护。」")
    root = migrated_settings.data_dir
    target = (tmp_path if outside else root) / "retained"
    source = root / "books" / imported["book_id"]
    if redirect == "book":
        source.rename(target)
        link = source
    else:
        target.mkdir()
        link = root / "trash"
    sentinel = target / "sentinel.txt"
    sentinel.write_text("保留", encoding="utf-8")
    directory_link(link, target)
    try:
        response = migrated_client.delete(f"/api/books/{imported['book_id']}")

        assert response.status_code == 422, response.text
        assert "重定向" in response.json()["error"]["message"]
        assert sentinel.read_text(encoding="utf-8") == "保留"
        assert source.exists()
        assert migrated_client.get(f"/api/books/{imported['book_id']}").status_code == 200
    finally:
        # Remove only the link, leaving its target for the isolated fixture cleanup.
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


def test_cleanup_rejects_redirect_in_staged_tree_and_preserves_target(
    migrated_client: TestClient, migrated_settings: Settings, tmp_path: Path, caplog,
) -> None:
    imported = import_book(migrated_client, "第一章\n「嵌套链接保护。」")
    root = migrated_settings.data_dir
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("保留", encoding="utf-8")
    directory_link(root / "books" / imported["book_id"] / "linked", external)
    try:
        with caplog.at_level("WARNING", logger="ndr.diagnostics"):
            response = migrated_client.delete(f"/api/books/{imported['book_id']}")

        assert response.status_code == 204, response.text
        assert migrated_client.get(f"/api/books/{imported['book_id']}").status_code == 404
        assert sentinel.read_text(encoding="utf-8") == "保留"
        assert "book_delete_cleanup_pending" in caplog.text
        assert list((root / "trash").glob(f"*/books/{imported['book_id']}/source.txt"))
    finally:
        for link in (root / "trash").glob(f"*/books/{imported['book_id']}/linked"):
            if os.name == "nt":
                link.rmdir()
            else:
                link.unlink()


@pytest.mark.parametrize("mode, attributes", [
    (stat.S_IFLNK, 0),
    (stat.S_IFDIR, stat.FILE_ATTRIBUTE_REPARSE_POINT),
    (stat.S_IFREG, stat.FILE_ATTRIBUTE_REPARSE_POINT),
])
def test_cleanup_rejects_symlink_and_reparse_metadata(mode: int, attributes: int) -> None:
    from ndr.ingest.deletion import _reject_redirect

    with pytest.raises(ApiError, match="重定向"):
        _reject_redirect(SimpleNamespace(st_mode=mode, st_file_attributes=attributes))


def test_empty_book_id_cannot_remove_entire_books_directory(
    migrated_client: TestClient, migrated_settings: Settings,
) -> None:
    from ndr.ingest.deletion import delete_book

    other = import_book(migrated_client, "第一章\n「正常书籍。」")
    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            session.add(Book(id="", title="异常ID夹具", format=BookFormat.TXT, source_sha256="a" * 64))
        with pytest.raises(ApiError, match="书籍文件目录无效"):
            delete_book(factory, migrated_settings, "")
        assert (migrated_settings.data_dir / "books" / other["book_id"] / "source.txt").exists()
        with transaction(factory) as session:
            assert session.get(Book, "") is not None
    finally:
        engine.dispose()
