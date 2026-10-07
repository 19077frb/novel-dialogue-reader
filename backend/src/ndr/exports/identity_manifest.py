"""Optional EPUB identity ledger. Invalid identity blocks never become guesses."""

import hashlib
import json
import re
from collections import Counter
from typing import Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from sqlalchemy import select

from ..characters.facts import (
    CharacterIdentityFact,
    IdentityProfileUpdate,
    OriginalIdentitySnapshot,
    append_identity_records,
)
from ..domain.enums import CharacterSource
from ..storage.models import BookCharacter, Chapter, ContentNode
from .identity_anchors import ExportAnchors, ImportAnchors

LEDGER_VERSION = "epub-identity-ledger-1"


class ManifestCharacter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity: str = Field(min_length=11, max_length=170, pattern=r"^character:.+")
    name: str = Field(max_length=128)
    aliases: list[str] = Field(max_length=10000)
    description: str = Field(max_length=512)
    source: CharacterSource
    user_confirmed: StrictBool
    name_locked: StrictBool
    confirmation_source: Literal["model", "manual", "automatic", "imported", "user"]
    first_seen_cp: dict | None
    preferred_color_index: StrictInt | None = Field(ge=0)
    presentation_history: list[dict] = Field(max_length=10000)
    records: list[dict] = Field(max_length=10000)


def build_identity_manifest(payload, rendered, text):
    frozen = payload.get("character_snapshot")
    if not isinstance(frozen, dict):
        return None
    if frozen.get("canonical_sha256") != hashlib.sha256(text.encode("utf8")).hexdigest():
        raise ValueError("Frozen identity original hash does not match")
    anchors = ExportAnchors(rendered, text, payload.get("chapter_bounds", {}))
    if len(anchors.blocks) > 100000:
        return {
            "schema_version": LEDGER_VERSION,
            "original_sha256": frozen["canonical_sha256"],
            "blocks": [],
            "characters": [],
            "omitted_characters": len(frozen.get("characters", [])),
        }
    characters = []
    omitted = 0
    total_records = 0
    for row in frozen.get("characters", []):
        try:
            converted = dict(row)
            converted["first_seen_cp"] = (
                anchors.point(row["first_seen_cp"]) if row["first_seen_cp"] is not None else None
            )
            converted["presentation_history"] = [
                dict(h, cp=anchors.point(h["cp"])) for h in row["presentation_history"]
            ]
            records = []
            for raw in row["records"]:
                record = dict(raw, visible_from_cp=anchors.point(raw["visible_from_cp"]))
                record["identity_links"] = [
                    dict(link, visible_from_cp=anchors.point(link["visible_from_cp"]))
                    for link in raw["identity_links"]
                ]
                if "evidence_spans" in raw:
                    record["evidence_spans"] = [
                        anchors.evidence(a, b) for a, b in raw["evidence_spans"]
                    ]
                records.append(record)
            converted["records"] = records
            if len(characters) >= 10000 or total_records + len(records) > 100000:
                raise ValueError("Identity export record limit exceeded")
            characters.append(ManifestCharacter.model_validate(converted).model_dump(mode="json"))
            total_records += len(records)
        except (ValueError, KeyError, TypeError):
            # Losing one alias reset or merge gate changes the meaning of other
            # records. Keep the block atomic, rather than retaining only positives.
            omitted += 1
    return {
        "schema_version": LEDGER_VERSION,
        "original_sha256": frozen["canonical_sha256"],
        "blocks": [b.descriptor() for b in anchors.blocks],
        "characters": characters,
        "omitted_characters": omitted,
    }


def history_identity(identity, ids, version):
    if identity in ids:
        return f"character:{ids[identity]}"
    return "imported-history:" + str(uuid5(NAMESPACE_URL, f"{version.id}:{identity}"))


def _records(row, anchors, text, version, ids, old_sha):
    records = []
    for raw in row.records:
        if raw.get("canonical_sha256") != old_sha:
            raise ValueError("Identity original hash is inconsistent")
        restored = dict(
            raw,
            canonical_sha256=version.canonical_sha256,
            visible_from_cp=anchors.point(raw["visible_from_cp"]),
        )
        links = []
        for link in raw.get("identity_links", []):
            remapped = {}
            for field in ("source_id", "target_id"):
                value = link[field]
                if not isinstance(value, str) or not value.strip() or len(value) > 160:
                    raise ValueError("Identity merge identifier is invalid")
                key = f"character:{value}"
                remapped[field] = ids.get(key) or str(uuid5(NAMESPACE_URL, f"{version.id}:{key}"))
            links.append(
                dict(link, **remapped, visible_from_cp=anchors.point(link["visible_from_cp"]))
            )
        restored["identity_links"] = links
        if raw.get("kind") != "profile_update":
            restored["evidence_spans"] = [
                anchors.evidence(proof, text) for proof in raw["evidence_spans"]
            ]
        model = (
            IdentityProfileUpdate if raw.get("kind") == "profile_update" else CharacterIdentityFact
        )
        records.append(model.model_validate(restored))
    return tuple(records)


def restore_identity_manifest(session, version, ledger, binding, text):
    stats = {"identity_characters": 0, "identity_rejected": 0, "identity_manifest_invalid": 0}
    if ledger is None:
        return {}, stats, None
    if (
        not isinstance(ledger, dict)
        or ledger.get("schema_version") != LEDGER_VERSION
        or not isinstance(ledger.get("characters"), list)
        or len(ledger["characters"]) > 10000
        or not isinstance(ledger.get("original_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", ledger["original_sha256"])
    ):
        stats["identity_manifest_invalid"] = 1
        return {}, stats, None
    try:
        nodes = session.scalars(
            select(ContentNode)
            .join(Chapter)
            .where(
                Chapter.book_version_id == version.id,
            )
            .order_by(Chapter.ordinal, ContentNode.start_cp, ContentNode.ordinal)
        )
        anchors = ImportAnchors(ledger.get("blocks"), binding, nodes, text)
        original = OriginalIdentitySnapshot(version.id, version.canonical_sha256, text)
    except (ValueError, KeyError, TypeError):
        stats["identity_rejected"] = len(ledger["characters"])
        stats["identity_manifest_invalid"] = 1
        return {}, stats, None
    keys = [
        r.get("identity")
        for r in ledger["characters"]
        if isinstance(r, dict) and isinstance(r.get("identity"), str)
    ]
    counts = Counter(keys)
    ids = {key: str(uuid4()) for key in keys if counts[key] == 1}
    prepared = {}
    total_records = 0
    for raw in ledger["characters"]:
        try:
            row = ManifestCharacter.model_validate(raw)
            if counts[row.identity] != 1:
                raise ValueError("Duplicate identity identifier")
            total_records += len(row.records)
            if total_records > 100000:
                raise ValueError("Identity record limit exceeded")
            character = BookCharacter(
                id=ids[row.identity],
                book_version_id=version.id,
                temp_key="imported-ledger:"
                + hashlib.sha256(row.identity.encode()).hexdigest()[:24],
                canonical_name=row.name or None,
                aliases_json=json.dumps(row.aliases, ensure_ascii=False),
                description=row.description or None,
                source=row.source,
                user_confirmed=row.user_confirmed,
                name_locked=row.name_locked,
                confirmation_source=row.confirmation_source,
                first_seen_cp=(
                    anchors.point(row.first_seen_cp) if row.first_seen_cp is not None else None
                ),
                preferred_color_index=row.preferred_color_index,
                identity_facts_json="[]",
                version=1,
            )
            history = []
            for raw_history in row.presentation_history:
                if set(raw_history) != {"cp", "identity", "name", "description"} or not all(
                    isinstance(raw_history[k], str) for k in ("identity", "name", "description")
                ):
                    raise ValueError("Identity history is invalid")
                if (
                    not raw_history["identity"].strip()
                    or len(raw_history["identity"]) > 160
                    or len(raw_history["name"]) > 128
                    or len(raw_history["description"]) > 512
                ):
                    raise ValueError("Identity history fields exceed bounds")
                history.append(
                    dict(
                        raw_history,
                        cp=anchors.point(raw_history["cp"]),
                        identity=history_identity(raw_history["identity"], ids, version),
                    )
                )
            if not history:
                # No original reveal history means no invented initial knowledge.
                history = [
                    {
                        "cp": version.canonical_length_cp,
                        "identity": f"character:{character.id}",
                        "name": row.name,
                        "description": row.description,
                    }
                ]
            character.presentation_history_json = json.dumps(history, ensure_ascii=False)
            append_identity_records(
                character,
                version,
                _records(row, anchors, text, version, ids, ledger["original_sha256"]),
                original=original,
            )
            character.version = 1  # The imported version starts a new revision history.
            prepared[row.identity] = character
        except (ValueError, KeyError, TypeError):
            stats["identity_rejected"] += 1
    # A failed block cannot partially create a person or mutate another block.
    session.add_all(prepared.values())
    session.flush()
    stats["identity_characters"] = len(prepared)
    return prepared, stats, anchors
