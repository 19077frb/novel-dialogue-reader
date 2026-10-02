"""冻结投影。

导出必须使用**一个**冻结快照：生成期间发生人工更正也不能改变正在导出的文件
。实现方式是把投影、身份投影、可见性策略与样式一次性写进
`export_snapshots`，之后渲染只读这份快照，不再查询实时标注表。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.enums import ExportStylePreset, ReadingMode, VisibilityPolicy
from ..scenes.projection import build_projection
from ..storage.cache import fingerprint
from ..storage.models import (
    Annotation,
    Book,
    BookVersion,
    Chapter,
    ExportSnapshot,
    IdentityRevision,
    Scene,
)

EXPORT_SNAPSHOT_VERSION = "export-snapshot-2"


@dataclass(frozen=True)
class SnapshotRange:
    start_cp: int
    end_cp: int
    chapter_ids: list[str]


def resolve_range(
    session: Session, version: BookVersion, chapter_ids: list[str] | None
) -> SnapshotRange:
    rows = list(
        session.execute(
            select(Chapter)
            .where(Chapter.book_version_id == version.id)
            .order_by(Chapter.ordinal)
        ).scalars()
    )
    if not chapter_ids:
        # 空列表表示「整本」：用于区分节选与全书（文件名与样张都要用到这个区别）
        return SnapshotRange(start_cp=0, end_cp=version.canonical_length_cp, chapter_ids=[])
    selected = [row for row in rows if row.id in set(chapter_ids)]
    if not selected:
        raise ValueError("选中的章节不属于该书籍版本")
    return SnapshotRange(
        start_cp=min(row.start_cp for row in selected),
        end_cp=max(row.end_cp for row in selected),
        chapter_ids=[row.id for row in selected],
    )


def freeze_snapshot(  # noqa: PLR0913 - 快照需要记录全部导出参数
    session: Session,
    *,
    book: Book,
    version: BookVersion,
    chapter_ids: list[str] | None,
    visibility_policy: VisibilityPolicy,
    style: ExportStylePreset,
) -> ExportSnapshot:
    """冻结一次投影；只读实时数据，写入一条不可变快照行。"""

    resolved = resolve_range(session, version, chapter_ids)
    reading_mode = (
        ReadingMode.REREAD
        if visibility_policy is VisibilityPolicy.REREAD
        else ReadingMode.INITIAL
    )
    horizon = None
    if visibility_policy is VisibilityPolicy.POSITION_SAFE:
        horizon = min(max(book.read_position_cp, 0), version.canonical_length_cp) or None
    projection = build_projection(
        session,
        book_id=book.id,
        book_version_id=version.id,
        start_cp=resolved.start_cp,
        end_cp=max(resolved.end_cp, resolved.start_cp + 1),
        reading_mode=reading_mode,
        visible_horizon_cp=horizon,
    )
    counts = projection.counts
    warnings = build_warnings(counts)
    annotation_versions = {
        row.quote_id: row.version
        for row in session.execute(
            select(Annotation).where(
                Annotation.quote_id.in_([item.quote_id for item in projection.items] or [""])
            )
        ).scalars()
    }
    revisions = [
        {
            "id": row.id,
            "operation": row.operation.value,
            "visible_from_cp": row.visible_from_cp,
            "scene_id": row.scene_id,
        }
        for row in session.execute(
            select(IdentityRevision)
            .join(Scene, IdentityRevision.scene_id == Scene.id)
            .where(Scene.book_version_id == version.id)
            .order_by(IdentityRevision.created_at)
        ).scalars()
    ]
    payload = projection.model_dump(mode="json")
    payload["warnings"] = warnings
    payload["snapshot_version"] = EXPORT_SNAPSHOT_VERSION
    payload["chapter_processing"] = {
        chapter.id: {"processed": chapter.dialogue_processed,
                     "override": chapter.processing_status_override}
        for chapter in session.scalars(select(Chapter).where(
            Chapter.book_version_id == version.id,
            *([Chapter.id.in_(resolved.chapter_ids)] if resolved.chapter_ids else []),
        ))
    }
    source_revision = fingerprint(
        {
            "snapshot_version": EXPORT_SNAPSHOT_VERSION,
            "book_version_id": version.id,
            "canonical_sha256": version.canonical_sha256,
            "annotation_versions": annotation_versions,
            "counts": counts.model_dump(mode="json"),
        }
    )
    snapshot_hash = fingerprint(
        {
            "source_revision": source_revision,
            "projection": payload,
            "identity": revisions,
            "visibility_policy": visibility_policy.value,
            "style": style.value,
            "chapter_ids": resolved.chapter_ids,
        }
    )
    row = ExportSnapshot(
        book_id=book.id,
        book_version_id=version.id,
        selected_chapter_ids_json=_json(resolved.chapter_ids),
        source_revision=source_revision,
        annotation_projection_json=_json(payload),
        identity_projection_json=_json(
            {"revisions": revisions, "reverts": projection.identity_reverts}
        ),
        visibility_policy=visibility_policy.value,
        style_json=_json({"preset": style.value}),
        snapshot_hash=snapshot_hash,
        warnings_json=_json(warnings),
    )
    session.add(row)
    session.flush()
    return row


def build_warnings(counts) -> list[str]:  # noqa: ANN001 - AnnotationCountsOut
    warnings: list[str] = []
    if counts.unprocessed_quotes:
        warnings.append(f"{counts.unprocessed_quotes} 条候选对白还没有标注，导出时保持原样。")
    if counts.unknown:
        warnings.append(f"{counts.unknown} 条对白被判定为未知，导出时不着色也不编号。")
    if counts.provisional:
        warnings.append(f"{counts.provisional} 条对白仍是暂定结果，请确认后再导出。")
    if counts.stale:
        warnings.append(f"{counts.stale} 条对白已过期（stale），导出时保持原样。")
    if counts.withheld:
        warnings.append(
            f"{counts.withheld} 条对白的证据位于当前阅读位置之后，"
        "初读导出不显示颜色与编号。"
        )
    return warnings


def _json(value) -> str:  # noqa: ANN001
    import json

    return json.dumps(value, ensure_ascii=False)
