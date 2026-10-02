"""Explicit offline database maintenance; default invocation only reports usage.

Never deletes books, cache entries, tasks, old backups or other data directories.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from ..config import Settings
from ..portable import LibraryLock
from .migrate import run_migrations
from .paths import resolve_within


def _usage(connection: sqlite3.Connection) -> dict[str, int]:
    size = connection.execute("PRAGMA page_size").fetchone()[0]
    return {
        "database_bytes": size * connection.execute("PRAGMA page_count").fetchone()[0],
        "reclaimable_bytes": size * connection.execute("PRAGMA freelist_count").fetchone()[0],
    }


def inspect_database(settings: Settings) -> dict[str, int]:
    """No implicit file creation and no application startup/model activity."""
    with closing(sqlite3.connect(settings.database_path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        return _usage(db)


def _logical_digest(db: sqlite3.Connection) -> str:
    """Compare all business fields, excluding only the expected migration revision change."""
    digest = hashlib.sha256()
    tables = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name!='alembic_version' ORDER BY name"
    ).fetchall()
    for (table,) in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        columns = db.execute(f"PRAGMA table_info({quoted})").fetchall()
        keys = [row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5]]
        if not keys:
            keys = [row[1] for row in columns]
        order = ",".join('"' + key.replace('"', '""') + '"' for key in keys)
        digest.update(repr((table, [row[1] for row in columns])).encode())
        for row in db.execute(f"SELECT * FROM {quoted} ORDER BY {order}"):
            digest.update(repr(row).encode("utf-8"))
    return digest.hexdigest()


def _check(db: sqlite3.Connection, *, full: bool = False) -> None:
    check = "integrity_check" if full else "quick_check"
    if db.execute(f"PRAGMA {check}").fetchall() != [("ok",)]:
        raise RuntimeError("数据库完整性检查失败，请保留书库并检查备份，未继续维护。")
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise RuntimeError("数据库存在失效关联，未继续维护；请先检查书库。")


def _checkpoint(db: sqlite3.Connection) -> None:
    if db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] != 0:
        raise RuntimeError("数据库仍被占用，请停止服务后再维护。")


def _backup(settings: Settings) -> Path:
    """Caller holds an exclusive lock and has checkpointed every committed WAL page."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = resolve_within(
        settings, f"backups/before-compact-{stamp}-{uuid4().hex[:8]}.sqlite3.gz",
    )
    backup.parent.mkdir(parents=True, exist_ok=True)
    partial = backup.with_suffix(backup.suffix + ".partial")
    with settings.database_path.open("rb") as source, partial.open("xb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6) as target:
            shutil.copyfileobj(source, target)
        raw.flush()
        os.fsync(raw.fileno())
    with settings.database_path.open("rb") as source, gzip.open(partial, "rb") as archived:
        if hashlib.file_digest(source, "sha256").digest() != hashlib.file_digest(
            archived, "sha256",
        ).digest():
            raise RuntimeError("压缩备份校验失败，未修改数据库。")
    partial.rename(backup)
    return backup


def compact_database(
    settings: Settings, *, progress: Callable[[str], None] = lambda _: None,
) -> dict[str, object]:
    """Backup, migrate and compact one idle library with a lock held throughout."""
    path = settings.database_path
    if not path.is_file() or path.resolve() != settings.data_dir.resolve() / "ndr.sqlite3":
        raise RuntimeError("数据库不存在或路径包含重定向，未执行维护。")
    if (settings.data_dir / "portable.lock").is_symlink():
        raise RuntimeError("书库锁文件包含重定向，未执行维护。")
    backup: Path | None = None
    with LibraryLock(settings.data_dir), closing(sqlite3.connect(
        path.as_uri() + "?mode=rw", uri=True, timeout=0, isolation_level=None,
    )) as db:
        engine = None
        try:
            db.execute("PRAGMA locking_mode=EXCLUSIVE")
            db.execute("BEGIN EXCLUSIVE")
            db.execute("COMMIT")  # EXCLUSIVE locking mode retains the lock until close.
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'",
            ).fetchone():
                raise RuntimeError("数据库尚未初始化，请先正常启动应用。")
            if db.execute(
                "SELECT 1 FROM jobs WHERE state IN ('QUEUED','RUNNING','PAUSING') LIMIT 1",
            ).fetchone():
                raise RuntimeError("仍有运行或排队任务，请先停止任务并等待结束，再停止服务。")
            before = _usage(db)
            required_space = before["database_bytes"] * 3 + 16 * 1024**2
            if shutil.disk_usage(settings.data_dir).free < required_space:
                raise RuntimeError("磁盘空闲空间不足，请预留数据库大小的三倍空间用于备份和压缩。")
            _check(db)
            progress("正在核对原有数据并创建压缩备份…")
            digest = _logical_digest(db)
            _checkpoint(db)
            backup = _backup(settings)
            progress(f"备份已保存：{backup}")
            engine = create_engine("sqlite://", creator=lambda: db, poolclass=StaticPool)
            with engine.connect() as connection:
                connection.exec_driver_sql("BEGIN EXCLUSIVE")
                run_migrations(settings, connection=connection)
                connection.commit()
            progress("正在压缩数据库，请勿启动服务…")
            db.execute("VACUUM")
            _checkpoint(db)
            _check(db, full=True)
            if _logical_digest(db) != digest:
                raise RuntimeError("维护前后的业务数据不一致，请勿启动服务，先检查备份。")
            after = _usage(db)
            return {
                "before": before, "after": after,
                "saved_bytes": before["database_bytes"] - after["database_bytes"],
                "backup_path": str(backup), "backup_bytes": backup.stat().st_size,
                "business_data_unchanged": True,
            }
        except Exception as exc:
            suffix = f"；原始备份保留在 {backup}" if backup else "；未完成备份，不执行压缩"
            raise RuntimeError(f"数据库维护未完成：{exc}{suffix}") from exc
        finally:
            if engine is not None:
                engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="数据库空间检查与离线压缩（不消耗 Tokens）")
    parser.add_argument("--data-dir", type=Path, help="书库 data 目录；默认沿用应用配置")
    parser.add_argument("--apply", action="store_true", help="停服后执行备份、迁移和压缩")
    args = parser.parse_args()
    settings = Settings(**({"data_dir": args.data_dir} if args.data_dir else {}))
    try:
        result = compact_database(settings, progress=lambda text: print(text, flush=True)) if (
            args.apply
        ) else inspect_database(settings)
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
