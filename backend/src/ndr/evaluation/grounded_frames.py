"""Opt-in nonblank evidence guard; not a semantic speaker verification rule."""

from __future__ import annotations

import hashlib
import json

from ..llm.errors import InvalidModelOutput
from .compact import CompactTask
from .turn_frames import (
    FrameOutput,
    FrameSpeech,
    TurnFrameAdapter,
    compile_turn_frames,
    turn_frame_fingerprint,
)

GROUNDING_VERSION = "nonblank-frame-evidence-1"
GROUNDING_POLICY = """
核查引用的实际文字，不是只检查编号存在：发声证据和受话对象证据不能引用仅空白的段落。
direct至少引用目标以外有文字的原文，明确支持这句实际由该人说出；
仅引用本句、空白、动作人物或场景转移不能冒充直接讲话证据。
如果依据是指代/同人延续，用coreference并核对指代；关联问答用response_link。
连续发言不一定换人，只有前一句存在不足以确定下一句说话者。
不能只改basis绕过问题：证据不支持人物时character=null、basis=insufficient、evidence=[]。
不改变原文、人物候选或类型，不输出推理；所有证据仍必须来自本次已发送context。
""".strip()


def grounding_fingerprint(source_fingerprint: str) -> str:
    return hashlib.sha256(
        json.dumps(
            [GROUNDING_VERSION, GROUNDING_POLICY, turn_frame_fingerprint(source_fingerprint)],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def compile_grounded_frames(payload: dict, task: CompactTask) -> tuple[dict, list[dict]]:
    # Preserve the original full contract before inspecting text. Never prune
    # bad references or repair a rejected identity to create an accepted result.
    stripped, frames = compile_turn_frames(payload, task)
    context = {row["ref"]: row for row in task.context}
    issues = []
    for label in FrameOutput.model_validate(payload).labels:
        if not isinstance(label, FrameSpeech):
            continue
        for field, refs in (
            ("evidence", label.evidence),
            ("addressee_evidence", label.addressee_evidence),
        ):
            blank = [ref for ref in refs if not context[ref]["text"].strip()]
            if blank:
                issues.append(f"{label.q}: {field} cites blank original text: {','.join(blank)}")
        if label.basis == "direct" and not any(ref != label.q for ref in label.evidence):
            issues.append(
                f"{label.q}: direct requires nonblank original evidence outside the target"
            )
    if issues:
        raise InvalidModelOutput(f"{len(issues)} evidence defects; " + "; ".join(issues[:12]))
    return stripped, frames


class GroundedTurnFrameAdapter(TurnFrameAdapter):
    """Reuse strict compilation, bounded diagnostics and known-usage handling."""

    def __init__(self, adapter, task: CompactTask):
        super().__init__(adapter, task)
        self.system += "\n\n" + GROUNDING_POLICY

    def compile_payload(self, payload: dict) -> tuple[dict, list[dict]]:
        return compile_grounded_frames(payload, self.task)
