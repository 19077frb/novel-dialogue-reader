"""从标注清单恢复标注（回导本工具导出的 EPUB 时）。

匹配规则：

- 章节：优先「清单章节标题 + 最接近的下标」对齐，其次按下标；
- 对白：在对应章节内，按清单顺序向前扫描**顶层候选引语**，找到第一条
  归一化后原文一致的对白；同一句重复出现时按出现次序一一对应；
- 说话人：按色号键（``c0``、``c1``…）恢复分组；每章一个场景，同色号在场景内共享分组，
  分组带名字时跨场景共享颜色（与导出前的投影规则一致）。

恢复出的标注 ``user_locked=True``、``visible_from_cp=0``：它们是已确认的结果，
后续自动处理不得覆盖，也不会被初读 horizon 遮断。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.enums import (
    AnnotationSource,
    AnnotationStatus,
    Assignment,
    CharacterSource,
    QuoteKind,
    SceneStatus,
    SpeakerBasis,
)
from ..exports.annotations import normalize_for_match
from ..storage.models import Annotation, BookCharacter, Chapter, Quote, Scene, SpeakerGroup

_MAX_GROUP_LABEL_LEN = 32
_MAX_GROUP_NAME_LEN = 128
_MAX_GROUP_DESCRIPTION_LEN = 512


def _enum_or_none(value: Any, enum_cls):  # noqa: ANN001 - 小工具函数
    if value is None:
        return None
    try:
        return enum_cls(value)
    except ValueError:
        return None


def restore_annotations_from_manifest(
    session: Session,
    version,  # noqa: ANN001 - BookVersion
    manifest: dict[str, Any],
    *,
    canonical_text: str,
) -> dict[str, int]:
    """把清单恢复成本版本的标注数据；返回统计（进度展示用）。

    任何一条匹配失败都只计数、不中断：恢复尽力而为，统计如实上报。
    """

    speakers = {
        str(row.get("key")): row
        for row in manifest.get("speakers", []) or []
        if isinstance(row, dict) and row.get("key")
    }
    entries = [row for row in manifest.get("annotations", []) or [] if isinstance(row, dict)]
    chapters = list(
        session.execute(
            select(Chapter).where(Chapter.book_version_id == version.id).order_by(Chapter.ordinal)
        ).scalars()
    )
    binding = _bind_chapters(chapters, manifest.get("chapter_titles", []) or [])
    processed_count = 0
    for raw_index in manifest.get("processed_chapter_indices", []) or []:
        if not isinstance(raw_index, int):
            continue
        chapter = binding.get(raw_index)
        if chapter is not None and not chapter.dialogue_processed:
            chapter.dialogue_processed = True
            processed_count += 1
    manual_status = manifest.get("manual_processing_status", {})
    if isinstance(manual_status, dict):
        for raw_index, status in manual_status.items():
            if not isinstance(status, bool):
                continue
            try:
                chapter = binding.get(int(raw_index))
            except (TypeError, ValueError):
                continue
            if chapter is not None:
                chapter.processing_status_override = status
                chapter.dialogue_processed = status
    if not entries:
        return {
            "restored": 0,
            "unmatched": 0,
            "speakers": len(speakers),
            "processed_chapters": processed_count,
        }

    quotes_by_chapter: dict[str, list[tuple[Quote, str]]] = {}
    top_level = list(
        session.execute(
            select(Quote)
            .where(Quote.book_version_id == version.id, Quote.nesting_depth == 0)
            .order_by(Quote.start_cp)
        ).scalars()
    )
    for quote in top_level:
        if quote.chapter_id:
            quotes_by_chapter.setdefault(quote.chapter_id, []).append(
                (quote, normalize_for_match(canonical_text[quote.start_cp : quote.end_cp]))
            )

    scenes: dict[str, Scene] = {}
    scene_bounds: dict[str, list[int]] = {}
    groups: dict[tuple[str, str], SpeakerGroup] = {}
    characters: dict[str, BookCharacter] = {}
    used_labels: dict[str, set[str]] = {}
    pointers: dict[str, int] = {}
    restored = 0
    unmatched = 0

    for entry in entries:
        chapter = binding.get(int(entry.get("chapter_index", -1)))
        if chapter is None:
            unmatched += 1
            continue
        quote_list = quotes_by_chapter.get(chapter.id, [])
        pointer = pointers.get(chapter.id, 0)
        quote = None
        expected = str(entry.get("quote_text") or "")
        match_pointer = pointer
        while match_pointer < len(quote_list):
            candidate, text = quote_list[match_pointer]
            match_pointer += 1
            if expected and text == expected:
                quote = candidate
                break
        if quote is None:
            unmatched += 1
            continue
        pointers[chapter.id] = match_pointer

        speaker = speakers.get(str(entry.get("speaker") or ""))
        group = None
        if speaker is not None:
            group = _group_for(
                session,
                scenes=scenes,
                groups=groups,
                characters=characters,
                used_labels=used_labels,
                version=version,
                chapter=chapter,
                quote=quote,
                speaker=speaker,
            )
        annotation = _annotation_for(
            entry, quote=quote, group=group, scene=scenes.get(chapter.id)
        )
        if annotation is None:
            unmatched += 1
            continue
        session.add(annotation)
        bounds = scene_bounds.setdefault(chapter.id, [quote.start_cp, quote.end_cp])
        bounds[0] = min(bounds[0], quote.start_cp)
        bounds[1] = max(bounds[1], quote.end_cp)
        restored += 1

    session.flush()
    for chapter_id, (start, end) in scene_bounds.items():
        scene = scenes[chapter_id]
        scene.start_cp = start
        scene.end_cp = end
    session.flush()
    return {
        "restored": restored,
        "unmatched": unmatched,
        "speakers": len(speakers),
        "processed_chapters": processed_count,
    }


def _bind_chapters(chapters: list, manifest_titles: list) -> dict[int, Any]:  # noqa: ANN001
    """把清单章节下标绑定到导入章节：标题 + 邻近下标优先，其次按下标。"""

    if not manifest_titles:
        return {chapter.ordinal: chapter for chapter in chapters}
    binding: dict[int, Any] = {}
    unused = {chapter.ordinal: chapter for chapter in chapters}
    for index, raw_title in enumerate(manifest_titles):
        title = str(raw_title or "").strip()
        chapter = None
        if title:
            candidates = [row for row in unused.values() if (row.title or "").strip() == title]
            if candidates:
                chapter = min(candidates, key=lambda row: abs(row.ordinal - index))
        if chapter is None:
            chapter = unused.get(index)
        if chapter is not None:
            unused.pop(chapter.ordinal, None)
            binding[index] = chapter
    return binding


def _scene_for(session: Session, scenes: dict, chapter) -> Scene:  # noqa: ANN001
    scene = scenes.get(chapter.id)
    if scene is None:
        scene = Scene(
            book_version_id=chapter.book_version_id,
            start_cp=chapter.start_cp,
            end_cp=chapter.end_cp,
            status=SceneStatus.OPEN,
        )
        session.add(scene)
        session.flush()
        scenes[chapter.id] = scene
    return scene


def _group_for(  # noqa: PLR0913 - 建组需要的上下文就是这些
    session: Session,
    *,
    scenes: dict,
    groups: dict,
    characters: dict,
    used_labels: dict,
    version,
    chapter,
    quote: Quote,
    speaker: dict,
) -> SpeakerGroup:
    scene = _scene_for(session, scenes, chapter)
    key = str(speaker.get("key"))
    group = groups.get((chapter.id, key))
    if group is not None:
        return group
    label = str(speaker.get("label") or "").strip() or "未确认说话人"
    display = label[:_MAX_GROUP_LABEL_LEN]
    seen = used_labels.setdefault(chapter.id, set())
    suffix = 2
    base = display
    while display in seen:
        display = f"{base[: _MAX_GROUP_LABEL_LEN - 3]}·{suffix}"
        suffix += 1
    seen.add(display)
    character = _character_for(
        session,
        characters=characters,
        version=version,
        speaker=speaker,
        first_seen_cp=quote.start_cp,
    )
    group = SpeakerGroup(
        scene_id=scene.id,
        first_quote_id=quote.id,
        display_label=display,
        canonical_name=label[:_MAX_GROUP_NAME_LEN],
        description=str(speaker.get("description") or "")[:_MAX_GROUP_DESCRIPTION_LEN] or None,
        character_id=character.id,
    )
    session.add(group)
    session.flush()
    groups[(chapter.id, key)] = group
    return group


def _character_for(
    session: Session,
    *,
    characters: dict[str, BookCharacter],
    version,
    speaker: dict,
    first_seen_cp: int,
) -> BookCharacter:
    """为清单身份建立书籍级人物，避免同名的不同色号在回导后被合并。"""

    key = str(speaker.get("key") or "")
    existing = characters.get(key)
    if existing is not None:
        return existing
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    temp_key = f"imported-annotation:{digest}"
    character = session.execute(
        select(BookCharacter).where(
            BookCharacter.book_version_id == version.id,
            BookCharacter.temp_key == temp_key,
        )
    ).scalar_one_or_none()
    if character is None:
        character = BookCharacter(
            book_version_id=version.id,
            temp_key=temp_key,
            canonical_name=str(speaker.get("label") or "")[:_MAX_GROUP_NAME_LEN],
            aliases_json="[]",
            description=(
                str(speaker.get("description") or "")[:_MAX_GROUP_DESCRIPTION_LEN] or None
            ),
            source=CharacterSource.USER,
            user_confirmed=True,
            confirmation_source="imported",
            first_seen_cp=first_seen_cp,
            preferred_color_index=(
                int(speaker["color_index"])
                if isinstance(speaker.get("color_index"), int)
                and int(speaker["color_index"]) >= 0
                else None
            ),
        )
        session.add(character)
        session.flush()
    characters[key] = character
    return character


def _annotation_for(
    entry: dict, *, quote: Quote, group: SpeakerGroup | None, scene: Scene | None
) -> Annotation | None:
    kind = _enum_or_none(entry.get("kind"), QuoteKind)
    status = _enum_or_none(entry.get("status"), AnnotationStatus)
    if kind is None or status is None:
        return None
    source = _enum_or_none(entry.get("source"), AnnotationSource) or AnnotationSource.USER
    return Annotation(
        quote_id=quote.id,
        scene_id=scene.id if scene is not None else None,
        kind=kind,
        assignment=_enum_or_none(entry.get("assignment"), Assignment),
        basis=_enum_or_none(entry.get("basis"), SpeakerBasis),
        speaker_id=group.id if group is not None else None,
        status=status,
        source=source,
        evidence_refs_json=json.dumps([], ensure_ascii=False),
        visible_from_cp=0,
        dependency_hash=None,
        stale=bool(entry.get("stale")),
        user_locked=True,
    )
