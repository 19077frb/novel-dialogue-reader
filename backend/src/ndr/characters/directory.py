"""Explicit user edits to the book-wide identity directory; never calls a model."""

from __future__ import annotations

import json

from sqlalchemy import func, literal, or_, select
from sqlalchemy.orm import Session

from ..api.errors import ApiError
from ..domain.characters import (
    CharacterColorIn,
    CharacterDirectoryOut,
    CharacterEditIn,
    CharacterMergeIn,
)
from ..domain.enums import (
    AnnotationStatus,
    CharacterRosterStatus,
    CharacterSource,
    ErrorCode,
    JobState,
    QuoteKind,
)
from ..ingest.query import load_canonical_text
from ..storage.models import (
    Annotation,
    BookCharacter,
    BookVersion,
    ChapterCharacterRoster,
    Job,
    Quote,
    Scene,
    SpeakerGroup,
)
from ..storage.transactions import check_version
from .colors import color_projection
from .facts import (
    IdentityLink,
    OriginalIdentitySnapshot,
    prepare_merged_identity_facts,
    read_identity_facts,
)
from .service import _character_out, _json_list, list_book_characters
from .visibility import baseline, capture, position


def _groups(session: Session, version: BookVersion, *filters) -> list[SpeakerGroup]:  # noqa: ANN002
    return list(
        session.scalars(
            select(SpeakerGroup)
            .join(Scene)
            .where(
                Scene.book_version_id == version.id,
                *filters,
            )
        )
    )


def _appearance_statistics(session: Session, version: BookVersion) -> dict[str, tuple[int, int]]:
    """Read current assignments in one grouped query, not one query per character."""
    chapters: dict[str, set[str]] = {}
    dialogues: dict[str, int] = {}
    key = func.coalesce(SpeakerGroup.character_id, literal("speaker:") + SpeakerGroup.id)
    for entry_id, chapter_id, count in session.execute(
        select(key, Quote.chapter_id, func.count(func.distinct(Annotation.quote_id)))
        .select_from(Quote)
        .join(Annotation, Annotation.quote_id == Quote.id)
        .join(SpeakerGroup, SpeakerGroup.id == Annotation.speaker_id)
        .join(Scene, Scene.id == SpeakerGroup.scene_id)
        .where(
            Quote.book_version_id == version.id, Scene.book_version_id == version.id,
            Annotation.kind == QuoteKind.SPEECH, Annotation.stale.is_(False),
            Annotation.status.in_((
                AnnotationStatus.ACCEPTED, AnnotationStatus.PROVISIONAL,
                AnnotationStatus.USER_CONFIRMED,
            )),
        )
        .group_by(key, Quote.chapter_id)
    ):
        dialogues[entry_id] = dialogues.get(entry_id, 0) + count
        if chapter_id:
            chapters.setdefault(entry_id, set()).add(chapter_id)
    for chapter_id, ids_json in session.execute(
        select(
            ChapterCharacterRoster.chapter_id, ChapterCharacterRoster.confirmed_character_ids_json,
        )
        .where(ChapterCharacterRoster.book_version_id == version.id,
               ChapterCharacterRoster.status == CharacterRosterStatus.CONFIRMED)
    ):
        for character_id in _json_list(ids_json):
            chapters.setdefault(character_id, set()).add(chapter_id)
    return {key: (len(chapters.get(key, set())), dialogues.get(key, 0))
            for key in chapters.keys() | dialogues.keys()}


def directory(
    session: Session, version: BookVersion, *, include_statistics: bool = True,
) -> list[CharacterDirectoryOut]:
    characters = list_book_characters(session, version)
    if include_statistics:
        groups, presentations, colors = color_projection(session, version.id, characters=characters)
    else:
        groups = _groups(session, version, SpeakerGroup.character_id.is_(None))
    entries = [
        CharacterDirectoryOut(**_character_out(row).model_dump(), version=row.version)
        for row in characters
    ]
    for group in groups:
        if not group.character_id:
            entries.append(
                CharacterDirectoryOut(
                    character_id=f"speaker:{group.id}",
                    kind="speaker",
                    version=group.version,
                    name=group.canonical_name or "未命名人物",
                    description=group.description or "",
                )
            )
    if include_statistics:
        for entry in entries:
            key = (presentations[entry.character_id.removeprefix("speaker:")]["identity"]
                   if entry.kind == "speaker" else f"character:{entry.character_id}")
            entry.color_index = colors.get(key)
        preferred = {row.id: row.preferred_color_index for row in characters}
        for entry in entries:
            entry.preferred_color_index = preferred.get(entry.character_id)
        statistics = _appearance_statistics(session, version)
        for entry in entries:
            entry.chapter_count, entry.dialogue_count = statistics.get(entry.character_id, (0, 0))
        entries.sort(key=lambda row: (
            -(row.chapter_count or 0), -(row.dialogue_count or 0),
            row.name.casefold(), row.character_id,
        ))
    return entries


def set_color(session, version, entry_id, payload: CharacterColorIn):
    _guard(session, version)
    row = _resolve(session, version, entry_id)
    check_version(row, payload.expected_version)
    if not isinstance(row, BookCharacter):
        raise ApiError.validation("请先保存该人物资料，纳入全书人物后再设置颜色")
    if payload.color_index is not None:
        conflict = session.scalar(select(BookCharacter).where(
            BookCharacter.book_version_id == version.id, BookCharacter.id != row.id,
            BookCharacter.preferred_color_index == payload.color_index,
        ).limit(1))
        if conflict:
            raise ApiError.validation(
                f"此颜色已由“{conflict.canonical_name}”手动指定，请选择另一颜色",
            )
    row.preferred_color_index = payload.color_index
    row.version += 1
    session.flush()
    return next(entry for entry in directory(session, version) if entry.character_id == row.id)


def _resolve(
    session: Session,
    version: BookVersion,
    entry_id: str,
) -> BookCharacter | SpeakerGroup:
    if entry_id.startswith("speaker:"):
        row = session.get(SpeakerGroup, entry_id.removeprefix("speaker:"))
        scene = session.get(Scene, row.scene_id) if row else None
        if row and scene and scene.book_version_id == version.id and not row.character_id:
            return row
    else:
        row = session.get(BookCharacter, entry_id)
        if row and row.book_version_id == version.id:
            return row
    raise ApiError.not_found("人物不存在或已合并，请重新读取人物表")


def _guard(session: Session, version: BookVersion, active_job_id: str | None = None) -> None:
    job = session.scalars(
        select(Job)
        .where(
            Job.book_version_id == version.id,
            Job.state.in_((JobState.QUEUED, JobState.RUNNING, JobState.PAUSING)),
            Job.id != active_job_id if active_job_id else True,
        )
        .limit(1)
    ).first()
    if job:
        raise ApiError(
            ErrorCode.RESOURCE_CONFLICT, "本书有处理任务正在运行，请停止任务后再修改人物"
        )


def _sync(
    session: Session,
    version: BookVersion,
    target: BookCharacter,
    source_id: str | None = None,
    source_group_id: str | None = None,
    visible_from_cp: int | None = None,
    force_history: bool = False,
) -> None:
    """Update live references, roster snapshots and resumable checkpoints atomically."""
    cp = position(version, visible_from_cp)
    capture(target, cp, force=force_history)
    for group in _groups(
        session,
        version,
        or_(
            SpeakerGroup.character_id.in_([value for value in (target.id, source_id) if value]),
            SpeakerGroup.id == source_group_id if source_group_id else False,
        ),
    ):
        if (group.character_id and group.character_id in {target.id, source_id}) or (
            group.id == source_group_id
        ):
            baseline(group)
            group.character_id = target.id
            group.canonical_name = target.canonical_name
            group.description = target.description
            group.version += 1
            capture(group, cp, force=force_history)
    for roster in session.scalars(
        select(ChapterCharacterRoster).where(
            ChapterCharacterRoster.book_version_id == version.id,
        )
    ):
        changed = False
        candidates = json.loads(roster.candidates_json)
        for item in candidates:
            if item.get("character_id") in {target.id, source_id} and item.get("character_id"):
                item.update(
                    character_id=target.id,
                    canonical_name=target.canonical_name,
                    aliases=_json_list(target.aliases_json),
                    description=target.description or "",
                )
                changed = True
        ids = _json_list(roster.confirmed_character_ids_json)
        replaced = list(dict.fromkeys(target.id if value == source_id else value for value in ids))
        if roster.pov_character_id == source_id and source_id:
            roster.pov_character_id = target.id
            changed = True
        if replaced != ids:
            roster.confirmed_character_ids_json = json.dumps(replaced)
            changed = True
        if changed:
            if source_id:
                unique: dict[str, dict] = {}
                for item in candidates:
                    key = item.get("character_id") or item["temp_ref"]
                    if key in unique:
                        previous = unique[key]
                        previous["pov_candidate"] = bool(
                            previous.get("pov_candidate") or item.get("pov_candidate")
                        )
                        previous["evidence_refs"] = list(
                            dict.fromkeys(
                                [
                                    *previous.get("evidence_refs", []),
                                    *item.get("evidence_refs", []),
                                ]
                            )
                        )
                    else:
                        unique[key] = item
                candidates = list(unique.values())
            roster.candidates_json = json.dumps(candidates, ensure_ascii=False)
            roster.version += 1

    def rewrite(value):  # noqa: ANN001, ANN202
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if not isinstance(value, dict):
            return value
        value = {key: rewrite(item) for key, item in value.items()}
        if source_id and value.get("pov_character_id") == source_id:
            value["pov_character_id"] = target.id
        if (value.get("character_id") and value.get("character_id") in {source_id, target.id}) or (
            source_group_id and value.get("group_id") == source_group_id
        ):
            value.update(
                character_id=target.id,
                canonical_name=target.canonical_name,
                description=target.description or "",
            )
            if "name" in value:
                value["name"] = target.canonical_name
            if "aliases" in value:
                value["aliases"] = _json_list(target.aliases_json)
        return value

    for job in session.scalars(
        select(Job).where(
            Job.book_version_id == version.id,
            Job.state != JobState.COMPLETED,
            Job.checkpoint_json.is_not(None),
        )
    ):
        if job.checkpoint_json and job.state is not JobState.COMPLETED:
            checkpoint = json.loads(job.checkpoint_json)
            updated = rewrite(checkpoint)
            if updated != checkpoint:
                job.checkpoint_json = json.dumps(updated, ensure_ascii=False)
    session.flush()


def edit(
    session: Session,
    version: BookVersion,
    entry_id: str,
    payload: CharacterEditIn,
    *,
    active_job_id: str | None = None,
) -> CharacterDirectoryOut:
    _guard(session, version, active_job_id)
    cp = position(version, payload.visible_from_cp)
    row = _resolve(session, version, entry_id)
    check_version(row, payload.expected_version)
    name = payload.name.strip()
    aliases = list(dict.fromkeys(value.strip() for value in payload.aliases if value.strip()))
    if not name or any(len(value) > 128 for value in aliases):
        raise ApiError.validation("姓名不能为空，别名最多 128 字")
    group_id = row.id if isinstance(row, SpeakerGroup) else None
    if group_id:
        row = BookCharacter(
            book_version_id=version.id,
            temp_key=f"directory:{group_id}",
            first_seen_cp=session.get(Scene, row.scene_id).start_cp,
        )
        session.add(row)
        session.flush()
    baseline(row)
    row.canonical_name = name
    row.aliases_json = json.dumps(aliases, ensure_ascii=False)
    row.description = payload.description.strip()
    row.user_confirmed = True
    row.name_locked = True
    row.confirmation_source = "manual"
    row.source = CharacterSource.USER
    row.version += 1
    _sync(session, version, row, source_group_id=group_id, visible_from_cp=cp,
          force_history=payload.visible_from_cp is not None)
    return CharacterDirectoryOut(**_character_out(row).model_dump(), version=row.version)


def merge(
    session: Session,
    version: BookVersion,
    entry_id: str,
    payload: CharacterMergeIn,
    *,
    active_job_id: str | None = None,
    model_decision: bool = False,
    settings=None,
    identity_originals: dict[str, OriginalIdentitySnapshot] | None = None,
) -> CharacterDirectoryOut:
    _guard(session, version, active_job_id)
    cp = position(version, payload.visible_from_cp)
    source = _resolve(session, version, entry_id)
    target = _resolve(session, version, payload.target_character_id)
    if (
        not isinstance(target, BookCharacter)
        or source.id == target.id
        or not (target.canonical_name or "").strip()
    ):
        raise ApiError.validation("请选择另一个已有的全书人物作为合并目标")
    check_version(source, payload.expected_version)
    check_version(target, payload.expected_target_version)
    try:
        source_facts = (
            read_identity_facts(source, version) if isinstance(source, BookCharacter) else ()
        )
        read_identity_facts(target, version)
        if source_facts:
            # Validate and prepare the entire block before changing live references.
            if settings is None:
                raise ApiError.validation("人物包含原文身份资料，合并需要核对当前书库原文")
            originals = identity_originals if identity_originals is not None else {}
            if version.id not in originals:
                originals[version.id] = OriginalIdentitySnapshot(
                    version.id, version.canonical_sha256, load_canonical_text(settings, version),
                )
            original = originals[version.id]
            target.identity_facts_json = prepare_merged_identity_facts(
                source, target, version,
                link=IdentityLink(
                    source_id=source.id, target_id=target.id, visible_from_cp=cp,
                    source="model" if model_decision else "user",
                    source_ref=f"merge:{source.id}:{target.id}", accepted=True,
                ),
                original=original,
            )
    except ValueError as exc:
        raise ApiError.validation("人物身份资料未通过校验，未执行合并") from exc
    baseline(target)
    aliases = [*_json_list(target.aliases_json), source.canonical_name or ""]
    if isinstance(source, BookCharacter):
        aliases += _json_list(source.aliases_json)
    target.aliases_json = json.dumps(
        list(dict.fromkeys(value for value in aliases if value and value != target.canonical_name)),
        ensure_ascii=False,
    )
    if not target.description:
        target.description = source.description
    if isinstance(source, BookCharacter) and source.first_seen_cp is not None:
        target.first_seen_cp = min(
            target.first_seen_cp if target.first_seen_cp is not None else source.first_seen_cp,
            source.first_seen_cp,
        )
    target.user_confirmed = target.user_confirmed if model_decision else True
    if not model_decision:
        target.confirmation_source = "manual"
    target.source = CharacterSource.MODEL if model_decision else CharacterSource.USER
    target.version += 1
    _sync(
        session,
        version,
        target,
        source_id=source.id if isinstance(source, BookCharacter) else None,
        source_group_id=source.id if isinstance(source, SpeakerGroup) else None,
        visible_from_cp=cp,
    )
    if isinstance(source, BookCharacter):
        session.delete(source)
    session.flush()
    return CharacterDirectoryOut(**_character_out(target).model_dump(), version=target.version)
