"""Chapter roster storage and confirmation.

The model proposes candidates, but stable book-level people are only used by
attribution after the user confirms the chapter roster and POV character.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
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
    ErrorCode,
    JobKind,
    JobPurpose,
    JobState,
)
from ..ingest.query import load_canonical_text
from ..jobs.service import digest_request, profile_snapshot
from ..llm.prompts import build_roster_messages
from ..llm.schemas import RosterOutput
from ..storage.models import (
    Book,
    BookCharacter,
    BookVersion,
    Chapter,
    ChapterCharacterRoster,
    Job,
    ModelProfile,
)


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
    characters = [
        _character_out(character)
        for character_id in character_ids
        if (character := session.get(BookCharacter, character_id)) is not None
    ]
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


def _match_existing(
    session: Session,
    version_id: str,
    *,
    name: str | None,
    aliases: list[str],
) -> BookCharacter | None:
    keys = {_normalize(value) for value in [name, *aliases] if _normalize(value)}
    if not keys:
        return None
    rows = session.execute(
        select(BookCharacter).where(BookCharacter.book_version_id == version_id)
    ).scalars()
    for row in rows:
        row_keys = {
            _normalize(row.canonical_name),
            *{_normalize(alias) for alias in _json_list(row.aliases_json)},
        }
        if keys & row_keys:
            return row
    return None


def store_roster_candidates(
    session: Session,
    *,
    version: BookVersion,
    chapter: Chapter,
    output: RosterOutput,
    job_id: str | None,
) -> ChapterCharacterRoster:
    roster = _roster_for_chapter(session, chapter.id)
    if roster is None:
        roster = ChapterCharacterRoster(
            chapter_id=chapter.id,
            book_version_id=version.id,
        )
        session.add(roster)

    records: list[dict[str, Any]] = []
    seen_refs: set[str] = set()
    for candidate in output.characters:
        if not candidate.temp_ref or candidate.temp_ref in seen_refs:
            continue
        seen_refs.add(candidate.temp_ref)
        matched = _match_existing(
            session,
            version.id,
            name=candidate.name,
            aliases=candidate.aliases,
        )
        character = matched
        if character is None:
            temp_key = f"{chapter.id}:{candidate.temp_ref}"
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
        # Model analysis may rediscover an already confirmed person under a
        # different name. Confirmation is the authority boundary: later model
        # output may link to that person, but must never rewrite the identity
        # the user approved.
        if not character.user_confirmed:
            character.canonical_name = candidate.name
            character.aliases_json = json.dumps(candidate.aliases, ensure_ascii=False)
            character.description = candidate.description
        session.flush()
        records.append(
            {
                "temp_ref": candidate.temp_ref,
                "character_id": character.id,
                "canonical_name": character.canonical_name or candidate.name,
                "aliases": _json_list(character.aliases_json),
                "description": character.description or "",
                "evidence_refs": candidate.evidence_refs,
                "pov_candidate": candidate.pov_candidate,
            }
        )

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
) -> BookCharacter:
    character_id = item.character_id or (source or {}).get("character_id")
    character = session.get(BookCharacter, character_id) if character_id else None
    if character is not None and character.book_version_id != version.id:
        raise ApiError.validation("character_id 不属于本书版本", character_id=character_id)

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
    character.canonical_name = name
    character.aliases_json = json.dumps(item.aliases, ensure_ascii=False)
    character.description = item.description or (source or {}).get("description") or ""
    character.source = CharacterSource.USER
    character.user_confirmed = True
    session.flush()
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
        raise ApiError.not_found("该章节还没有人物分析结果", chapter_id=chapter.id)
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
    confirmed_ids: list[str] = []
    character_by_ref: dict[str, BookCharacter] = {}
    for item in payload.candidates:
        source = current.get(item.temp_ref)
        if source is None and not (item.canonical_name or "").strip():
            raise ApiError.validation("人物候选不存在，且未提供新人物名称", temp_ref=item.temp_ref)
        if not item.accepted:
            continue
        character = _upsert_confirmed_character(
            session,
            version=version,
            chapter=chapter,
            item=item,
            source=source,
        )
        confirmed_ids.append(character.id)
        character_by_ref[item.temp_ref] = character

    if not confirmed_ids:
        raise ApiError.validation("至少确认一个人物")

    pov_character = character_by_ref.get(payload.pov_temp_ref or "")
    if pov_character is None:
        raise ApiError.validation(
            "本章主人公必须来自已确认的人物",
            pov_temp_ref=payload.pov_temp_ref,
        )

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
    characters = [
        character
        for character_id in character_ids
        if (character := session.get(BookCharacter, character_id)) is not None
    ]
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
) -> tuple[Job, bool]:
    request_payload = {
        "kind": JobKind.CHARACTER_ROSTER.value,
        "book_id": book.id,
        "book_version_id": version.id,
        "chapter_id": chapter.id,
        "profile_id": profile.id,
        "max_input_tokens": max_input_tokens,
    }
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
    active_duplicate = session.execute(
        select(Job)
        .where(
            Job.request_digest == digest,
            Job.state.in_((JobState.QUEUED, JobState.RUNNING, JobState.PAUSING)),
        )
        .order_by(Job.created_at.desc())
    ).scalars().first()
    if active_duplicate is not None:
        return active_duplicate, False

    job = Job(
        kind=JobKind.CHARACTER_ROSTER,
        purpose=JobPurpose.NONE,
        book_id=book.id,
        book_version_id=version.id,
        state=JobState.QUEUED,
        range_json=json.dumps(
            {
                "chapter_id": chapter.id,
                "start_cp": chapter.start_cp,
                "end_cp": chapter.end_cp,
            },
            ensure_ascii=False,
        ),
        profile_snapshot_json=json.dumps(profile_snapshot(profile), ensure_ascii=False),
        budget_json=json.dumps({"max_input_tokens": max_input_tokens}),
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
):
    text = load_canonical_text(settings, version)[chapter.start_cp : chapter.end_cp]
    return build_roster_messages(
        chapter_title=chapter.title,
        chapter_lines=text.splitlines() or [""],
        existing_characters=existing_characters_for_prompt(session, version),
    )
