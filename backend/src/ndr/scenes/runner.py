"""单窗口推理流程（T09）：组装提示 → 调用适配器 → 校验 → 有限重试 → 应用。

- 适配器本身不重试（T07 决策）：重试次数与原因由这里按 :class:`RetryPolicy` 决定，
  每次尝试都单独记录（T10 会落成 `inference_runs` 并计入用量）。
- 只有 `INVALID_MODEL_OUTPUT` 会触发一次带纠错提示的重复请求；鉴权/限流/超时不在这里重试。
- 校验失败或锁定冲突都不会写入任何自动结果。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from ..llm.adapter import ProviderAdapter
from ..llm.errors import InvalidModelOutput, ProviderError
from ..llm.prompts import LABELING_PROMPT_VERSION, build_labeling_messages
from ..llm.validation import RetryPolicy, parse_and_validate
from .engine import WindowApplication, apply_window
from .state import SceneState

LABELING_MAX_TOKENS = 800


@dataclass
class WindowRunResult:
    application: WindowApplication
    attempts: int = 0
    usage_records: list[dict[str, Any]] = field(default_factory=list)
    raw_outputs: list[str] = field(default_factory=list)
    adapter: str = "none"
    prompt_version: str = LABELING_PROMPT_VERSION
    error_code: str | None = None

    @property
    def ok(self) -> bool:
        return self.application.validation_ok

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "attempts": self.attempts,
            "usage": self.usage_records,
            "adapter": self.adapter,
            "prompt_version": self.prompt_version,
            "error_code": self.error_code,
            "application": self.application.as_dict(),
        }


def _reference_aliases(window) -> tuple[dict[str, str], dict[str, str]]:  # noqa: ANN001
    """建立窗口内短引用；返回 stable→alias 与 alias→stable。"""

    stable_to_alias: dict[str, str] = {}
    for index, quote_id in enumerate(window.target_quote_ids, start=1):
        stable_to_alias[str(quote_id)] = f"Q{index}"
    gap_index = evidence_index = 0
    for fragment in window.fragments:
        ref = str(fragment.fragment_id)
        if ref in stable_to_alias:
            continue
        if fragment.kind.value in {"inner_gap", "outer_gap"}:
            gap_index += 1
            stable_to_alias[ref] = f"G{gap_index}"
        else:
            evidence_index += 1
            stable_to_alias[ref] = f"E{evidence_index}"
    return stable_to_alias, {alias: stable for stable, alias in stable_to_alias.items()}


def _restore_output_references(raw: Any, window) -> Any:  # noqa: ANN001
    """把模型使用的 Q/G/E 短引用还原为数据库稳定 ID。"""

    if not isinstance(raw, Mapping):
        return raw
    _stable_to_alias, alias_to_stable = _reference_aliases(window)

    def restore(value: Any) -> Any:
        return alias_to_stable.get(str(value), value) if isinstance(value, str) else value

    payload = dict(raw)
    field_map = {
        "scene_updates": ("after_gap_id", "starts_at_quote_id"),
        "gap_decisions": ("gap_id",),
        "new_speakers": ("first_quote_id",),
        "labels": ("quote_id",),
        "identity_proposals": (),
    }
    for collection, scalar_fields in field_map.items():
        normalized: list[Any] = []
        for item in payload.get(collection, []) or []:
            if not isinstance(item, Mapping):
                normalized.append(item)
                continue
            record = dict(item)
            for field_name in scalar_fields:
                if field_name in record:
                    record[field_name] = restore(record[field_name])
            if isinstance(record.get("evidence_refs"), list):
                record["evidence_refs"] = [restore(ref) for ref in record["evidence_refs"]]
            normalized.append(record)
        payload[collection] = normalized
    if isinstance(payload.get("needs_context"), list):
        payload["needs_context"] = [restore(ref) for ref in payload["needs_context"]]
    return payload


def _messages_for(
    *,
    window,  # noqa: ANN001 - ProcessingWindow
    state: SceneState,
    locked_summary: str | None,
    correction: str | None = None,
) -> list[dict[str, str]]:
    stable_to_alias, _alias_to_stable = _reference_aliases(window)

    def alias(ref: str) -> str:
        return stable_to_alias.get(ref, ref)

    context_records = [
        {
            "ref": alias(fragment.fragment_id),
            "kind": fragment.kind.value,
            "start_cp": fragment.start_cp,
            "end_cp": fragment.end_cp,
            "text": fragment.text,
        }
        for fragment in window.fragments
    ]
    speaker_records = [
        {
            "speaker_ref": slot.display_label,
            "canonical_name": slot.canonical_name or None,
            "description": slot.description or "未说明",
            "first_quote_id": stable_to_alias.get(slot.first_quote_id),
            "evidence_refs": [alias(ref) for ref in slot.evidence_refs if ref in stable_to_alias],
        }
        for slot in state.participants
    ]
    messages = build_labeling_messages(
        context_lines=(),
        context_records=context_records,
        target_ids=[alias(ref) for ref in window.target_quote_ids],
        gap_ids=[
            alias(fragment.fragment_id)
            for fragment in window.fragments
            if fragment.kind.value in {"inner_gap", "outer_gap"}
        ],
        scene_ref=state.scene_ref,
        speaker_refs=[slot.display_label for slot in state.participants],
        speaker_records=speaker_records,
        known_characters=[
            {"name": name, "description": description}
            for name, description in state.known_characters.items()
        ],
        evidence_ids=[alias(ref) for ref in window.fragment_ids],
        locked_summary=locked_summary or state.prompt_state(max_chars=600),
    )
    if correction:
        for stable, short in stable_to_alias.items():
            correction = correction.replace(stable, short)
        messages = [
            *messages,
            {
                "role": "user",
                "content": (
                    "上一次输出无效：" + correction + "。请重新只输出符合 schema 的 JSON 对象，"
                    "不要包含任何解释或额外文本。"
                ),
            },
        ]
    return messages


async def run_window(
    session: Session,
    *,
    adapter: ProviderAdapter,
    window,  # noqa: ANN001 - ProcessingWindow
    state: SceneState,
    book_version_id: str,
    quote_positions: Mapping[str, tuple[int, int]],
    gap_positions: Mapping[str, int] | None = None,
    evidence_positions: Mapping[str, int] | None = None,
    locked_quote_ids: set[str] | None = None,
    locked_group_ids: set[str] | None = None,
    dependency_hash: str | None = None,
    locked_summary: str | None = None,
    cold_start: bool = True,
    retry_policy: RetryPolicy | None = None,
    run_id: str | None = None,
) -> WindowRunResult:
    """跑一个窗口：最多 ``1 + max_format_retries`` 次调用，然后在同一事务里应用结果。"""

    policy = retry_policy or RetryPolicy()
    result = WindowRunResult(
        application=WindowApplication(
            window_id=window.window_id,
            scene_state=state,
            validation_ok=False,
        ),
        adapter=getattr(adapter, "name", "none"),
    )
    correction: str | None = None
    attempt = 0

    while True:
        attempt += 1
        result.attempts = attempt
        payload = {
            "messages": _messages_for(
                window=window, state=state, locked_summary=locked_summary, correction=correction
            ),
            "max_tokens": LABELING_MAX_TOKENS,
            "json_object": True,
            "target_quote_ids": list(window.target_quote_ids),
        }
        try:
            raw = _restore_output_references(await adapter.generate_labels(payload), window)
        except ProviderError as exc:
            result.error_code = exc.code.value
            result.application.warnings.append(f"{exc.code.value}: {exc.message}")
            return result

        usage = raw.get("_usage") if isinstance(raw, Mapping) else None
        if usage:
            result.usage_records.append(dict(usage))
        text = json.dumps(raw, ensure_ascii=False) if isinstance(raw, Mapping) else str(raw)
        result.raw_outputs.append(text)

        # 适配器用 "_" 前缀返回旁路信息（usage 等）；校验前必须剥离，否则会被 extra=forbid 拒绝。
        payload_for_validation = (
            {key: value for key, value in raw.items() if not str(key).startswith("_")}
            if isinstance(raw, Mapping)
            else raw
        )
        report = parse_and_validate(payload_for_validation, _targets_for(window, state))
        if report.ok and report.output is not None:
            result.application = apply_window(
                session,
                book_version_id=book_version_id,
                window=window,
                output=report.output,
                state=state,
                quote_positions=quote_positions,
                gap_positions=gap_positions or {},
                evidence_positions=evidence_positions or {},
                locked_quote_ids=locked_quote_ids or set(),
                locked_group_ids=locked_group_ids or set(),
                dependency_hash=dependency_hash or window.dependency_hash,
                run_id=run_id,
            )
            return result

        result.application.validation_ok = False
        result.application.validation_codes = report.error_codes
        result.application.warnings.extend(report.messages[:5])
        error = InvalidModelOutput("；".join(report.messages[:3]) or "输出不符合 schema")
        result.error_code = error.code.value
        if not policy.should_retry(error, retries_used=attempt - 1):
            return result
        correction = error.message


def _targets_for(window, state: SceneState):  # noqa: ANN001, ANN202
    from ..llm.validation import LabelingTargets

    return LabelingTargets(
        quote_ids=tuple(window.target_quote_ids),
        gap_ids=tuple(
            fragment.fragment_id
            for fragment in window.fragments
            if fragment.kind.value in {"inner_gap", "outer_gap"}
        ),
        scene_refs=(state.scene_ref,),
        speaker_refs=tuple(slot.display_label for slot in state.participants),
        evidence_ids=tuple(window.fragment_ids),
    )
