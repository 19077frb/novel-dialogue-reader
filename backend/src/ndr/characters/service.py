"""Chapter roster storage and confirmation.

The model proposes candidates. Attribution requires an accepted chapter roster
and POV; human and automatic acceptance are recorded separately.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..api.errors import ApiError
from ..domain.characters import (
    BookCharacterOut,
    ChapterRosterOut,
    RosterCharacterCandidate,
    RosterConfirmIn,
)
from ..domain.enums import (
    CharacterRosterStatus,
    CharacterSource,
    ContentNodeType,
    ErrorCode,
    JobKind,
    JobPurpose,
    JobState,
    ReadingMode,
)
from ..ingest.document import collapse_whitespace, has_chapter_body_text
from ..ingest.query import load_canonical_text
from ..jobs.service import digest_request, profile_snapshot
from ..llm.prompts import build_roster_messages
from ..llm.schemas import RosterOutput
from ..llm.sourced_roster import (
    SOURCED_ROSTER_VERSION,
    SOURCED_ROSTER_VERSIONS,
    SourcedRosterOutput,
)
from ..scenes.state import SceneState
from ..storage.chapter_status import complete_chapter_automatically
from ..storage.models import (
    Book,
    BookCharacter,
    BookVersion,
    Chapter,
    ChapterCharacterRoster,
    ContentNode,
    Job,
    ModelProfile,
)
from .identity import supplement_aliases
from .input_view import IDENTITY_INPUT_VERSION, project_identity_state
from .names import GENERIC_NAMES, matches_name, revealed_name, undecorated_name


def _json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _normalize(value: str | None) -> str:
    return (value or "").strip().casefold()


def _character_out(row: BookCharacter) -> BookCharacterOut:
    return BookCharacterOut(
        character_id=row.id,
        name=row.canonical_name or "",
        aliases=_json_list(row.aliases_json),
        description=row.description or "",
        user_confirmed=row.user_confirmed,
        name_locked=row.name_locked,
        confirmation_source=row.confirmation_source,
    )


def list_book_characters(session: Session, version: BookVersion) -> list[BookCharacter]:
    return list(
        session.execute(
            select(BookCharacter)
            .where(BookCharacter.book_version_id == version.id)
            .order_by(BookCharacter.created_at, BookCharacter.id)
        ).scalars()
    )


def list_book_characters_out(session: Session, version: BookVersion) -> list[BookCharacterOut]:
    return [_character_out(row) for row in list_book_characters(session, version)]


def existing_characters_for_prompt(session: Session, version: BookVersion) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in list_book_characters_out(session, version)]


def _roster_out(
    session: Session,
    row: ChapterCharacterRoster,
) -> ChapterRosterOut:
    candidates = [
        RosterCharacterCandidate.model_validate(item)
        for item in json.loads(row.candidates_json or "[]")
        if isinstance(item, dict)
    ]
    character_ids = json.loads(row.confirmed_character_ids_json or "[]")
    by_id = (
        {
            character.id: character
            for character in session.scalars(
                select(BookCharacter).where(BookCharacter.id.in_(character_ids))
            )
        }
        if character_ids
        else {}
    )
    characters = [
        _character_out(character)
        for character_id in character_ids
        if (character := by_id.get(character_id)) is not None
    ]
    if row.status is CharacterRosterStatus.CONFIRMED:
        # Older confirmations saved identities but not the edited candidate snapshot.
        # Reconcile without rewriting the database or inventing model evidence.
        saved_by_id = {item.character_id: item for item in candidates}
        candidates = []
        for character_id in dict.fromkeys(character_ids):
            character = by_id.get(character_id)
            if character is None:
                continue
            saved = saved_by_id.get(character_id)
            candidates.append(RosterCharacterCandidate(
                temp_ref=saved.temp_ref if saved else f"confirmed-{character_id}",
                character_id=character_id,
                canonical_name=character.canonical_name,
                aliases=_json_list(character.aliases_json),
                description=character.description or "",
                evidence_refs=saved.evidence_refs if saved else [],
                pov_candidate=character_id == row.pov_character_id,
            ))
    return ChapterRosterOut(
        chapter_id=row.chapter_id,
        book_version_id=row.book_version_id,
        status=row.status,
        candidates=candidates,
        confirmed_characters=characters,
        pov_character_id=row.pov_character_id,
        version=row.version,
    )


def _roster_for_chapter(session: Session, chapter_id: str) -> ChapterCharacterRoster | None:
    return session.execute(
        select(ChapterCharacterRoster).where(ChapterCharacterRoster.chapter_id == chapter_id)
    ).scalar_one_or_none()


def get_roster(session: Session, version: BookVersion, chapter: Chapter) -> ChapterRosterOut:
    row = _roster_for_chapter(session, chapter.id)
    if row is None:
        return ChapterRosterOut(
            chapter_id=chapter.id,
            book_version_id=version.id,
            status=CharacterRosterStatus.DRAFT,
        )
    return _roster_out(session, row)


def complete_textless_chapter(session: Session, chapter: Chapter) -> ChapterCharacterRoster:
    """Persist a confirmed empty roster only after the caller checks actual text."""
    roster = _roster_for_chapter(session, chapter.id)
    if roster is None:
        roster = ChapterCharacterRoster(
            chapter_id=chapter.id, book_version_id=chapter.book_version_id,
        )
        session.add(roster)
    else:
        roster.version += 1
    roster.status = CharacterRosterStatus.CONFIRMED
    roster.candidates_json = "[]"
    roster.confirmed_character_ids_json = "[]"
    roster.pov_character_id = None
    complete_chapter_automatically(session, chapter)
    session.flush()
    return roster


def chapter_has_body_text(
    session: Session, settings, version: BookVersion, chapter: Chapter,  # noqa: ANN001
) -> bool:
    canonical = load_canonical_text(settings, version)
    if not 0 <= chapter.start_cp <= chapter.end_cp <= len(canonical):
        raise ApiError.validation("章节文字范围超出正文，不能判断为空白章", chapter_id=chapter.id)
    text = canonical[chapter.start_cp : chapter.end_cp]
    has_heading = False
    if chapter.title and collapse_whitespace(text) == collapse_whitespace(chapter.title):
        has_heading = session.scalar(select(ContentNode.id).where(
            ContentNode.chapter_id == chapter.id,
            ContentNode.node_type == ContentNodeType.HEADING,
        ).limit(1)) is not None
    return has_chapter_body_text(text, chapter.title, has_heading=has_heading)


def complete_existing_textless_chapters(session: Session, settings) -> int:  # noqa: ANN001
    """Startup backfill: inspect short unfinished chapters, not historical jobs."""
    candidates = session.execute(select(Chapter, BookVersion).join(
        BookVersion, BookVersion.id == Chapter.book_version_id,
    ).where(
        Chapter.dialogue_processed.is_(False),
        Chapter.processing_status_override.is_(None),
        Chapter.end_cp - Chapter.start_cp <= func.coalesce(func.length(Chapter.title), 0) + 64,
    ).order_by(Chapter.book_version_id, Chapter.id)).all()
    count = 0
    for chapter, version in candidates:
        try:
            has_text = chapter_has_body_text(session, settings, version, chapter)
        except (ApiError, OSError, UnicodeError):
            # Missing files are real read errors, not evidence that a chapter is empty.
            continue
        if not has_text:
            complete_textless_chapter(session, chapter)
            count += 1
    return count


def _match_existing(
    session: Session,
    version_id: str,
    *,
    name: str | None,
    aliases: list[str],
    characters: list[BookCharacter] | None = None,
) -> BookCharacter | None:
    keys = {_normalize(value) for value in [name, *aliases] if _normalize(value)}
    if not keys:
        return None
    rows = (
        characters
        if characters is not None
        else session.execute(
            select(BookCharacter).where(BookCharacter.book_version_id == version_id)
        ).scalars()
    )
    matches = []
    for row in rows:
        row_keys = {
            _normalize(row.canonical_name),
            *{_normalize(alias) for alias in _json_list(row.aliases_json)},
        }
        if (keys - GENERIC_NAMES) & row_keys or (
            name
            and undecorated_name(name) not in GENERIC_NAMES
            and matches_name(
                name,
                [row.canonical_name or "", *_json_list(row.aliases_json)],
            )
        ):
            matches.append(row)
    return matches[0] if len(matches) == 1 else None


def store_roster_candidates(
    session: Session,
    *,
    version: BookVersion,
    chapter: Chapter,
    output: RosterOutput,
    job_id: str | None,
    allow_overwrite_manual: bool = False,
    identity_facts=None,  # noqa: ANN001 - validated per-temp-ref identity blocks
    original=None,  # noqa: ANN001 - immutable OriginalIdentitySnapshot
) -> ChapterCharacterRoster:
    sourced = isinstance(output, SourcedRosterOutput)
    if sourced and (original is None or identity_facts is None
                    or set(identity_facts) != {c.temp_ref for c in output.characters}):
        raise ValueError("逐事实人物提案缺少核验后的原文与事实块")
    roster = _roster_for_chapter(session, chapter.id)
    if roster is None:
        roster = ChapterCharacterRoster(
            chapter_id=chapter.id,
            book_version_id=version.id,
            version=2,  # Version 1 is the empty GET placeholder before analysis.
        )
        session.add(roster)
    else:
        roster.version += 1

    records: list[dict[str, Any]] = []
    existing = list_book_characters(session, version)
    by_id = {row.id: row for row in existing}
    if any(candidate.character_id and candidate.character_id not in by_id
           for candidate in output.characters):
        raise ValueError("人物引用了未提供的全书人物 ID")
    seen_refs: set[str] = set()
    for candidate in output.characters:
        if not candidate.temp_ref or candidate.temp_ref in seen_refs:
            continue
        seen_refs.add(candidate.temp_ref)
        matched = by_id.get(candidate.character_id) if candidate.character_id else (
            None if sourced else _match_existing(
                session,
                version.id,
                name=candidate.name,
                aliases=candidate.aliases,
                characters=existing,
            )
        )
        character = matched
        if character is None:
            temp_key = (f"{chapter.id}:{job_id}:{candidate.temp_ref}" if sourced
                        else f"{chapter.id}:{candidate.temp_ref}")
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
                    source=CharacterSource.MODEL,
                    first_seen_cp=chapter.start_cp,
                )
                session.add(character)
                existing.append(character)
        if candidate.character_id and candidate.evidence_refs:
            supplement_aliases(session, character, [candidate.name or "", *candidate.aliases],
                               characters=existing)
        # Model analysis may rediscover an already confirmed person under a
        # different name. Confirmation is the authority boundary: later model
        # output may link to that person, but must never rewrite the accepted
        # identity. Automatic acceptance is not a claim of human review.
        if not character.user_confirmed and character.confirmation_source != "automatic":
            from .visibility import capture
            if character.canonical_name:
                capture(character, chapter.end_cp)
            character.canonical_name = (
                matched.canonical_name if matched else undecorated_name(candidate.name) or None
            )
            character.aliases_json = json.dumps(
                list(
                    dict.fromkeys(
                        [
                            *_json_list(character.aliases_json),
                            *candidate.aliases,
                        ]
                    )
                ),
                ensure_ascii=False,
            )
            character.description = candidate.description
            character.version = (character.version or 0) + 1
        session.flush()
        if sourced:
            from .facts import append_identity_facts

            # Analysis is still a proposal, even when a later confirmation is
            # allowed to update human data. Do not change the fact-side profile
            # before that confirmation.
            protected = character.user_confirmed or character.name_locked
            append_identity_facts(
                character, version,
                tuple(f.model_copy(update={"accepted": not protected})
                      for f in identity_facts[candidate.temp_ref]),
                original=original,
            )
        proposed_name = revealed_name(
            character.canonical_name, candidate.real_name or candidate.name,
            locked=character.name_locked and not allow_overwrite_manual,
        ) if candidate.evidence_refs else None
        if allow_overwrite_manual and candidate.evidence_refs:
            suggested = candidate.real_name or candidate.name
            from .names import is_role_name, valid_display_name
            if valid_display_name(suggested) and not is_role_name(suggested):
                from .names import prefer_complete_name

                current = character.canonical_name or ""
                shortened = (suggested != current
                             and prefer_complete_name(suggested, current) == current)
                proposed_name = suggested if suggested != current and not shortened else None
        if proposed_name and any(row.id != character.id and matches_name(
            proposed_name, [row.canonical_name or "", *_json_list(row.aliases_json)],
        ) for row in existing):
            proposed_name = None
        duplicate = next(
            (record for record in records if record["character_id"] == character.id),
            None,
        )
        if duplicate is not None:
            if proposed_name:
                duplicate["canonical_name"] = proposed_name
                duplicate["aliases"] = list(dict.fromkeys([
                    *duplicate["aliases"], character.canonical_name,
                ]))
            duplicate["pov_candidate"] |= candidate.pov_candidate
            duplicate["evidence_refs"] = list(
                dict.fromkeys(
                    [
                        *duplicate["evidence_refs"],
                        *candidate.evidence_refs,
                    ]
                )
            )
            continue
        records.append(
            {
                "temp_ref": candidate.temp_ref,
                "character_id": character.id,
                "canonical_name": proposed_name or character.canonical_name or candidate.name,
                "aliases": list(dict.fromkeys([
                    *_json_list(character.aliases_json),
                    *([character.canonical_name] if proposed_name else []),
                ])),
                "description": (candidate.description if candidate.evidence_refs and (
                                    allow_overwrite_manual or not (
                                        character.user_confirmed or character.name_locked
                                    )
                                ) else character.description)
                                or character.description or "",
                "evidence_refs": candidate.evidence_refs,
                "pov_candidate": candidate.pov_candidate,
            }
        )

    from .visibility import capture
    for character in existing:
        if character.canonical_name:
            capture(character, chapter.end_cp)
    roster.candidates_json = json.dumps(records, ensure_ascii=False)
    roster.status = CharacterRosterStatus.DRAFT
    roster.confirmed_character_ids_json = "[]"
    roster.pov_character_id = None
    roster.analysis_job_id = job_id
    session.flush()
    return roster


def _upsert_confirmed_character(
    session: Session,
    *,
    version: BookVersion,
    chapter: Chapter,
    item: Any,
    source: dict[str, Any] | None,
    automatic: bool = False,
    allow_overwrite_manual: bool = False,
) -> BookCharacter:
    character_id = item.character_id or (source or {}).get("character_id")
    character = session.get(BookCharacter, character_id) if character_id else None
    if character is not None and character.book_version_id != version.id:
        raise ApiError.validation("character_id 不属于本书版本", character_id=character_id)
    if automatic and not allow_overwrite_manual and character is not None and (
        character.user_confirmed or character.name_locked
    ):
        return character

    if character is None:
        temp_key = f"chapter-user:{chapter.id}:{item.temp_ref}"
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
                source=CharacterSource.USER,
                first_seen_cp=chapter.start_cp,
            )
            session.add(character)

    name = (item.canonical_name or (source or {}).get("canonical_name") or "").strip()
    if not name:
        raise ApiError.validation("已确认人物必须有名称", temp_ref=item.temp_ref)
    from .visibility import baseline, capture
    if character.canonical_name:
        baseline(character)
    else:
        capture(character, chapter.end_cp)
    old_name = character.canonical_name
    old_description = character.description
    if not automatic and (source is None or name != (source or {}).get("canonical_name")):
        character.name_locked = True
    character.canonical_name = name
    # A roster may have been previewed before a parallel dialogue window
    # discovered a new alias. Confirming that snapshot must not erase it;
    # deliberate alias removal remains available in the book directory.
    character.aliases_json = json.dumps(
        list(dict.fromkeys(value for value in [*_json_list(character.aliases_json), *item.aliases,
                          old_name if old_name != name else None] if value and value != name)),
        ensure_ascii=False,
    )
    character.description = (item.description or (source or {}).get("description")
                             or (character.description if automatic else "") or "")
    if not automatic:
        character.source = CharacterSource.USER
        character.user_confirmed = True
        character.confirmation_source = "manual"
    elif character.confirmation_source not in {"manual", "legacy", "imported"} and not (
        character.name_locked
    ):
        character.source = CharacterSource.MODEL
        character.user_confirmed = False
        character.confirmation_source = "automatic"
    character.version = (character.version or 0) + 1
    session.flush()
    capture(character, chapter.end_cp)
    if old_name and (old_name != name or old_description != character.description):
        from .directory import _sync
        _sync(session, version, character, visible_from_cp=chapter.end_cp)
    return character


def confirm_roster(
    session: Session,
    *,
    version: BookVersion,
    chapter: Chapter,
    payload: RosterConfirmIn,
) -> ChapterRosterOut:
    roster = _roster_for_chapter(session, chapter.id)
    if roster is None:
        if payload.confirmation_mode != "manual":
            raise ApiError.not_found("该章节还没有人物分析结果", chapter_id=chapter.id)
        roster = ChapterCharacterRoster(
            chapter_id=chapter.id, book_version_id=version.id, version=1,
        )
        session.add(roster)
    if roster.version != payload.expected_version:
        raise ApiError.validation(
            "人物名单已被修改，请刷新后重试",
            expected_version=roster.version,
            actual_version=payload.expected_version,
        )

    current = {
        item["temp_ref"]: item
        for item in json.loads(roster.candidates_json or "[]")
        if isinstance(item, dict)
    }
    analysis_job = session.get(Job, roster.analysis_job_id) if roster.analysis_job_id else None
    allow_overwrite_manual = bool(analysis_job and json.loads(
        analysis_job.range_json or "{}"
    ).get("allow_overwrite_manual", False))
    confirmed_ids: list[str] = []
    confirmed_candidates: list[dict[str, Any]] = []
    character_by_ref: dict[str, BookCharacter] = {}
    for item in payload.candidates:
        source = current.get(item.temp_ref)
        if source is None and not (item.canonical_name or "").strip():
            raise ApiError.validation("人物候选不存在，且未提供新人物名称", temp_ref=item.temp_ref)
        if not item.accepted:
            continue
        if payload.confirmation_mode == "automatic" and (
            source is None
            or (item.character_id and item.character_id != source.get("character_id"))
            or (item.canonical_name and item.canonical_name.strip() != source.get("canonical_name"))
            or item.aliases != source.get("aliases", [])
            or (item.description and item.description != source.get("description", ""))
        ):
            raise ApiError.validation("自动确认只能接受已保存的模型候选，编辑人物请使用人工确认")
        character = _upsert_confirmed_character(
            session,
            version=version,
            chapter=chapter,
            item=item,
            source=source,
            automatic=payload.confirmation_mode == "automatic",
            allow_overwrite_manual=allow_overwrite_manual,
        )
        confirmed_ids.append(character.id)
        character_by_ref[item.temp_ref] = character
        confirmed_candidates.append({
            "temp_ref": item.temp_ref,
            "character_id": character.id,
            "canonical_name": character.canonical_name,
            "aliases": _json_list(character.aliases_json),
            "description": character.description or "",
            "evidence_refs": (source or {}).get("evidence_refs", []),
            "pov_candidate": item.temp_ref == payload.pov_temp_ref,
        })

    if not confirmed_ids:
        raise ApiError.validation("至少确认一个人物")

    pov_character = character_by_ref.get(payload.pov_temp_ref or "")
    if pov_character is None:
        raise ApiError.validation(
            "本章主人公必须来自已确认的人物",
            pov_temp_ref=payload.pov_temp_ref,
        )

    roster.candidates_json = json.dumps(confirmed_candidates, ensure_ascii=False)
    roster.confirmed_character_ids_json = json.dumps(confirmed_ids, ensure_ascii=False)
    roster.pov_character_id = pov_character.id
    roster.status = CharacterRosterStatus.CONFIRMED
    roster.version += 1
    session.flush()
    return _roster_out(session, roster)


def confirmed_roster_context(
    session: Session,
    version: BookVersion,
    chapter_id: str | None,
) -> tuple[ChapterCharacterRoster | None, list[BookCharacter]]:
    if not chapter_id:
        return None, []
    roster = _roster_for_chapter(session, chapter_id)
    if roster is None or roster.status is not CharacterRosterStatus.CONFIRMED:
        return roster, []
    character_ids = json.loads(roster.confirmed_character_ids_json or "[]")
    by_id = (
        {
            character.id: character
            for character in session.scalars(
                select(BookCharacter).where(BookCharacter.id.in_(character_ids))
            )
        }
        if character_ids
        else {}
    )
    characters = [by_id[character_id] for character_id in character_ids if character_id in by_id]
    return roster, characters


def create_roster_job(
    session: Session,
    *,
    book: Book,
    version: BookVersion,
    chapter: Chapter,
    profile: ModelProfile,
    idempotency_key: str,
    max_input_tokens: int | None = None,
    inference_options: dict[str, Any] | None = None,
    allow_overwrite_manual: bool = False,
    roster_repair_enabled: bool = False,
    max_roster_repairs: int = 1,
    max_format_retries: int = 1,
) -> tuple[Job, bool]:
    request_payload = {
        "kind": JobKind.CHARACTER_ROSTER.value,
        "book_id": book.id,
        "book_version_id": version.id,
        "chapter_id": chapter.id,
        "profile_id": profile.id,
        "max_input_tokens": max_input_tokens,
        "inference_options": inference_options or {},
    }
    # Preserve the digest of pre-setting requests so restored legacy jobs keep
    # their original idempotency keys. Opting in is a distinct request.
    if allow_overwrite_manual:
        request_payload["allow_overwrite_manual"] = True
    repair_range = {}
    repair_budget = {}
    if roster_repair_enabled:
        from ..llm.isolated_roster_repair import ISOLATED_REPAIR_POLICY

        repair_range = {"roster_repair_protocol": "roster-repair-1",
                        "roster_repair_policy": ISOLATED_REPAIR_POLICY}
        repair_budget = {
            "max_roster_repairs": max_roster_repairs,
            "max_format_retries": max_format_retries,
        }
        # Same user request/key must still return its original frozen legacy job.
        # Only a newly created job receives the new internal compilation policy.
        request_payload["roster_repair_protocol"] = repair_range["roster_repair_protocol"]
        request_payload.update(repair_budget)
    digest = digest_request(request_payload)
    existing = session.execute(
        select(Job).where(Job.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_digest == digest:
            return existing, False
        raise ApiError(
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "同一幂等键已被不同的请求使用",
            details={"idempotency_key": idempotency_key, "job_id": existing.id},
            status_code=409,
        )
    active_duplicate = (
        session.execute(
            select(Job)
            .where(
                Job.request_digest == digest,
                Job.state.in_((JobState.QUEUED, JobState.RUNNING, JobState.PAUSING)),
            )
            .order_by(Job.created_at.desc())
        )
        .scalars()
        .first()
    )
    if active_duplicate is not None:
        return active_duplicate, False

    from ..jobs.occupancy import guard_active_target

    guard_active_target(session, version.id, JobKind.CHARACTER_ROSTER,
                        {"chapter_id": chapter.id})

    job = Job(
        kind=JobKind.CHARACTER_ROSTER,
        purpose=JobPurpose.NONE,
        book_id=book.id,
        book_version_id=version.id,
        state=JobState.QUEUED,
        range_json=json.dumps(
            {
                "chapter_id": chapter.id,
                "allow_overwrite_manual": allow_overwrite_manual,
                "start_cp": chapter.start_cp,
                "end_cp": chapter.end_cp,
                "roster_protocol": SOURCED_ROSTER_VERSION,
                "identity_input_version": IDENTITY_INPUT_VERSION,
                **repair_range,
            },
            ensure_ascii=False,
        ),
        profile_snapshot_json=json.dumps(
            profile_snapshot(profile, inference_options), ensure_ascii=False
        ),
        budget_json=json.dumps({"max_input_tokens": max_input_tokens, **repair_budget}),
        progress_json=json.dumps({"stage": "queued", "calls": 0}, ensure_ascii=False),
        idempotency_key=idempotency_key,
        request_digest=digest,
    )
    session.add(job)
    session.flush()
    return job, True


def roster_messages(
    session: Session,
    settings,  # noqa: ANN001
    job: Job,
    version: BookVersion,
    chapter: Chapter,
    *,
    canonical_text: str | None = None,
):
    canonical = (load_canonical_text(settings, version)
                 if canonical_text is None else canonical_text)
    text = canonical[chapter.start_cp : chapter.end_cp]
    identity_version = json.loads(job.range_json or "{}").get("identity_input_version")
    if identity_version is None:
        existing = existing_characters_for_prompt(session, version)
    elif identity_version == IDENTITY_INPUT_VERSION:
        people = list_book_characters(session, version)
        projected = project_identity_state(SceneState(), people, version,
                                           reading_mode=ReadingMode.INITIAL, horizon=chapter.end_cp)
        existing = [{**projected[p.id].prompt_record(), "name_locked": p.name_locked}
                    for p in people]
    else:
        raise ValueError("人物输入版本不受支持")
    return build_roster_messages(
        chapter_title=chapter.title,
        chapter_lines=text.splitlines() or [""],
        existing_characters=existing,
        sourced=(json.loads(job.range_json or "{}").get("roster_protocol")
                 in SOURCED_ROSTER_VERSIONS),
    )
