"""导出服务：冻结快照 → 渲染 → 落盘 → 校验。

- 只读已存数据与本地资源；本模块**不导入任何 LLM 适配器**（导出零模型调用）。
- 相同 `snapshot_hash + 格式 + 样式` 的重复请求复用已有产物。
- 文件永远写到 `data/exports/<artifact_id>/`，**从不覆盖原书**。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..api.errors import ApiError
from ..domain.enums import ExportFormat, ExportStylePreset, JobKind, JobState
from ..ingest.query import load_canonical_text, processed_chapter_ids
from ..ingest.resources import read_resource_bytes
from ..storage.cache import fingerprint
from ..storage.models import (
    Book,
    BookVersion,
    Chapter,
    ExportArtifact,
    ExportSnapshot,
    Job,
    Resource,
)
from ..storage.paths import resolve_within, to_relative
from .annotations import build_annotations_manifest
from .epub import build_epub
from .html import render_html
from .render import EXPORTER_VERSION, RenderedBook, export_filename, render_book
from .validation import validate_export


@dataclass
class ExportOutcome:
    artifact_id: str
    state: str
    format: str
    filename: str | None = None
    byte_size: int | None = None
    file_sha256: str | None = None
    validation: dict | None = None
    reused: bool = False


def artifact_fingerprint(snapshot_hash: str, fmt: ExportFormat, style: ExportStylePreset) -> str:
    return fingerprint(
        {
            "snapshot_hash": snapshot_hash,
            "format": fmt.value,
            "style": style.value,
            "exporter_version": EXPORTER_VERSION,
        }
    )


def export_dir(settings, artifact_id: str) -> Path:  # noqa: ANN001 - Settings
    return resolve_within(settings, Path("exports") / artifact_id)


def create_artifact(
    session: Session,
    *,
    snapshot: ExportSnapshot,
    fmt: ExportFormat,
    style: ExportStylePreset,
) -> tuple[ExportArtifact, bool]:
    """幂等创建：相同 fingerprint 且已完成的产物直接复用。"""

    digest = artifact_fingerprint(snapshot.snapshot_hash, fmt, style)
    existing = session.execute(
        select(ExportArtifact).where(
            ExportArtifact.fingerprint == digest,
            ExportArtifact.state == JobState.COMPLETED.value,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False
    job = Job(
        kind=JobKind.EXPORT,
        purpose=None,
        book_id=snapshot.book_id,
        book_version_id=snapshot.book_version_id,
        state=JobState.QUEUED,
        range_json=json.dumps({"snapshot_id": snapshot.id}, ensure_ascii=False),
        progress_json=json.dumps({"stage": "queued"}, ensure_ascii=False),
    )
    session.add(job)
    session.flush()
    artifact = ExportArtifact(
        snapshot_id=snapshot.id,
        format=fmt,
        options_json=json.dumps({"style": style.value}, ensure_ascii=False),
        exporter_version=EXPORTER_VERSION,
        fingerprint=digest,
        job_id=job.id,
        state=JobState.QUEUED.value,
        validation_json="{}",
    )
    session.add(artifact)
    session.flush()
    return artifact, True


def load_resource_bytes(
    session: Session,
    settings,  # noqa: ANN001 - Settings
    *,
    book: Book,
    version: BookVersion,
    resource_ids: list[str],
) -> dict[str, bytes]:
    """只读**已登记**的书籍资源（EPUB 从源包读取、TXT 没有包内资源）。

    路径/条目解析复用导入侧的 `read_resource_bytes`，越界与缺失都会抛出可解释错误。
    """

    if not resource_ids:
        return {}
    rows = list(
        session.execute(
            select(Resource).where(
                Resource.book_version_id == version.id, Resource.resource_id.in_(resource_ids)
            )
        ).scalars()
    )
    payload: dict[str, bytes] = {}
    for row in rows:
        try:
            payload[row.resource_id] = read_resource_bytes(settings, book, version, row)
        except ApiError:
            # 资源缺失不阻断导出：正文用占位符，校验会把缺失如实标出来
            continue
    return payload


def run_export(  # noqa: PLR0913 - 需要 settings / 会话工厂 / 产物与可选工具
    session_factory: sessionmaker[Session],
    settings,  # noqa: ANN001 - Settings
    *,
    artifact_id: str,
    epubcheck_jar: str | Path | None = None,
) -> ExportOutcome:
    """执行一次导出：渲染（读事务）→ 写文件 + 校验（无事务）→ 落状态（写事务）。"""

    with session_factory() as session:
        artifact = session.get(ExportArtifact, artifact_id)
        if artifact is None:
            return ExportOutcome(
                artifact_id=artifact_id,
                state="FAILED",
                format="",
                validation={"detail": "产物不存在"},
            )
        snapshot = session.get(ExportSnapshot, artifact.snapshot_id)
        assert snapshot is not None
        book = session.get(Book, snapshot.book_id)
        version = session.get(BookVersion, snapshot.book_version_id)
        assert book is not None and version is not None
        style = ExportStylePreset(
            json.loads(snapshot.style_json or "{}").get(
                "preset", ExportStylePreset.COLOR_AND_LABEL.value
            )
        )
        payload = json.loads(snapshot.annotation_projection_json or "{}")
        chapter_ids = json.loads(snapshot.selected_chapter_ids_json or "[]")
        artifact.state = JobState.RUNNING.value
        session.commit()
        rendered = render_book(
            session,
            settings,
            book=book,
            version=version,
            projection_payload=payload,
            style=style,
            chapter_ids=chapter_ids or None,
        )
        images = load_resource_bytes(
            session,
            settings,
            book=book,
            version=version,
            resource_ids=rendered.referenced_resource_ids(),
        )
        book_title = book.title
        selected = bool(chapter_ids)
        fmt = artifact.format
        annotations_manifest = None
        if fmt is ExportFormat.EPUB:
            frozen_processing = payload.get("chapter_processing")
            if isinstance(frozen_processing, dict):
                processed_ids = {key for key, state in frozen_processing.items()
                                 if state.get("processed")}
                manual_status = {key: state["override"] for key, state in frozen_processing.items()
                                 if state.get("override") is not None}
            else:
                # Older saved previews did not freeze completion flags.
                processed_ids = processed_chapter_ids(session, version.id)
                manual_status = dict(session.execute(select(
                    Chapter.id, Chapter.processing_status_override,
                ).where(Chapter.book_version_id == version.id,
                        Chapter.processing_status_override.is_not(None))).all())
            annotations_manifest = build_annotations_manifest(
                projection_payload=payload,
                rendered=rendered,
                canonical_text=load_canonical_text(settings, version),
                style=style,
                processed_chapter_indices=[
                    index
                    for index, chapter in enumerate(rendered.chapters)
                    if chapter.chapter_id in processed_ids
                ],
                manual_processing_status={index: manual_status[chapter.chapter_id]
                                          for index, chapter in enumerate(rendered.chapters)
                                          if chapter.chapter_id in manual_status},
            )

    out_dir = export_dir(settings, artifact_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    suffix = ".epub" if fmt is ExportFormat.EPUB else ".html"
    filename = export_filename(rendered, suffix=suffix, selected=selected)
    path = out_dir / filename
    try:
        if fmt is ExportFormat.EPUB:
            data = build_epub(
                rendered,
                images=images,
                identifier=f"urn:ndr:{artifact_id}",
                generated_at=generated_at,
                annotations=annotations_manifest,
            )
        else:
            document = render_html(rendered, images=images, generated_at=generated_at)
            data = document.encode("utf-8")
        path.write_bytes(data)
        validation = validate_export(
            path,
            fmt=fmt.value,
            expected_fragments=_expected_fragments(rendered),
            epubcheck_jar=epubcheck_jar,
        )
        ok = bool(validation.get("ok"))
        outcome = ExportOutcome(
            artifact_id=artifact_id,
            state=JobState.COMPLETED.value if ok else JobState.FAILED.value,
            format=fmt.value,
            filename=filename,
            byte_size=len(data),
            file_sha256=hashlib.sha256(data).hexdigest(),
            validation=validation,
        )
    except Exception as exc:  # noqa: BLE001 - 导出失败必须落在产物状态里
        outcome = ExportOutcome(
            artifact_id=artifact_id,
            state=JobState.FAILED.value,
            format=fmt.value,
            validation={"ok": False, "detail": f"{type(exc).__name__}: {exc}"},
        )

    with session_factory() as session:
        artifact = session.get(ExportArtifact, artifact_id)
        if artifact is not None:
            artifact.state = outcome.state
            artifact.relative_path = to_relative(settings, path) if outcome.filename else None
            artifact.file_sha256 = outcome.file_sha256
            artifact.byte_size = outcome.byte_size
            artifact.validation_json = json.dumps(outcome.validation or {}, ensure_ascii=False)
            job = session.get(Job, artifact.job_id) if artifact.job_id else None
            if job is not None:
                job.state = JobState(outcome.state)
                job.last_error = None if outcome.state == JobState.COMPLETED.value else str(
                    (outcome.validation or {}).get("detail") or "导出未通过校验"
                )
                job.progress_json = json.dumps(
                    {"stage": "completed" if outcome.state == "COMPLETED" else "failed"},
                    ensure_ascii=False,
                )
        session.commit()
    _ = book_title
    return outcome


def _expected_fragments(rendered: RenderedBook) -> list[str]:
    """校验用的正文片段：章节标题 + 每个有文字的块。"""

    parts: list[str] = []
    for chapter in rendered.chapters:
        parts.append(chapter.title)
        parts.extend(block.plain_text for block in chapter.blocks if block.plain_text)
    return parts


def render_sample(
    session,  # noqa: ANN001 - Session
    settings,  # noqa: ANN001 - Settings
    *,
    book: Book,
    version: BookVersion,
    projection_payload: dict,
    style: ExportStylePreset,
    max_chapters: int = 1,
    max_blocks: int = 12,
) -> tuple[str, list[str], dict]:
    """用同一个渲染器产出样张：前若干章的前若干段（隔离容器里展示，不执行脚本）。"""

    rendered = render_book(
        session,
        settings,
        book=book,
        version=version,
        projection_payload=projection_payload,
        style=style,
        chapter_ids=None,
    )
    trimmed = RenderedBook(
        book_id=rendered.book_id,
        title=rendered.title,
        language=rendered.language,
        style=rendered.style,
        chapters=[
            chapter.__class__(
                chapter_id=chapter.chapter_id,
                ordinal=chapter.ordinal,
                title=chapter.title,
                blocks=chapter.blocks[:max_blocks],
            )
            for chapter in rendered.chapters[:max_chapters]
        ],
        legend=rendered.legend,
        warnings=rendered.warnings,
        visible_horizon_cp=rendered.visible_horizon_cp,
    )
    fragments = [
        block.plain_text
        for chapter in trimmed.chapters
        for block in chapter.blocks
        if block.plain_text
    ]
    document = render_html(trimmed, images={}, generated_at="sample")
    return document, fragments, coverage_of(rendered)


def coverage_of(rendered: RenderedBook) -> dict:
    paragraphs = annotated = 0
    for chapter in rendered.chapters:
        for block in chapter.blocks:
            if not block.plain_text:
                continue
            paragraphs += 1
            if any(run.quote_id for run in block.runs):
                annotated += 1
    return {
        "paragraphs": paragraphs,
        "paragraphs_with_annotations": annotated,
        "legend_entries": len(rendered.legend),
    }
