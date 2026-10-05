"""Opt-in simplified grounding adapter; not a production task entry point."""

import hashlib
import json
from copy import deepcopy

from pydantic import ValidationError

from ndr.evaluation.compact import CompactOutput
from ndr.evaluation.enumerated_view import EnumeratedEvidenceViewAdapter
from ndr.evaluation.evidence_view import EVIDENCE_VIEW_POLICY
from ndr.evaluation.grounded_frames import GROUNDING_POLICY
from ndr.evaluation.turn_frames import BASE_EXAMPLE
from ndr.evaluation.type_basis_cues import TYPE_ACTIVITY_POLICY
from ndr.llm.errors import InvalidModelOutput, ProviderError, ProviderErrorKind

VERSION = "simple-grounded-compact-1"
EXAMPLE = (
    '{"labels":[{"q":"Q1","kind":"speech","character":null,'
    '"basis":"insufficient","evidence":[]},{"q":"Q2","kind":"thought"}],'
    '"breaks":[],"new_characters":[],"needs_context":[]}'
)


class SimpleGroundedCompactAdapter:
    def __init__(self, adapter, task):
        self.adapter = adapter
        self.task = deepcopy(task)
        self.original_messages = self.task.messages()
        self.guard = EnumeratedEvidenceViewAdapter(None, self.task)
        schema = CompactOutput.model_json_schema()
        refs = [r["ref"] for r in self.guard.view["context"] if "ref" in r]
        schema["$defs"]["OriginalEvidenceReference"] = (
            {"type": "string", "enum": refs} if refs else {"not": {}}
        )
        for name in ("Speech", "DiscoveredCharacter"):
            schema["$defs"][name]["properties"]["evidence"]["items"] = {
                "$ref": "#/$defs/OriginalEvidenceReference"
            }
        boundaries = [r for r, q in self.guard.view["gap_next_quote"].items() if q]
        schema["properties"]["breaks"]["items"] = (
            {"type": "string", "enum": boundaries} if boundaries else {"not": {}}
        )
        original_schema = json.dumps(CompactOutput.model_json_schema(), ensure_ascii=False)
        system = self.original_messages[0]["content"]
        assert system.count(original_schema) == 1 and system.count(BASE_EXAMPLE) == 1
        self.system = system.replace(
            original_schema, json.dumps(schema, ensure_ascii=False)
        ).replace(BASE_EXAMPLE, EXAMPLE)
        self.system = self.system.replace(
            "时间、地点或交谈群体明显改变时在breaks给对应G编号；",
            "时间、地点或交谈群体明显改变时在breaks给gap_next_quote中的实际编号；",
        )
        self.system += "\n\n" + "\n\n".join(
            (EVIDENCE_VIEW_POLICY, GROUNDING_POLICY, TYPE_ACTIVITY_POLICY)
        )
        self.schema = schema

    def compile_payload(self, payload):
        # Strictly reject on-wire extras before adding validation-only nulls.
        compact = CompactOutput.model_validate(payload)
        frames = compact.model_dump(mode="json")
        for row in frames["labels"]:
            if row["kind"] == "speech":
                row.update(addressee=None, addressee_evidence=[])
        stripped, _ = self.guard.compile_payload(frames)
        return stripped

    def fingerprint(self):
        return hashlib.sha256(
            json.dumps(
                [VERSION, self.task.fingerprint(), self.system, self.guard.view],
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()

    async def generate_labels(self, request):
        if request.get("messages", [])[:2] != self.original_messages:
            raise ValueError("Original task changed")
        prepared = deepcopy(request)
        prepared["messages"][0]["content"] = self.system
        prepared["messages"][1]["content"] = json.dumps(self.guard.view, ensure_ascii=False)
        raw = await self.adapter.generate_labels(prepared)
        candidate = deepcopy(raw)
        usage = candidate.pop("_usage", {"unknown": True, "total_tokens": None})
        try:
            compact = self.compile_payload(candidate)
        except (InvalidModelOutput, ValidationError) as exc:
            raise ProviderError(
                ProviderErrorKind.INVALID_OUTPUT,
                "Simple compact validation failed",
                details={"usage": usage, "compact_error": str(exc)[:1800]},
                retryable=False,
            ) from exc
        return {**compact, "_usage": usage}
