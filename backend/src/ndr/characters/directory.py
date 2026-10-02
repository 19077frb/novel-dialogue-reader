"""Explicit user edits to the book-wide identity directory; never calls a model."""

from __future__ import annotations

import json

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..api.errors import ApiError
from ..domain.characters import CharacterDirectoryOut, CharacterEditIn, CharacterMergeIn
from ..domain.enums import CharacterSource, ErrorCode, JobState
from ..storage.models import (
    BookCharacter,
    BookVersion,
    ChapterCharacterRoster,
    Job,
    Scene,
    SpeakerGroup,
)
from ..storage.transactions import check_version
from .service import _character_out, _json_list, list_book_characters


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


def directory(session: Session, version: BookVersion) -> list[CharacterDirectoryOut]:
    entries = [
        CharacterDirectoryOut(**_character_out(row).model_dump(), version=row.version)
        for row in list_book_characters(session, version)
    ]
    for group in _groups(session, version, SpeakerGroup.character_id.is_(None)):
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
    return entries


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
) -> None:
    """Update live references, roster snapshots and resumable checkpoints atomically."""
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
            group.character_id = target.id
            group.canonical_name = target.canonical_name
            group.description = target.description
            group.version += 1
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
    row.canonical_name = name
    row.aliases_json = json.dumps(aliases, ensure_ascii=False)
    row.description = payload.description.strip()
    row.user_confirmed = True
    row.name_locked = True
    row.confirmation_source = "manual"
    row.source = CharacterSource.USER
    row.version += 1
    _sync(session, version, row, source_group_id=group_id)
    return CharacterDirectoryOut(**_character_out(row).model_dump(), version=row.version)


def merge(
    session: Session,
    version: BookVersion,
    entry_id: str,
    payload: CharacterMergeIn,
    *,
    active_job_id: str | None = None,
    model_decision: bool = False,
) -> CharacterDirectoryOut:
    _guard(session, version, active_job_id)
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
    )
    if isinstance(source, BookCharacter):
        session.delete(source)
    session.flush()
    return CharacterDirectoryOut(**_character_out(target).model_dump(), version=target.version)
