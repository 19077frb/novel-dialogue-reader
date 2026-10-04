"""Full-text experimental view with no citable IDs on whitespace-only rows."""

from __future__ import annotations

import hashlib
import json

from ..llm.errors import InvalidModelOutput
from .compact import CompactTask
from .grounded_frames import GroundedTurnFrameAdapter, grounding_fingerprint
from .turn_frames import FrameOutput, FrameSpeech

EVIDENCE_VIEW_VERSION = "nonblank-reference-view-1"
EVIDENCE_VIEW_POLICY = """
context中没有ref的条目是保留的正文间隔，不是可引用证据，不要推测或生成它的编号。
只引用context实际列出的ref；诊断中的编号也不能代替原文证据。
没有可用的身份依据时保持人物未知，不把相邻对白或无关叙述当成证明。
boundary_ref中的B编号仅用于breaks场景边界，不能用于任何人物或受话证据。
gap_next_quote保留场景间隔，不为没有提供编号的空白自行创建引用。
""".strip()


def evidence_view_fingerprint(source_fingerprint: str) -> str:
    return hashlib.sha256(
        json.dumps(
            [
                EVIDENCE_VIEW_VERSION,
                EVIDENCE_VIEW_POLICY,
                grounding_fingerprint(source_fingerprint),
            ],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def nonblank_evidence_view(task: CompactTask) -> tuple[dict, frozenset[str], dict[str, str]]:
    """Keep original characters, positions and order; hide only blank-row IDs."""
    data = json.loads(task.messages()[1]["content"])
    if any(not isinstance(row.get("text"), str) for row in data["context"]):
        raise ValueError("Evidence view requires original text on every context row")
    hidden = frozenset(row["ref"] for row in data["context"] if not row["text"].strip())
    if hidden.intersection(task.quote_ids):
        raise ValueError("Evidence view cannot hide a target")
    if any(row.get("ref") in hidden for row in (*task.evidence_hints, *task.relay)):
        raise ValueError("Evidence view cannot silently remove a hint or relay reference")
    boundary_map = {}
    for row in data["context"]:
        if row["ref"] in hidden:
            original = row["ref"]
            del row["ref"]
            row["referenceable"] = False
            if original in data["gap_next_quote"]:
                ref = f"B{len(boundary_map) + 1}"
                while ref in task.references or ref in boundary_map:
                    ref += "b"
                row["boundary_ref"] = ref
                boundary_map[ref] = original
    renamed = {original: ref for ref, original in boundary_map.items()}
    data["gap_next_quote"] = {
        renamed.get(ref, ref): successor for ref, successor in data["gap_next_quote"].items()
    }
    return data, hidden, boundary_map


class NonblankEvidenceViewAdapter(GroundedTurnFrameAdapter):
    """Actual view enters the journal; guessed hidden IDs are always rejected."""

    def __init__(self, adapter, task: CompactTask):
        super().__init__(adapter, task)
        self.view, self.hidden_references, self.boundary_map = nonblank_evidence_view(self.task)
        self.system += "\n\n" + EVIDENCE_VIEW_POLICY

    def prepare_request(self, request: dict) -> dict:
        # The base adapter has already verified the immutable original task.
        request["messages"][1]["content"] = json.dumps(self.view, ensure_ascii=False)
        return request

    def compile_payload(self, payload: dict) -> tuple[dict, list[dict]]:
        proposed = FrameOutput.model_validate(payload)
        evidence = set()
        for person in proposed.new_characters:
            evidence.update(person.evidence)
        for label in proposed.labels:
            if isinstance(label, FrameSpeech):
                evidence.update(label.evidence)
                evidence.update(label.addressee_evidence)
        hidden = evidence.intersection(self.hidden_references | self.boundary_map.keys())
        hidden.update(set(proposed.breaks).intersection(self.hidden_references))
        if hidden:
            raise InvalidModelOutput(
                "Evidence view did not provide these reference IDs: " + ",".join(sorted(hidden))
            )
        translated = proposed.model_dump(mode="json")
        translated["breaks"] = [self.boundary_map.get(ref, ref) for ref in proposed.breaks]
        return super().compile_payload(translated)
