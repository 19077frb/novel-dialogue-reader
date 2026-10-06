"""Program-only identity colors shared by the directory, reader and exports."""

from sqlalchemy import select

from ..storage.models import BookCharacter, Quote, Scene, SpeakerGroup
from .visibility import visible_value

BASE_COLORS = (
    "#2f6feb", "#d97706", "#16a34a", "#dc2626", "#7c3aed", "#0891b2", "#ca8a04", "#db2777",
    "#78512d", "#00695c", "#3949ab", "#8e2458", "#596c12", "#48687c", "#77557a", "#414b55",
)
DARK_COLORS = (
    "#82b1ff", "#ffb74d", "#81c784", "#ff8a80", "#b39ddb", "#80deea", "#e6d267", "#f48fb1",
    "#bc9473", "#80cbc4", "#9fa8da", "#d98aaf", "#b6c66c", "#9ab5c8", "#c3a3c7", "#b0bec5",
)


def color_hue(index: int) -> int:
    return int(((index - len(BASE_COLORS) + 1) * 137.508) % 360 * 1000)


def color_css(index: int, *, dark: bool = False) -> str:
    if index < len(BASE_COLORS):
        return (DARK_COLORS if dark else BASE_COLORS)[index]
    # No modulo palette reuse: retain fractional hues for large casts.
    return f"hsl({color_hue(index) / 1000:g}, 65%, {72 if dark else 38}%)"


def allocate_colors(entries):
    reserved = {preferred for _, preferred in entries if preferred is not None and preferred >= 0}
    assigned, used = {}, set()
    next_color = 0
    for identity, preferred in entries:
        if identity in assigned:
            continue
        if preferred is not None and preferred >= 0 and preferred not in used:
            color = preferred
        else:
            while next_color in used or next_color in reserved:
                next_color += 1
            color = next_color
        assigned[identity] = color
        used.add(color)
    return assigned


def color_projection(session, version_id, horizon=None, *, characters=None):
    """Batch queries only; no annotation scan or per-character lookup."""
    groups = list(session.execute(select(
        SpeakerGroup.id, SpeakerGroup.version, SpeakerGroup.scene_id, SpeakerGroup.first_quote_id,
        SpeakerGroup.character_id, SpeakerGroup.canonical_name, SpeakerGroup.description,
        SpeakerGroup.presentation_history_json, Quote.start_cp.label("first_start_cp"),
        BookCharacter.preferred_color_index,
    ).join(Scene, SpeakerGroup.scene_id == Scene.id)
        .outerjoin(Quote, SpeakerGroup.first_quote_id == Quote.id)
        .outerjoin(BookCharacter, SpeakerGroup.character_id == BookCharacter.id)
        .where(Scene.book_version_id == version_id)))
    groups.sort(key=lambda group: (
        group.first_start_cp if group.first_start_cp is not None else 2**63 - 1, group.id,
    ))
    presentations = {group.id: visible_value(group.presentation_history_json, horizon, fallback={
        "identity": (f"character:{group.character_id}" if group.character_id else
                     f"name:{group.canonical_name.casefold()}" if group.canonical_name else
                     f"group:{group.id}"),
        "private_identity": f"group:{group.id}",
        "name": group.canonical_name or "", "description": group.description or "",
    }) for group in groups}
    if characters is None:
        character_colors = list(session.execute(
            select(BookCharacter.id, BookCharacter.preferred_color_index,
                   BookCharacter.presentation_history_json)
            .where(BookCharacter.book_version_id == version_id)
            .order_by(BookCharacter.first_seen_cp, BookCharacter.created_at, BookCharacter.id),
        ))
    else:
        character_colors = [(row.id, row.preferred_color_index, row.presentation_history_json)
                            for row in sorted(
            characters, key=lambda row: (
                row.first_seen_cp if row.first_seen_cp is not None else -1, row.created_at, row.id,
            ),
        )]
    # Imported historical identities may differ from the current character ID.
    # Only an exact match of the two *visible* histories may inherit its color;
    # a later merge must not paint unrelated earlier voices with the target color.
    visible_characters = {key: visible_value(raw, horizon, fallback={
        "identity": f"character:{key}", "private_identity": f"private-character:{key}",
        "name": "", "description": "",
    })["identity"] for key, _preferred, raw in character_colors}
    entries = [(presentations[group.id]["identity"], group.preferred_color_index
                if presentations[group.id]["identity"] == visible_characters.get(
                    group.character_id, f"character:{group.character_id}")
                else None) for group in groups]
    entries.extend((f"character:{key}", preferred) for key, preferred, _raw in character_colors)
    return groups, presentations, allocate_colors(entries)
