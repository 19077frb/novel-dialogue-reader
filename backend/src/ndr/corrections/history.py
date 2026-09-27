"""标注快照与历史（T12）。

`annotation_history` 只追加：每次人工更正与撤销都先写一份旧快照，再改当前投影。
撤销不是硬删除，而是「恢复旧快照 + 再写一条历史」。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.enums import (
    AnnotationSource,
    AnnotationStatus,
    Assignment,
    QuoteKind,
    SpeakerBasis,
)
from ..storage.models import Annotation, AnnotationHistory


def annotation_snapshot(annotation: Annotation) -> dict[str, Any]:
    """当前投影的完整快照；`corrections.before_json/after_json` 与历史共用同一形状。"""

    try:
        evidence = json.loads(annotation.evidence_refs_json or "[]")
    except json.JSONDecodeError:
        evidence = []
    return {
        "quote_id": annotation.quote_id,
        "scene_id": annotation.scene_id,
        "kind": annotation.kind.value,
        "assignment": annotation.assignment.value if annotation.assignment else None,
        "basis": annotation.basis.value if annotation.basis else None,
        "speaker_id": annotation.speaker_id,
        "status": annotation.status.value,
        "source": annotation.source.value,
        "evidence_refs": list(evidence),
        "visible_from_cp": annotation.visible_from_cp,
        "dependency_hash": annotation.dependency_hash,
        "stale": annotation.stale,
        "user_locked": annotation.user_locked,
        "version": annotation.version,
    }


def apply_snapshot(annotation: Annotation, snapshot: Mapping[str, Any]) -> None:
    """把快照写回标注（不递增 version，由调用方决定；不 flush）。"""

    annotation.scene_id = snapshot.get("scene_id")
    annotation.kind = QuoteKind(snapshot["kind"])
    assignment = snapshot.get("assignment")
    annotation.assignment = Assignment(assignment) if assignment else None
    basis = snapshot.get("basis")
    annotation.basis = SpeakerBasis(basis) if basis else None
    annotation.speaker_id = snapshot.get("speaker_id")
    annotation.status = AnnotationStatus(snapshot["status"])
    annotation.source = AnnotationSource(snapshot["source"])
    annotation.evidence_refs_json = json.dumps(
        list(snapshot.get("evidence_refs") or []), ensure_ascii=False
    )
    annotation.visible_from_cp = snapshot.get("visible_from_cp")
    annotation.dependency_hash = snapshot.get("dependency_hash")
    annotation.stale = bool(snapshot.get("stale", False))
    annotation.user_locked = bool(snapshot.get("user_locked", False))


def write_annotation_history(
    session: Session,
    annotation: Annotation,
    *,
    correction_id: str | None = None,
    run_id: str | None = None,
) -> AnnotationHistory | None:
    """在改动当前投影之前保存旧快照；同一 revision 只写一次。"""

    existing = session.execute(
        select(AnnotationHistory).where(
            AnnotationHistory.annotation_id == annotation.id,
            AnnotationHistory.revision == annotation.version,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = AnnotationHistory(
        annotation_id=annotation.id,
        revision=annotation.version,
        snapshot_json=json.dumps(annotation_snapshot(annotation), ensure_ascii=False),
        visible_from_cp=annotation.visible_from_cp,
        run_id=run_id,
        correction_id=correction_id,
    )
    session.add(row)
    session.flush()
    return row
