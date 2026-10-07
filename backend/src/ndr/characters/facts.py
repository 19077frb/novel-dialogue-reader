"""Opt-in sourced identity storage. Does not infer facts from current metadata."""

import hashlib
import json
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StrictBool, StrictInt, model_validator

from ..domain.common import ApiModel
from ..storage.models import BookCharacter, BookVersion
from .names import valid_display_name

FACTS_VERSION = "identity-facts-1"
Position = Annotated[StrictInt, Field(ge=0)]


class IdentityLink(ApiModel):
    """An accepted merge event, not proof that two identities truly are the same."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1, max_length=160)
    target_id: str = Field(min_length=1, max_length=160)
    visible_from_cp: Position
    source: Literal["user", "model"]
    source_ref: str = Field(min_length=1, max_length=160)
    accepted: StrictBool = False

    @model_validator(mode="after")
    def check_link(self):
        if any(not value.strip() for value in (self.source_id, self.target_id, self.source_ref)):
            raise ValueError("Identity link identifiers must be nonblank")
        if self.source_id == self.target_id:
            raise ValueError("Identity link must join distinct identities")
        return self


class CharacterIdentityFact(ApiModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["identity-facts-1"] = FACTS_VERSION
    kind: Literal["name", "alias", "designation", "description", "relation"]
    value: str = Field(min_length=1, max_length=512)
    visible_from_cp: Position
    canonical_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_spans: tuple[tuple[Position, Position], ...] = ()
    source: Literal["user", "model", "source"]
    source_ref: str = Field(min_length=1, max_length=160)
    accepted: StrictBool = False
    identity_links: tuple[IdentityLink, ...] = ()

    @model_validator(mode="after")
    def check_proof(self):
        if not self.value.strip() or not self.source_ref.strip():
            raise ValueError("Fact value and source reference must be nonblank")
        if self.kind in {"name", "alias", "designation"} and not valid_display_name(self.value):
            raise ValueError("Identity names must be short names or distinct roles")
        if self.source != "user" and not self.evidence_spans:
            raise ValueError("Non-user identity facts require original evidence")
        if len(set(self.evidence_spans)) != len(self.evidence_spans):
            raise ValueError("Duplicate identity evidence")
        if any(a >= b or b > self.visible_from_cp for a, b in self.evidence_spans):
            raise ValueError("Identity evidence must precede its reveal position")
        visited = set()
        for index, link in enumerate(self.identity_links):
            if index and self.identity_links[index - 1].target_id != link.source_id:
                raise ValueError("Identity link chain must be continuous")
            if link.source_id in visited or link.target_id in visited:
                raise ValueError("Identity link chain cannot revisit an identity")
            visited.add(link.source_id)
        return self


class IdentityProfileUpdate(ApiModel):
    """A deliberate field update, never a claimed quotation from the original."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["identity-facts-1"] = FACTS_VERSION
    kind: Literal["profile_update"] = "profile_update"
    field: Literal["name", "aliases", "description"]
    value: str | tuple[str, ...]
    visible_from_cp: Position
    canonical_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: Literal["user", "model"]
    source_ref: str = Field(min_length=1, max_length=160)
    accepted: StrictBool = False
    identity_links: tuple[IdentityLink, ...] = ()

    @model_validator(mode="after")
    def check_update(self):
        if not self.source_ref.strip():
            raise ValueError("Profile update needs a source reference")
        if self.field == "aliases":
            if (
                not isinstance(self.value, tuple)
                or (self.source == "user" and len(self.value) > 64)
                or len(set(self.value)) != len(self.value)
                or any(
                    not v.strip() or (self.source == "user" and len(v) > 128) for v in self.value
                )
            ):
                raise ValueError("Profile aliases must be distinct bounded names")
        elif not isinstance(self.value, str):
            raise ValueError("Profile name and description must be strings")
        elif self.field == "name" and (not self.value.strip() or len(self.value) > 128):
            raise ValueError("Profile name must be nonblank and at most 128 characters")
        elif self.field == "description" and len(self.value) > 512:
            raise ValueError("Profile description must be at most 512 characters")
        # Reuse the chain invariant without presenting this update as original proof.
        _check_update_chain(self.identity_links)
        return self


def _check_update_chain(links):
    visited = set()
    for index, link in enumerate(links):
        if index and links[index - 1].target_id != link.source_id:
            raise ValueError("Identity link chain must be continuous")
        if link.source_id in visited or link.target_id in visited:
            raise ValueError("Identity link chain cannot revisit an identity")
        visited.add(link.source_id)


IdentityRecord = CharacterIdentityFact | IdentityProfileUpdate


def _parse_record(value) -> IdentityRecord:
    model = (
        IdentityProfileUpdate
        if isinstance(value, dict) and value.get("kind") == "profile_update"
        else CharacterIdentityFact
    )
    return model.model_validate(value)


@dataclass(frozen=True)
class OriginalIdentitySnapshot:
    book_version_id: str
    canonical_sha256: str
    text: str

    def __post_init__(self):
        if (
            not self.book_version_id
            or hashlib.sha256(self.text.encode("utf8")).hexdigest() != self.canonical_sha256
        ):
            raise ValueError("Identity original snapshot hash mismatch")


def _validate_version(character: BookCharacter, version: BookVersion) -> None:
    if character.book_version_id != version.id:
        raise ValueError("Identity belongs to another book version")


def read_identity_records(
    character: BookCharacter, version: BookVersion
) -> tuple[IdentityRecord, ...]:
    _validate_version(character, version)
    raw = json.loads(character.identity_facts_json or "[]")
    if not isinstance(raw, list):
        raise ValueError("Identity facts must be a list")
    facts = tuple(_parse_record(value) for value in raw)
    for fact in facts:
        if (
            fact.canonical_sha256 != version.canonical_sha256
            or fact.visible_from_cp > version.canonical_length_cp
        ):
            raise ValueError("Identity fact does not belong to the immutable original")
        _validate_links(fact, character, version)
    return facts


def read_identity_facts(
    character: BookCharacter, version: BookVersion
) -> tuple[CharacterIdentityFact, ...]:
    return tuple(
        f for f in read_identity_records(character, version) if isinstance(f, CharacterIdentityFact)
    )


def _validate_links(fact, character, version):
    if any(link.visible_from_cp > version.canonical_length_cp for link in fact.identity_links):
        raise ValueError("Identity link is outside the immutable original")
    if fact.identity_links and fact.identity_links[-1].target_id != character.id:
        raise ValueError("Identity link does not lead to its current character")


def append_identity_facts(
    character: BookCharacter,
    version: BookVersion,
    facts: tuple[CharacterIdentityFact, ...],
    *,
    original: OriginalIdentitySnapshot | None = None,
) -> bool:
    return append_identity_records(character, version, facts, original=original)


def append_identity_records(
    character: BookCharacter,
    version: BookVersion,
    facts: tuple[IdentityRecord, ...],
    *,
    original: OriginalIdentitySnapshot | None = None,
) -> bool:
    """Validate the whole block before mutating; the caller owns its transaction."""
    current = read_identity_records(character, version)
    if original is not None and (
        original.book_version_id != version.id
        or original.canonical_sha256 != version.canonical_sha256
        or len(original.text) != version.canonical_length_cp
    ):
        raise ValueError("Identity original snapshot belongs to another book version")
    validated = []
    for supplied in facts:
        # Revalidate even already constructed objects instead of trusting bypassed models.
        fact = _parse_record(supplied.model_dump())
        _validate_links(fact, character, version)
        if (
            fact.canonical_sha256 != version.canonical_sha256
            or fact.visible_from_cp > version.canonical_length_cp
        ):
            raise ValueError("Identity fact is outside the immutable original")
        if isinstance(fact, CharacterIdentityFact) and (
            fact.source != "user" or fact.evidence_spans
        ):
            if original is None:
                raise ValueError("Identity original snapshot is required")
            proof = [original.text[a:b] for a, b in fact.evidence_spans]
            if any(not value.strip() for value in proof):
                raise ValueError("Identity evidence must contain original text")
            if (
                fact.source != "user"
                and fact.kind in {"name", "alias"}
                and not any(fact.value in value for value in proof)
            ):
                raise ValueError("Identity name or alias lacks its own literal proof")
        validated.append(fact)
    combined = tuple(dict.fromkeys((*current, *validated)))
    if combined == current:
        return False
    character.identity_facts_json = json.dumps(
        [f.model_dump(mode="json") for f in combined], ensure_ascii=False
    )
    character.version = (character.version or 1) + 1
    return True


def visible_identity_facts(
    character: BookCharacter,
    version: BookVersion,
    *,
    horizon: int,
) -> tuple[CharacterIdentityFact, ...]:
    return tuple(
        f
        for f in visible_identity_records(character, version, horizon=horizon)
        if isinstance(f, CharacterIdentityFact)
    )


def visible_identity_records(character, version, *, horizon: int) -> tuple[IdentityRecord, ...]:
    if type(horizon) is not int or not 0 <= horizon <= version.canonical_length_cp:
        raise ValueError("Explicit identity horizon must be within the original")
    return tuple(
        f
        for f in read_identity_records(character, version)
        if f.accepted
        and f.visible_from_cp <= horizon
        and all(link.accepted and link.visible_from_cp <= horizon for link in f.identity_links)
    )


def visible_identity_profile(
    character: BookCharacter, version: BookVersion, *, horizon: int
) -> dict:
    records = visible_identity_records(character, version, horizon=horizon)
    cohorts = {}
    for index, record in enumerate(records):
        origin = record.identity_links[0].source_id if record.identity_links else character.id
        cohorts.setdefault(origin, []).append((index, record))
    profiles = {origin: _cohort_profile(rows) for origin, rows in cohorts.items()}
    own = profiles.get(character.id, {})
    named = [p for p in profiles.values() if p["name"] is not None]
    chosen = (
        own
        if own.get("name") is not None
        else max(
            named,
            key=lambda p: p["name_position"],
            default={},
        )
    )
    name = chosen.get("name")
    aliases = list(own.get("aliases", {}))
    alias_reset = own.get("alias_reset_index", -1)
    for origin, profile in profiles.items():
        if origin == character.id:
            continue
        if profile["name"] and profile["name_position"][1] > alias_reset:
            aliases.append(profile["name"])
        aliases.extend(
            value for value, position in profile["aliases"].items() if position[1] > alias_reset
        )
    described = [p for p in profiles.values() if p["description_defined"]]
    description = (
        own
        if own.get("description_defined")
        else max(
            described,
            key=lambda p: p["description_position"],
            default={},
        )
    )
    facts = tuple(f for f in records if isinstance(f, CharacterIdentityFact))
    return {
        "character_id": character.id,
        "name": name,
        "aliases": tuple(dict.fromkeys(v for v in aliases if v != name)),
        "description": description.get("description", ""),
        "relations": tuple(f for f in facts if f.kind == "relation"),
        "facts": facts,
        "updates": tuple(r for r in records if isinstance(r, IdentityProfileUpdate)),
    }


def _effective_position(index, record):
    return max(
        (record.visible_from_cp, *(link.visible_from_cp for link in record.identity_links))
    ), index


def _latest_field(facts, updates, kind, field):
    latest_update = max(
        ((i, r) for i, r in updates if r.field == field), key=lambda row: row[0], default=None
    )
    cutoff = latest_update[0] if latest_update else -1
    new_facts = [(i, r) for i, r in facts if r.kind == kind and i > cutoff]
    return max(new_facts, key=lambda row: (row[1].visible_from_cp, row[0]), default=latest_update)


def _cohort_profile(rows):
    facts = [(i, r) for i, r in rows if isinstance(r, CharacterIdentityFact)]
    updates = [(i, r) for i, r in rows if isinstance(r, IdentityProfileUpdate)]
    roles = [(i, r) for i, r in facts if r.kind == "designation"]
    chosen = _latest_field(facts, updates, "name", "name") or max(
        roles,
        key=lambda row: (row[1].visible_from_cp, row[0]),
        default=None,
    )
    name = chosen[1].value if chosen else None
    alias_update = max(
        ((i, r) for i, r in updates if r.field == "aliases"),
        key=lambda row: row[0],
        default=None,
    )
    cutoff = alias_update[0] if alias_update else -1
    aliases = (
        {v: _effective_position(*alias_update) for v in alias_update[1].value}
        if alias_update
        else {}
    )
    for i, fact in facts:
        if fact.kind in {"name", "alias", "designation"} and i > cutoff:
            aliases[fact.value] = _effective_position(i, fact)
    described = _latest_field(facts, updates, "description", "description")
    return {
        "name": name,
        "name_position": _effective_position(*chosen) if chosen else (-1, -1),
        "aliases": aliases,
        "alias_reset_index": cutoff,
        "description": described[1].value if described else "",
        "description_defined": described is not None,
        "description_position": _effective_position(*described) if described else (-1, -1),
    }


def prepare_profile_updates(character, version, updates: tuple[IdentityProfileUpdate, ...]) -> str:
    read_identity_records(character, version)
    prepared = BookCharacter(
        id=character.id,
        book_version_id=version.id,
        identity_facts_json=character.identity_facts_json or "[]",
        version=1,
    )
    append_identity_records(prepared, version, updates)
    return prepared.identity_facts_json


def prepare_merged_identity_facts(
    source: BookCharacter,
    target: BookCharacter,
    version: BookVersion,
    *,
    link: IdentityLink,
    original: OriginalIdentitySnapshot,
) -> str:
    """Keep original proof and reveal time; gate transferred facts on every merge."""
    checked_link = IdentityLink.model_validate(link.model_dump())
    if checked_link.source_id != source.id or checked_link.target_id != target.id:
        raise ValueError("Identity merge link does not match its source and target")
    if checked_link.visible_from_cp > version.canonical_length_cp:
        raise ValueError("Identity link is outside the immutable original")
    source_facts = read_identity_records(source, version)
    target_facts = read_identity_records(target, version)
    transferred = tuple(
        fact.model_copy(update={"identity_links": (*fact.identity_links, checked_link)})
        for fact in source_facts
    )
    prepared = BookCharacter(
        id=target.id,
        book_version_id=target.book_version_id,
        identity_facts_json="[]",
        version=1,
    )
    append_identity_records(prepared, version, (*target_facts, *transferred), original=original)
    return prepared.identity_facts_json


def identity_facts_fingerprint(character: BookCharacter, version: BookVersion) -> str:
    values = [
        FACTS_VERSION,
        version.id,
        character.id,
        [f.model_dump(mode="json") for f in read_identity_records(character, version)],
    ]
    return hashlib.sha256(
        json.dumps(values, ensure_ascii=False, sort_keys=True).encode("utf8")
    ).hexdigest()
