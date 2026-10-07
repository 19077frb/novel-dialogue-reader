"""Freeze identity records without querying live metadata during rendering.

This internal snapshot is not yet an EPUB interchange contract. Original
coordinates must be rebound to verified imported text before restoration.
"""

import json

from ..characters.facts import (
    read_identity_records,
    visible_identity_profile,
    visible_identity_records,
)
from ..storage.models import BookCharacter, BookVersion

IDENTITY_SNAPSHOT_VERSION = "export-identities-1"


def freeze_identity(character: BookCharacter, version: BookVersion, *, horizon: int | None):
    """Return an independent JSON value; never mutate a character or infer facts."""
    records = read_identity_records(character, version)
    history = json.loads(character.presentation_history_json or "[]")
    if not isinstance(history, list):
        raise ValueError("Identity presentation history must be a list")
    for row in history:
        if (
            not isinstance(row, dict)
            or set(row) != {"cp", "identity", "name", "description"}
            or type(row.get("cp")) is not int
            or not 0 <= row["cp"] <= version.canonical_length_cp
            or not all(isinstance(row.get(k), str) for k in ("identity", "name", "description"))
        ):
            raise ValueError("Identity presentation history is invalid")

    if horizon is None:
        aliases = json.loads(character.aliases_json or "[]")
        if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
            raise ValueError("Identity aliases must be strings")
        name = character.canonical_name or ""
        description = character.description or ""
    else:
        # Do not fall back to final aliases or final metadata for a legacy row.
        records = visible_identity_records(character, version, horizon=horizon)
        history = [row for row in history if row["cp"] <= horizon]
        if not records and not history:
            return None
        profile = visible_identity_profile(character, version, horizon=horizon)
        presentation = (
            max(enumerate(history), key=lambda r: (r[1]["cp"], r[0]))[1] if history else {}
        )
        name = profile["name"] or presentation.get("name", "")
        aliases = list(profile["aliases"])
        has_description = any(
            r.kind == "description" or (r.kind == "profile_update" and r.field == "description")
            for r in records
        )
        description = (
            profile["description"] if has_description else presentation.get("description", "")
        )

    first_seen = character.first_seen_cp
    if horizon is not None and first_seen is not None and first_seen > horizon:
        first_seen = None
    return {
        "identity": f"character:{character.id}",
        "name": name,
        "aliases": aliases,
        "description": description,
        "source": character.source.value,
        "user_confirmed": character.user_confirmed,
        "name_locked": character.name_locked,
        "confirmation_source": character.confirmation_source,
        "first_seen_cp": first_seen,
        "preferred_color_index": character.preferred_color_index,
        "presentation_history": [dict(row) for row in history],
        "records": [record.model_dump(mode="json") for record in records],
    }
