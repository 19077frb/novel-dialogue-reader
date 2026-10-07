"""Opt-in prompt-side reference enums, not provider-enforced JSON Schema."""

from __future__ import annotations

import hashlib
import json

from .compact import CompactTask
from .evidence_view import NonblankEvidenceViewAdapter, evidence_view_fingerprint
from .turn_frames import FRAME_EXAMPLE, FrameOutput

ENUMERATED_VIEW_VERSION = "actual-reference-enums-1"
SCENE_SENTENCE = "时间、地点或交谈群体明显改变时在breaks给对应G编号；"
ACTUAL_SCENE_SENTENCE = "时间、地点或交谈群体明显改变时在breaks给gap_next_quote中的实际编号；"
UNKNOWN_EXAMPLE = (
    '{"labels":[{"q":"Q1","kind":"speech","character":null,'
    '"basis":"insufficient","evidence":[],"addressee":null,"addressee_evidence":[]},'
    '{"q":"Q2","kind":"thought"}],"breaks":[],"new_characters":[],"needs_context":[]}'
)
ENUMERATED_VIEW_POLICY = (
    "证据引用的允许值由本次schema的OriginalEvidenceReference列出，"
    "它与实际context.ref完全相同。只能选已提供编号，不能按对白编号计算G编号。"
    "breaks只用有后继的gap_next_quote编号，场景编号不是人物依据。"
    "允许值仅证明编号已提供，不证明证据支持人物；仍须核对原文语义。"
)


def enumerated_view_fingerprint(source_fingerprint: str) -> str:
    return hashlib.sha256(
        json.dumps(
            [
                ENUMERATED_VIEW_VERSION,
                ENUMERATED_VIEW_POLICY,
                SCENE_SENTENCE,
                ACTUAL_SCENE_SENTENCE,
                UNKNOWN_EXAMPLE,
                FrameOutput.model_json_schema(),
                evidence_view_fingerprint(source_fingerprint),
            ],
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()


def _allowed_items(refs: list[str]) -> dict:
    # An empty enum is invalid JSON Schema. A false items schema still allows
    # an empty array, while prohibiting any element when no reference exists.
    return {"type": "string", "enum": refs} if refs else {"not": {}}


class EnumeratedEvidenceViewAdapter(NonblankEvidenceViewAdapter):
    """Change only the prompt; all original local acceptance guards remain."""

    def __init__(self, adapter, task: CompactTask):
        super().__init__(adapter, task)
        original_schema = json.dumps(FrameOutput.model_json_schema(), ensure_ascii=False)
        if (
            self.system.count(original_schema) != 1
            or self.system.count(FRAME_EXAMPLE) != 1
            or self.system.count(SCENE_SENTENCE) != 1
        ):
            raise ValueError("Enumerated view requires the declared frame prompt")
        schema = FrameOutput.model_json_schema()
        refs = [row["ref"] for row in self.view["context"] if "ref" in row]
        boundaries = [ref for ref, successor in self.view["gap_next_quote"].items() if successor]
        schema["$defs"]["OriginalEvidenceReference"] = _allowed_items(refs)
        for model, field in (
            ("FrameSpeech", "evidence"),
            ("FrameSpeech", "addressee_evidence"),
            ("DiscoveredCharacter", "evidence"),
        ):
            schema["$defs"][model]["properties"][field]["items"] = {
                "$ref": "#/$defs/OriginalEvidenceReference"
            }
        schema["properties"]["breaks"]["items"] = _allowed_items(boundaries)
        self.prompt_schema = schema
        self.system = (
            self.system.replace(original_schema, json.dumps(schema, ensure_ascii=False))
            .replace(FRAME_EXAMPLE, UNKNOWN_EXAMPLE)
            .replace(SCENE_SENTENCE, ACTUAL_SCENE_SENTENCE)
            + "\n\n"
            + ENUMERATED_VIEW_POLICY
        )
