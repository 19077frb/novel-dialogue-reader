"""Explicit offline EPUB navigation repair; never reimports or runs inference."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from ..config import Settings
from ..ingest.epub import parse_epub
from ..ingest.toc import bind_toc
from ..portable import LibraryLock
from .maintenance import _backup, _business_columns, _check, _checkpoint
from .migrate import run_migrations

_BLOCKING = ('QUEUED', 'RUNNING', 'PAUSING', 'PAUSED', 'PARTIAL', 'BUDGET_EXHAUSTED')


def _path(settings: Settings, relative: str) -> Path:
    root = settings.data_dir.resolve()
    candidate = Path(relative)
    resolved = (root / candidate).resolve()
    if candidate.is_absolute() or root not in resolved.parents:
        raise RuntimeError('书籍文件路径不在书库内，未继续修复')
    return resolved


def _validate_database(settings: Settings) -> None:
    path = settings.database_path
    if not path.is_file() or path.resolve() != settings.data_dir.resolve() / 'ndr.sqlite3':
        raise RuntimeError('数据库不存在或路径包含重定向')
    if (settings.data_dir / 'portable.lock').is_symlink():
        raise RuntimeError('书库锁文件包含重定向')


def _idle(db: sqlite3.Connection, book_id: str, *, global_running: bool = False) -> None:
    markers = ','.join('?' for _ in _BLOCKING)
    if db.execute(
        f'SELECT 1 FROM jobs WHERE book_id=? AND state IN ({markers}) LIMIT 1',
        (book_id, *_BLOCKING),
    ).fetchone():
        raise RuntimeError('本书有在途或可恢复任务，未执行目录修复')
    if global_running and db.execute(
        "SELECT 1 FROM jobs WHERE state IN ('QUEUED','RUNNING','PAUSING') LIMIT 1",
    ).fetchone():
        raise RuntimeError('书库仍有运行或排队任务，请先正常结束任务并关闭服务')


def _row(db: sqlite3.Connection, sql: str, args: tuple) -> dict | None:
    cursor = db.execute(sql, args)
    row = cursor.fetchone()
    return dict(zip((col[0] for col in cursor.description), row, strict=True)) if row else None


def _plan(db: sqlite3.Connection, settings: Settings, book_id: str) -> dict:
    book = _row(db, 'SELECT * FROM books WHERE id=?', (book_id,))
    if not book or book['format'] != 'EPUB' or not book['active_version_id']:
        raise RuntimeError('只能为已有有效版本的 EPUB 补齐目录')
    _idle(db, book_id)
    version = _row(db, 'SELECT * FROM book_versions WHERE id=?', (book['active_version_id'],))
    if not version or not version['source_path'] or not version['canonical_path']:
        raise RuntimeError('版本原文件或正文路径缺失，未继续修复')
    source = _path(settings, version['source_path']).read_bytes()
    if hashlib.sha256(source).hexdigest() != book['source_sha256']:
        raise RuntimeError('原文件摘要与书库记录不一致，未继续修复')
    parsed = parse_epub(source)
    canonical = _path(settings, version['canonical_path']).read_bytes()
    if (parsed.canonical_sha256 != version['canonical_sha256']
            or parsed.canonical_length_cp != version['canonical_length_cp']
            or hashlib.sha256(canonical).hexdigest() != version['canonical_sha256']):
        raise RuntimeError('重新解析结果与现有正文不一致，禁止仅修目录')
    cursor = db.execute(
        'SELECT * FROM chapters WHERE book_version_id=? ORDER BY ordinal', (version['id'],),
    )
    columns = [col[0] for col in cursor.description]
    chapters = [dict(zip(columns, row, strict=True)) for row in cursor]
    # Older parsers attached image-only title plates to the next chapter. A later
    # parser may expose additional empty chapters without changing any text range.
    # Match every existing boundary exactly; never create those extra chapters.
    fields = ('source_href', 'start_cp', 'end_cp')
    parsed_by_range = {tuple(getattr(chapter, key) for key in fields): chapter
                       for chapter in parsed.chapters}
    if len(parsed_by_range) != len(parsed.chapters):
        raise RuntimeError('解析章节来源与边界重复，不能唯一关联')
    matched = []
    for old in chapters:
        new = parsed_by_range.get(tuple(old[key] for key in fields))
        if new is None:
            raise RuntimeError(f'章节 {old["ordinal"]} 的来源或边界不一致，禁止仅修目录')
        matched.append(new)
    ordinals = [chapter.ordinal for chapter in matched]
    if ordinals != sorted(set(ordinals)):
        raise RuntimeError('章节顺序不一致，禁止仅修目录')
    extras = [chapter for chapter in parsed.chapters if chapter.ordinal not in set(ordinals)]
    if any(chapter.start_cp != chapter.end_cp for chapter in extras):
        raise RuntimeError('正文章节数量改变，禁止仅修目录')
    ordinal_map = {new.ordinal: old['ordinal']
                   for old, new in zip(chapters, matched, strict=True)}
    bound = replace(parsed, toc=tuple({**entry, 'chapter_ordinal': ordinal_map.get(
        entry.get('chapter_ordinal'),
    )} for entry in parsed.toc))
    toc = bind_toc(bound, [SimpleNamespace(**chapter) for chapter in chapters])
    by_node = {entry['id']: entry for entry in parsed.toc}
    legacy: dict[int, set[str]] = {}
    for entry in parsed.toc:
        ordinal = entry.get('chapter_ordinal')
        if ordinal is None:
            continue
        label = entry['title']
        parent = by_node.get(entry.get('parent_id'))
        if parent and parent['title']:
            label = f'{parent["title"]} · {label}'
        legacy.setdefault(ordinal, set()).add(label)
    changes = []
    for old, new in zip(chapters, matched, strict=True):
        if old['title'] == new.title:
            continue
        if old['title'] not in legacy.get(new.ordinal, set()):
            raise RuntimeError(f'章节 {old["ordinal"]} 存在人工改名或未知差异，未覆盖')
        changes.append({'id': old['id'], 'ordinal': old['ordinal'],
                        'before': old['title'], 'after': new.title,
                        'updated_at': old['updated_at']})
    encoded = json.dumps(toc, ensure_ascii=False, separators=(',', ':'))
    return {'book_id': book_id, 'version_id': version['id'], 'chapter_count': len(chapters),
            'root_count': sum(node['parent_id'] is None for node in toc), 'node_count': len(toc),
            'canonical_sha256': version['canonical_sha256'], 'title_changes': changes,
            'before_toc': version.get('toc_json'), 'after_toc': encoded,
            'book_version': book['version'], 'book_updated_at': book['updated_at'],
            'version_updated_at': version['updated_at'],
            'preserved_empty_page_groups': len(extras),
            'changed': bool(changes) or version.get('toc_json') != encoded}


def _restore_plan(db: sqlite3.Connection, settings: Settings, book_id: str, backup: Path) -> dict:
    location = backup.resolve()
    if (settings.data_dir.resolve() / 'backups') not in location.parents:
        raise RuntimeError('目录元数据回退文件必须位于本书库 backups 内')
    saved = json.loads(location.read_text(encoding='utf-8'))
    if saved.get('book_id') != book_id:
        raise RuntimeError('回退文件不属于指定书籍')
    _idle(db, book_id)
    book = _row(db, 'SELECT * FROM books WHERE id=?', (book_id,))
    version = _row(db, 'SELECT * FROM book_versions WHERE id=?', (saved['version_id'],))
    if (not book or not version or book['active_version_id'] != saved['version_id']
            or version['canonical_sha256'] != saved['canonical_sha256']
            or book['version'] != saved['book_version'] + 1
            or version.get('toc_json') != saved['after_toc']):
        raise RuntimeError('修复后书籍版本或目录已变化，不能直接回退')
    changes = []
    for change in saved['title_changes']:
        chapter = _row(db, 'SELECT * FROM chapters WHERE id=?', (change['id'],))
        if (not chapter or chapter['book_version_id'] != version['id']
                or chapter['ordinal'] != change['ordinal'] or chapter['title'] != change['after']):
            raise RuntimeError('修复后章节已变化，不能直接回退')
        changes.append({**change, 'before': change['after'], 'after': change['before'],
                        'updated_at': chapter['updated_at']})
    old_toc = json.loads(saved['before_toc']) if saved['before_toc'] is not None else []
    return {**saved, 'title_changes': changes, 'before_toc': saved['after_toc'],
            'after_toc': saved['before_toc'], 'book_version': book['version'],
            'book_updated_at': book['updated_at'], 'version_updated_at': version['updated_at'],
            'root_count': sum(node['parent_id'] is None for node in old_toc),
            'node_count': len(old_toc), 'changed': True, 'restoring': True}


def inspect_toc(settings: Settings, book_id: str, *, restore_metadata: Path | None = None) -> dict:
    """Read-only preview, including on pre-tree schemas. Creates no files or jobs."""
    _validate_database(settings)
    with closing(sqlite3.connect(settings.database_path.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        return (_restore_plan(db, settings, book_id, restore_metadata) if restore_metadata
                else _plan(db, settings, book_id))


def _digest(db: sqlite3.Connection, columns: dict, plan: dict) -> str:
    """Verify every old business field except the exact metadata rows being repaired."""
    digest = hashlib.sha256()
    changed = {item['id'] for item in plan['title_changes']}
    for table, names in sorted(columns.items()):
        quoted = '"' + table.replace('"', '""') + '"'
        selected = ','.join('"' + name.replace('"', '""') + '"' for name in names)
        info = db.execute(f'PRAGMA table_info({quoted})').fetchall()
        keys = [row[1] for row in sorted(info, key=lambda row: row[5]) if row[5]] or names
        order = ','.join('"' + key.replace('"', '""') + '"' for key in keys)
        digest.update(repr((table, names)).encode())
        for row in db.execute(f'SELECT {selected} FROM {quoted} ORDER BY {order}'):
            values = dict(zip(names, row, strict=True))
            ignored = set()
            if table == 'books' and values.get('id') == plan['book_id']:
                ignored = {'version', 'updated_at'}
            elif table == 'book_versions' and values.get('id') == plan['version_id']:
                ignored = {'toc_json', 'updated_at'}
            elif table == 'chapters' and values.get('id') in changed:
                ignored = {'title', 'updated_at'}
            digest.update(repr(tuple(None if name in ignored else values[name]
                                     for name in names)).encode('utf-8'))
    return digest.hexdigest()


def repair_toc(settings: Settings, book_id: str, *, restore_metadata: Path | None = None) -> dict:
    """Backup and update one idle library under both application and SQLite locks."""
    _validate_database(settings)
    backup = None
    engine = None
    with LibraryLock(settings.data_dir), closing(sqlite3.connect(
        settings.database_path.as_uri() + '?mode=rw', uri=True, timeout=0, isolation_level=None,
    )) as db:
        try:
            db.execute('PRAGMA locking_mode=EXCLUSIVE')
            db.execute('BEGIN EXCLUSIVE')
            db.execute('COMMIT')
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA synchronous=FULL')
            _idle(db, book_id, global_running=True)
            plan = (_restore_plan(db, settings, book_id, restore_metadata) if restore_metadata
                    else _plan(db, settings, book_id))
            if not plan['changed']:
                return {**plan, 'preview': False, 'business_data_unchanged': True}
            _check(db)
            size = settings.database_path.stat().st_size
            if shutil.disk_usage(settings.data_dir).free < size * 2 + 16 * 1024**2:
                raise RuntimeError('空间不足，请为备份与事务预留数据库两倍大小的空间')
            columns = _business_columns(db)
            before = _digest(db, columns, plan)
            _checkpoint(db)
            backup = _backup(settings, purpose='toc-repair')
            metadata = backup.with_suffix('.metadata.json')
            metadata.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
            engine = create_engine('sqlite://', creator=lambda: db, poolclass=StaticPool)
            with engine.connect() as connection:
                connection.exec_driver_sql('BEGIN EXCLUSIVE')
                run_migrations(settings, connection=connection)
                now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=' ')
                db.execute('UPDATE book_versions SET toc_json=?,updated_at=? WHERE id=?',
                           (plan['after_toc'], now, plan['version_id']))
                for change in plan['title_changes']:
                    db.execute('UPDATE chapters SET title=?,updated_at=? WHERE id=?',
                               (change['after'], now, change['id']))
                db.execute('UPDATE books SET version=version+1,updated_at=? WHERE id=?',
                           (now, book_id))
                _check(db, full=True)
                if _digest(db, columns, plan) != before:
                    raise RuntimeError('目录修复改变了其他业务数据，事务已回滚')
                connection.commit()
            return {**plan, 'preview': False, 'backup_path': str(backup),
                    'metadata_backup_path': str(metadata), 'business_data_unchanged': True}
        except Exception as exc:
            if db.in_transaction:
                db.rollback()
            suffix = f'；备份保留在 {backup}' if backup else '；未写入目录变更'
            raise RuntimeError(f'离线目录修复未完成：{exc}{suffix}') from exc
        finally:
            if engine is not None:
                engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description='离线检查并补齐 EPUB 多层目录，不重新导入')
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--book-id', required=True)
    parser.add_argument('--apply', action='store_true', help='备份后执行；默认仅只读预览')
    parser.add_argument('--restore-metadata', type=Path, help='使用本书库的元数据备份回退目录')
    args = parser.parse_args()
    settings = Settings(_env_file=None, data_dir=args.data_dir.resolve())
    try:
        operation = repair_toc if args.apply else inspect_toc
        result = operation(settings, args.book_id, restore_metadata=args.restore_metadata)
        print(json.dumps({key: value for key, value in result.items()
                          if key not in {'before_toc', 'after_toc'}}, ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
