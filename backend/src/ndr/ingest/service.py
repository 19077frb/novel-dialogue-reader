"""导入写入路径。

一次导入在一个事务里完成：书籍 → 版本 → 章节 → 节点 → source_map → 资源 → IMPORT 任务。
TXT 与 EPUB 共用同一套持久化：区别只在解析器与是否登记资源。

原始文件与 canonical 全文写在数据目录内（相对路径入库，避免暴露任意磁盘路径），
写文件在事务提交前完成，事务失败时删除刚写入的文件。

复用规则（门槛：重复导入不重复建书、换编码/换解析器生成新版本）：

- 同一份原始字节（``source_sha256`` 相同）复用同一本书；
- 同一本书内 ``(canonical_sha256, parser_version, normalization_version)`` 相同则复用版本；
- 换了编码或解析器版本 → 新版本，并把 ``active_version_id`` 指向它。

EPUB 的 ``book_versions.encoding`` 记为 ``"xml"``：正文编码由各 XHTML 文档自身的 XML 声明决定，
不是单一文件级编码，因此不伪造一个具体编码名。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..domain.enums import BookFormat, ImportStatus, JobKind, JobState
from ..quotes.scanner import SCANNER_VERSION, ScanLimits
from ..quotes.service import scan_and_store
from ..storage.models import (
    Book,
    BookVersion,
    Chapter,
    ContentNode,
    Job,
    Resource,
    TextMapping,
)
from ..storage.paths import book_source_path, to_relative, version_canonical_path
from .document import ParsedBook
from .encoding import DecodeFailure
from .epub import EpubError, EpubLimits, parse_epub
from .txt import parse_txt

DEFAULT_TITLE_FALLBACK = "未命名"
XML_ENCODING_SENTINEL = "xml"


@dataclass
class ImportOutcome:
    book: Book
    version: BookVersion
    job: Job
    parsed: ParsedBook
    reused_book: bool
    reused_version: bool


def record_failed_import(
    session: Session,
    *,
    message: str,
    filename: str | None,
    requested_encoding: str | None = None,
    stage: str = "decode",
) -> Job:
    """解析失败也留痕：失败任务没有 book_id，但可以按 job_id 查询。"""

    job = Job(
        kind=JobKind.IMPORT,
        purpose=None,
        state=JobState.FAILED,
        last_error=message[:1000],
        progress_json=json.dumps(
            {
                "stage": stage,
                "filename": filename,
                "requested_encoding": requested_encoding,
            },
            ensure_ascii=False,
        ),
        checkpoint_json=None,
        range_json="{}",
    )
    session.add(job)
    session.flush()
    return job


def import_txt(
    session: Session,
    settings: Settings,
    *,
    filename: str,
    raw: bytes,
    encoding: str | None = None,
    title: str | None = None,
) -> ImportOutcome:
    """解析并落库 TXT；解析失败时抛出 :class:`DecodeFailure`（调用方转成 422）。"""

    parsed = parse_txt(raw, encoding=encoding, title=title)
    return persist_parsed(session, settings, filename=filename, raw=raw, parsed=parsed)


def import_epub(
    session: Session,
    settings: Settings,
    *,
    filename: str,
    raw: bytes,
    title: str | None = None,
    limits: EpubLimits | None = None,
) -> ImportOutcome:
    """解析并落库 EPUB；结构/安全校验失败时抛出 :class:`EpubError`。"""

    parsed = parse_epub(raw, limits=limits, title=title)
    return persist_parsed(session, settings, filename=filename, raw=raw, parsed=parsed)


def persist_parsed(
    session: Session,
    settings: Settings,
    *,
    filename: str,
    raw: bytes,
    parsed: ParsedBook,
) -> ImportOutcome:
    resolved_title = (parsed.metadata.get("title") or Path(filename).stem).strip()
    if not resolved_title:
        resolved_title = DEFAULT_TITLE_FALLBACK

    book = session.execute(
        select(Book).where(Book.source_sha256 == parsed.source_sha256)
    ).scalar_one_or_none()
    reused_book = book is not None
    if book is None:
        book = Book(
            title=resolved_title,
            format=BookFormat(parsed.format),
            source_sha256=parsed.source_sha256,
            import_status=ImportStatus.RUNNING,
        )
        session.add(book)
        session.flush()

    version = session.execute(
        select(BookVersion).where(
            BookVersion.book_id == book.id,
            BookVersion.canonical_sha256 == parsed.canonical_sha256,
            BookVersion.parser_version == parsed.parser_version,
            BookVersion.normalization_version == parsed.normalization_version,
        )
    ).scalar_one_or_none()
    reused_version = version is not None

    written: list[Path] = []
    try:
        source_path = book_source_path(settings, book.id, _suffix_for(filename, parsed.format))
        if not source_path.exists():
            _write_bytes(source_path, raw)
            written.append(source_path)

        if version is None:
            version = BookVersion(
                book_id=book.id,
                source_path=to_relative(settings, source_path),
                encoding=parsed.encoding or XML_ENCODING_SENTINEL,
                parser_version=parsed.parser_version,
                normalization_version=parsed.normalization_version,
                canonical_sha256=parsed.canonical_sha256,
                canonical_length_cp=parsed.canonical_length_cp,
                warnings_json=json.dumps(list(parsed.warnings), ensure_ascii=False),
            )
            session.add(version)
            session.flush()

            canonical_path = version_canonical_path(settings, book.id, version.id)
            _write_text(canonical_path, parsed.canonical_text)
            written.append(canonical_path)
            version.canonical_path = to_relative(settings, canonical_path)

            chapter_ids = _insert_chapters(session, version, parsed)
            _insert_nodes(session, parsed, chapter_ids)
            _insert_mappings(session, version, parsed, chapter_ids)
            _insert_resources(session, version, parsed)
            # 候选引语/Gap 是派生数据：导入后立即扫描，便于界面显示候选覆盖（不涉及任何模型调用）。
            scan_and_store(
                session,
                settings,
                version,
                canonical_text=parsed.canonical_text,
                limits=ScanLimits(),
                scanner_version=SCANNER_VERSION,
            )

        book.active_version_id = version.id
        book.import_status = ImportStatus.COMPLETED

        job = Job(
            kind=JobKind.IMPORT,
            purpose=None,
            book_id=book.id,
            book_version_id=version.id,
            state=JobState.COMPLETED,
            range_json="{}",
            progress_json=json.dumps(
                {
                    "stage": "completed",
                    "reused_book": reused_book,
                    "reused_version": reused_version,
                    "chapters": len(parsed.chapters),
                    "nodes": len(parsed.nodes),
                    "resources": len(parsed.resources),
                    "canonical_length_cp": parsed.canonical_length_cp,
                },
                ensure_ascii=False,
            ),
            checkpoint_json=json.dumps(
                {"canonical_sha256": parsed.canonical_sha256}, ensure_ascii=False
            ),
        )
        session.add(job)
        session.flush()
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise

    return ImportOutcome(
        book=book,
        version=version,
        job=job,
        parsed=parsed,
        reused_book=reused_book,
        reused_version=reused_version,
    )


def _suffix_for(filename: str, book_format: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix:
        return suffix
    return ".epub" if book_format == "EPUB" else ".txt"


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(payload)
    temp.replace(path)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    temp.replace(path)


def _insert_chapters(session: Session, version: BookVersion, parsed: ParsedBook) -> list[str]:
    chapter_ids: list[str] = []
    for chapter in parsed.chapters:
        row = Chapter(
            book_version_id=version.id,
            ordinal=chapter.ordinal,
            title=chapter.title,
            start_cp=chapter.start_cp,
            end_cp=chapter.end_cp,
            source_href=chapter.source_href,
        )
        session.add(row)
        session.flush()
        chapter_ids.append(row.id)
    return chapter_ids


def _insert_nodes(session: Session, parsed: ParsedBook, chapter_ids: list[str]) -> None:
    for node in parsed.nodes:
        session.add(
            ContentNode(
                chapter_id=chapter_ids[node.chapter_ordinal],
                node_id=node.node_id,
                node_type=node.node_type,
                ordinal=node.ordinal,
                tree_json=node.tree_json,
                start_cp=node.start_cp,
                end_cp=node.end_cp,
            )
        )


def _insert_mappings(
    session: Session,
    version: BookVersion,
    parsed: ParsedBook,
    chapter_ids: list[str],
) -> None:
    for mapping in parsed.mappings:
        session.add(
            TextMapping(
                book_version_id=version.id,
                chapter_id=(
                    chapter_ids[mapping.chapter_ordinal]
                    if mapping.chapter_ordinal is not None
                    else None
                ),
                node_id=mapping.node_id,
                ordinal=mapping.ordinal,
                canonical_start_cp=mapping.canonical_start_cp,
                canonical_end_cp=mapping.canonical_end_cp,
                source_href=mapping.source_href,
                source_text_start_cp=mapping.source_text_start_cp,
                source_text_end_cp=mapping.source_text_end_cp,
                synthetic=mapping.synthetic,
            )
        )


def _insert_resources(session: Session, version: BookVersion, parsed: ParsedBook) -> None:
    for resource in parsed.resources:
        session.add(
            Resource(
                book_version_id=version.id,
                resource_id=resource.resource_id,
                media_type=resource.media_type,
                relative_path=resource.relative_path,
                sha256=resource.sha256,
            )
        )


__all__ = [
    "DEFAULT_TITLE_FALLBACK",
    "XML_ENCODING_SENTINEL",
    "DecodeFailure",
    "EpubError",
    "ImportOutcome",
    "import_epub",
    "import_txt",
    "persist_parsed",
    "record_failed_import",
]
