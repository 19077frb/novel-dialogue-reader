"""Lossless shared presentation baselines and group-local appended revisions."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import object_session
from sqlalchemy.orm.attributes import flag_modified

from .base import new_id
from .models import PresentationHistoryBaseline, Scene, SpeakerGroup


def materialized_history_json(group: SpeakerGroup) -> str:
    local = group._presentation_history_json or "[]"
    baseline = group.presentation_baseline
    if baseline is None:
        return local
    if local == "[]":
        return baseline.history_json
    return json.dumps(json.loads(baseline.history_json) + json.loads(local), ensure_ascii=False)


def append_history(row, value: dict) -> None:
    if isinstance(row, SpeakerGroup):
        rows = json.loads(row._presentation_history_json or "[]")
        rows.append(value)
        row._presentation_history_json = json.dumps(rows, ensure_ascii=False)
    else:
        rows = json.loads(row.presentation_history_json or "[]")
        rows.append(value)
        row.presentation_history_json = json.dumps(rows, ensure_ascii=False)


def share_history(group: SpeakerGroup, rows: list, *, book_version_id: str | None = None) -> None:
    """Freeze a complete history; later identity edits never mutate this content."""
    session = object_session(group)
    if session is None:
        group.presentation_history_json = json.dumps(rows, ensure_ascii=False)
        return
    if book_version_id is None:
        book_version_id = session.scalar(select(Scene.book_version_id).where(
            Scene.id == group.scene_id,
        ))
    if book_version_id is None:
        raise ValueError("分组缺少所属正文版本")
    raw = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    # SQLite serializes writers. Conflict handling also supports another process
    # freezing the same content without rolling back the caller's transaction.
    session.execute(insert(PresentationHistoryBaseline).values(
        id=new_id(), book_version_id=book_version_id, digest=digest, history_json=raw,
    ).on_conflict_do_nothing(index_elements=["book_version_id", "digest"]))
    baseline = session.scalar(select(PresentationHistoryBaseline).where(
        PresentationHistoryBaseline.book_version_id == book_version_id,
        PresentationHistoryBaseline.digest == digest,
    ))
    if baseline.history_json != raw:
        raise ValueError("共享展示历史摘要冲突")
    group.presentation_baseline = baseline
    group.presentation_baseline_id = baseline.id
    group._presentation_history_json = "[]"


def deduplicate_group_histories(session) -> int:
    """Explicit offline rewrite preserving every complete presentation timeline."""
    changed = 0
    groups = session.execute(select(SpeakerGroup, Scene.book_version_id).join(
        Scene, SpeakerGroup.scene_id == Scene.id,
    ))
    for group, version_id in groups:
        if group.presentation_baseline_id and group._presentation_history_json == "[]":
            continue
        try:
            rows = json.loads(group.presentation_history_json or "[]")
        except (ValueError, TypeError):
            continue
        if not isinstance(rows, list) or not rows:
            continue
        share_history(group, rows, book_version_id=version_id)
        # Storage-only maintenance preserves the original business timestamp.
        flag_modified(group, "updated_at")
        changed += 1
    session.flush()
    return changed
