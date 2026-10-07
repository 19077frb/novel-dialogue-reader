"""Isolated literal-context ablation; not a production context policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace

from .compact import CompactTask
from .evidence import EvidenceIndex

BOUNDARY_CONTEXT_VERSION = "literal-boundary-context-1"


@dataclass(frozen=True)
class PreparedContext:
    task: CompactTask
    source_fingerprint: str
    margin: int
    anchor_limit: int

    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(
                [
                    BOUNDARY_CONTEXT_VERSION,
                    self.source_fingerprint,
                    self.margin,
                    self.anchor_limit,
                    self.task.fingerprint(),
                ],
                ensure_ascii=False,
            ).encode()
        ).hexdigest()


def literal_boundary_context(
    source: CompactTask, text: str, *, margin: int = 500, anchor_limit: int = 8
) -> PreparedContext:
    """Keep targets/identities, restore literal gaps and bounded visible edges.

    Only fresh windows are supported. Referenced hints/facts/relay cannot be
    silently dropped or left pointing at the old evidence namespace.
    """
    if any(not isinstance(v, int) or isinstance(v, bool) for v in (margin, anchor_limit)):
        raise ValueError("Explicit integer context limits required")
    if margin < 0 or not 0 <= anchor_limit <= 16:
        raise ValueError("Invalid bounded context limits")
    if source.evidence_hints or source.identity_facts or source.relay:
        raise ValueError("Referenced context attachments require their own remapping policy")
    horizon = source.visible_horizon_cp
    if horizon is not None and (
        not isinstance(horizon, int) or isinstance(horizon, bool) or not 0 <= horizon <= len(text)
    ):
        raise ValueError("Context horizon must be within the original snapshot")
    limit = len(text) if horizon is None else horizon
    for row in source.context:
        a, b = row["start_cp"], row["end_cp"]
        if (
            any(not isinstance(v, int) or isinstance(v, bool) for v in (a, b))
            or not 0 <= a < b <= limit
            or row["text"] != text[a:b]
        ):
            raise ValueError("Source evidence must match the visible original exactly")
    positions = {row["ref"]: row for row in source.context}
    rows = [positions[q] for q in source.quote_ids]
    if not rows or any(row["kind"] != "target_quote" for row in rows):
        raise ValueError("Explicit original quote targets required")
    spans = tuple((row["start_cp"], row["end_cp"]) for row in rows)
    rebuilt = EvidenceIndex(text, ()).task(
        spans,
        margin=margin,
        reading_mode=source.reading_mode,
        horizon=horizon,
    )
    start = min(row["start_cp"] for row in rebuilt.context)
    end = max(row["end_cp"] for row in rebuilt.context)
    anchors = sorted(
        {
            (row["start_cp"], row["end_cp"])
            for row in source.context
            if row["kind"] == "overlap" and (row["end_cp"] <= start or row["start_cp"] >= end)
        },
        key=lambda span: (min(abs(start - span[1]), abs(span[0] - end)), span),
    )[:anchor_limit]
    references = dict(rebuilt.references)
    context = list(rebuilt.context)
    for index, (a, b) in enumerate(sorted(anchors), 1):
        ref = f"E{index}"
        references[ref] = f"evidence:{a}:{b}"
        context.append(
            {"ref": ref, "kind": "overlap", "start_cp": a, "end_cp": b, "text": text[a:b]}
        )
    task = replace(
        rebuilt,
        references=references,
        context=tuple(context),
        candidates=source.candidates,
        pov_ref=source.pov_ref,
        scene_ref=source.scene_ref,
        evidence_hints=(),
        identity_facts=(),
        relay=(),
    )
    return PreparedContext(task, source.fingerprint(), margin, anchor_limit)
