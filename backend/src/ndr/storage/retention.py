"""Explicit offline retention with verified backups and semantic invariance checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ..config import Settings
from ..domain.enums import JobState
from ..portable import LibraryLock
from .engine import head_revision
from .history import deduplicate_group_histories
from .maintenance import _backup, _business_columns, _check, _checkpoint, _usage
from .models import Job, PresentationHistoryBaseline, ResultCache, SpeakerGroup


def _validate_path(settings: Settings) -> None:
    path = settings.database_path
    if not path.is_file() or path.resolve() != settings.data_dir.resolve() / "ndr.sqlite3":
        raise RuntimeError("数据库不存在或路径包含重定向，未执行留存维护。")
    if (settings.data_dir / "portable.lock").is_symlink():
        raise RuntimeError("书库锁文件包含重定向，未执行留存维护。")


def _require_current_schema(db: sqlite3.Connection) -> None:
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'",
    ).fetchone():
        raise RuntimeError("数据库尚未初始化，请先完成数据库迁移。")
    if db.execute("SELECT version_num FROM alembic_version").fetchall() != [(head_revision(),)]:
        raise RuntimeError("数据库版本与当前程序不一致，请先单独备份并完成迁移。")


def _history_value(raw, baseline):
    try:
        local = json.loads(raw or "[]")
    except (ValueError, TypeError):
        if baseline is not None:
            raise RuntimeError("共享人物历史的本地修订无效，未继续维护。") from None
        return raw
    if baseline is not None:
        local = json.loads(baseline) + local
    return local


def _semantic_digest(db: sqlite3.Connection, plan: dict) -> str:
    """Only the exact planned execution material and storage representation vary."""
    digest = hashlib.sha256()
    columns_by_table = _business_columns(db)
    baselines = dict(db.execute("SELECT id, history_json FROM presentation_history_baselines"))
    checkpoint_ids = set(plan["checkpoint_job_ids"])
    archive_ids = set(plan["archive_run_ids"])
    for table, columns in sorted(columns_by_table.items()):
        if table == "presentation_history_baselines":
            continue
        quoted = '"' + table.replace('"', '""') + '"'
        digest.update(repr((table, columns)).encode())
        info = db.execute(f"PRAGMA table_info({quoted})").fetchall()
        keys = [row[1] for row in sorted(info, key=lambda row: row[5]) if row[5]] or columns
        order = ",".join('"' + key.replace('"', '""') + '"' for key in keys)
        for raw_row in db.execute(f"SELECT * FROM {quoted} ORDER BY {order}"):
            row = dict(zip(columns, raw_row, strict=True))
            if table == "jobs" and row["id"] in checkpoint_ids:
                checkpoint = json.loads(row["checkpoint_json"] or "{}")
                checkpoint.pop("scene_state", None)
                row["checkpoint_json"] = checkpoint
            if table == "inference_runs" and row["id"] in archive_ids:
                row["call_archive"] = None
            if table == "speaker_groups":
                baseline_id = row.pop("presentation_baseline_id")
                row["presentation_history_json"] = _history_value(
                    row["presentation_history_json"], baselines.get(baseline_id),
                )
            digest.update(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                     default=lambda value: value.hex()).encode())
    return digest.hexdigest()


def _plan_retention(session: Session, *, keep_call_details: bool) -> dict:
    from .retention_policy import plan_completed_job_retention

    checkpoint_ids = []
    archive_ids = []
    job_ids = []
    checkpoint_bytes = 0
    archive_bytes = 0
    jobs = session.scalars(select(Job).where(Job.state == JobState.COMPLETED))
    for job in jobs:
        planned = plan_completed_job_retention(session, job, keep_call_details=keep_call_details)
        if planned["drop_scene_state"] or planned["archive_run_ids"]:
            job_ids.append(job.id)
        if planned["drop_scene_state"]:
            checkpoint_ids.append(job.id)
            checkpoint_bytes += planned["checkpoint_bytes"]
        if not keep_call_details:
            archive_ids.extend(planned["archive_run_ids"])
            archive_bytes += planned["archive_bytes"]
    history_count = 0
    history_bytes = 0
    for group in session.scalars(select(SpeakerGroup)):
        if group.presentation_baseline_id and group._presentation_history_json == "[]":
            continue
        try:
            rows = json.loads(group.presentation_history_json or "[]")
        except (ValueError, TypeError):
            continue
        if isinstance(rows, list) and rows:
            history_count += 1
            history_bytes += len((group._presentation_history_json or "[]").encode())
    return {
        "checkpoint_job_ids": checkpoint_ids,
        "archive_run_ids": archive_ids,
        "retention_job_ids": job_ids,
        "checkpoint_logical_bytes_to_remove": checkpoint_bytes,
        "archive_bytes_to_remove": archive_bytes,
        "history_groups_to_share": history_count,
        "history_inline_bytes_to_share": history_bytes,
        "unowned_cache_rows_preserved": session.scalar(select(func.count()).select_from(
            ResultCache).where(ResultCache.book_version_id.is_(None))),
        "unreferenced_baselines": session.scalar(select(func.count()).select_from(
            PresentationHistoryBaseline).where(~select(SpeakerGroup.id).where(
                SpeakerGroup.presentation_baseline_id == PresentationHistoryBaseline.id,
            ).exists())),
    }


def _public_plan(plan: dict) -> dict:
    return {"completed_checkpoints_to_trim": len(plan["checkpoint_job_ids"]),
            "successful_archives_to_remove": len(plan["archive_run_ids"]),
            **{key: value for key, value in plan.items()
               if key not in {"checkpoint_job_ids", "archive_run_ids", "retention_job_ids"}}}


def inspect_retention(settings: Settings) -> dict:
    """Preview without creating a database, library lock, backup or write transaction."""
    _validate_path(settings)
    with closing(sqlite3.connect(settings.database_path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        engine = create_engine("sqlite://", creator=lambda: db, poolclass=StaticPool)
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql("BEGIN")
                _require_current_schema(db)
                with Session(bind=connection) as session:
                    return {"preview": True, **_public_plan(_plan_retention(
                        session, keep_call_details=settings.retain_completed_call_details,
                    ))}
        finally:
            engine.dispose()


def _apply_retention(session: Session, plan: dict, *, keep_call_details: bool) -> dict:
    from .retention_policy import apply_completed_job_retention

    job_ids = set(plan["retention_job_ids"])
    for job in session.scalars(select(Job).where(Job.state == JobState.COMPLETED)):
        if job.id in job_ids:
            apply_completed_job_retention(session, job, keep_call_details=keep_call_details)
    shared = deduplicate_group_histories(session)
    removed = session.execute(delete(PresentationHistoryBaseline).where(
        ~select(SpeakerGroup.id).where(
            SpeakerGroup.presentation_baseline_id == PresentationHistoryBaseline.id,
        ).exists(),
    )).rowcount
    session.flush()
    return {"shared_history_groups": shared, "removed_unreferenced_baselines": removed}


def apply_retention(
    settings: Settings, *, progress: Callable[[str], None] = lambda _: None,
) -> dict:
    """Back up and trim one idle current-schema library under an exclusive lock."""
    _validate_path(settings)
    backup = None
    with LibraryLock(settings.data_dir), closing(sqlite3.connect(
        settings.database_path.as_uri() + "?mode=rw", uri=True, timeout=0, isolation_level=None,
    )) as db:
        engine = None
        try:
            db.execute("PRAGMA locking_mode=EXCLUSIVE")
            db.execute("BEGIN EXCLUSIVE")
            db.execute("COMMIT")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            _require_current_schema(db)
            if db.execute(
                "SELECT 1 FROM jobs WHERE state IN ('QUEUED','RUNNING','PAUSING') LIMIT 1",
            ).fetchone():
                raise RuntimeError("仍有运行或排队任务，请先停止任务并等待结束，再停止服务。")
            before = _usage(db)
            required_space = before["database_bytes"] * 3 + 16 * 1024**2
            if shutil.disk_usage(settings.data_dir).free < required_space:
                raise RuntimeError("磁盘空闲空间不足，请预留数据库大小的三倍空间用于备份和维护。")
            _check(db)
            _checkpoint(db)
            progress("正在创建并校验完整备份…")
            backup = _backup(settings, purpose="retention")
            engine = create_engine("sqlite://", creator=lambda: db, poolclass=StaticPool)
            with engine.connect() as connection:
                connection.exec_driver_sql("BEGIN EXCLUSIVE")
                with Session(bind=connection, join_transaction_mode="rollback_only") as session:
                    plan = _plan_retention(
                        session, keep_call_details=settings.retain_completed_call_details,
                    )
                    digest = _semantic_digest(db, plan)
                    progress("正在精简已完成执行材料并共享人物展示历史…")
                    changed = _apply_retention(
                        session, plan, keep_call_details=settings.retain_completed_call_details,
                    )
                    _check(db, full=True)
                    if _semantic_digest(db, plan) != digest:
                        raise RuntimeError("留存维护改变了业务结果或必要历史，事务已回滚。")
                    connection.commit()
            return {"preview": False, **_public_plan(plan), **changed,
                    "before": before, "after": _usage(db),
                    "backup_path": str(backup), "backup_bytes": backup.stat().st_size,
                    "business_data_unchanged": True}
        except Exception as exc:
            if db.in_transaction:
                db.rollback()
            suffix = f"；完整备份保留在 {backup}" if backup else "；未完成备份，未执行留存清理"
            raise RuntimeError(f"离线留存维护未完成：{exc}{suffix}") from exc
        finally:
            if engine is not None:
                engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="离线留存检查与已完成执行材料精简")
    parser.add_argument("--data-dir", type=Path, help="书库 data 目录；默认沿用应用配置")
    parser.add_argument("--apply", action="store_true", help="停服后备份并精简已完成执行材料")
    args = parser.parse_args()
    settings = Settings(**({"data_dir": args.data_dir} if args.data_dir else {}))
    try:
        result = apply_retention(settings, progress=lambda value: print(value, flush=True)) if (
            args.apply
        ) else inspect_retention(settings)
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
