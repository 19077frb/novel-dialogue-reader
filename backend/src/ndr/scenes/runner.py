"""单窗口推理流程：组装提示 → 调用适配器 → 校验 → 有限重试 → 应用。

- 适配器本身不重试：重试次数与原因由这里按 :class:`RetryPolicy` 决定，
  每次尝试都单独记录。
- 只有 `INVALID_MODEL_OUTPUT` 会触发一次带纠错提示的重复请求；鉴权/限流/超时不在这里重试。
- 校验失败或锁定冲突都不会写入任何自动结果。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
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
    compiler_fingerprint: str | None = None

    @property
    def ok(self) -> bool:
        return self.application.validation_ok

    def as_dict(self) -> dict[str, Any]:
        value = {
            "ok": self.ok,
            "attempts": self.attempts,
            "usage": self.usage_records,
            "adapter": self.adapter,
            "prompt_version": self.prompt_version,
            "error_code": self.error_code,
            "application": self.application.as_dict(),
        }
        if self.compiler_fingerprint is not None:
            value["compiler_fingerprint"] = self.compiler_fingerprint
        return value


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

    # DeepSeek 偶尔把 after_gap_id 原样填进 starts_at_quote_id。若两者确实是
    # 同一个已发送 Gap，可由位置唯一推出 Gap 后第一条目标对白，无需再次付费重试。
    fragments = {str(item.fragment_id): item for item in window.fragments}
    targets = set(str(item) for item in window.target_quote_ids)
    reference_repairs: list[str] = []
    deferred_gaps: set[str] = set()
    for update in payload.get("scene_updates", []) or []:
        if not isinstance(update, dict):
            continue
        gap_id = str(update.get("after_gap_id", ""))
        starts_at = str(update.get("starts_at_quote_id", ""))
        gap = fragments.get(gap_id)
        if gap is None or gap.kind.value not in {"inner_gap", "outer_gap"}:
            continue
        candidates = sorted(
            (
                item
                for item in window.fragments
                if str(item.fragment_id) in targets and item.start_cp >= gap.end_cp
            ),
            key=lambda item: item.start_cp,
        )
        start_fragment = fragments.get(starts_at)
        # A trailing context break has no target in this window. It belongs to
        # the next window, not to a read-only overlap fragment. Only remove an
        # unused declaration with a known context reference; never guess IDs or
        # rewrite any target's scene/speaker assignment.
        if (
            not candidates and start_fragment is not None
            and gap.kind.value == "outer_gap"
            and starts_at not in targets
            and (starts_at == gap_id or start_fragment.kind.value == "overlap")
            and start_fragment.start_cp >= gap.start_cp
            and sum(
                item.get("after_gap_id") == gap_id
                for item in payload["scene_updates"] if isinstance(item, dict)
            ) == 1
            and any(
                decision.get("gap_id") == gap_id and decision.get("decision") == "BREAK"
                for decision in payload["gap_decisions"] if isinstance(decision, dict)
            )
            and not any(
                label.get("scene_ref") == update.get("temp_ref")
                for label in payload.get("labels", []) if isinstance(label, dict)
            )
            and not any(
                speaker.get("scene_ref") == update.get("temp_ref")
                for speaker in payload.get("new_speakers", []) if isinstance(speaker, dict)
            )
        ):
            deferred_gaps.add(gap_id)
            reference_repairs.append(f"deferred_trailing_scene_break:{gap_id}")
        elif candidates and starts_at == gap_id:
            quote_id = str(candidates[0].fragment_id)
            update["starts_at_quote_id"] = quote_id
            reference_repairs.append(
                f"repaired_scene_start:{gap_id}->{quote_id}"
            )
    if deferred_gaps:
        payload["scene_updates"] = [update for update in payload["scene_updates"]
                                   if not isinstance(update, dict)
                                   or update.get("after_gap_id") not in deferred_gaps]
        payload["gap_decisions"] = [
            {**decision, "decision": "CONTINUE"}
            if isinstance(decision, dict) and decision.get("gap_id") in deferred_gaps
            else decision for decision in payload["gap_decisions"]
        ]
    if reference_repairs:
        payload["_reference_repairs"] = reference_repairs
    return payload


def _messages_for(
    *,
    window,  # noqa: ANN001 - ProcessingWindow
    state: SceneState,
    locked_summary: str | None,
    correction: str | None = None,
) -> list[dict[str, str]]:
    stable_to_alias, _alias_to_stable = _reference_aliases(window)
    state.sync_confirmed_participants()

    def alias(ref: str) -> str:
        return stable_to_alias.get(ref, ref)

    targets = set(window.target_quote_ids)
    target_fragments = sorted(
        (fragment for fragment in window.fragments if fragment.fragment_id in targets),
        key=lambda fragment: fragment.start_cp,
    )
    context_records = [
        {
            "ref": alias(fragment.fragment_id),
            "kind": fragment.kind.value,
            "start_cp": fragment.start_cp,
            "end_cp": fragment.end_cp,
            "text": fragment.text,
            **({"next_target_quote_id": next(
                (alias(target.fragment_id) for target in target_fragments
                 if target.start_cp >= fragment.end_cp), None,
            )} if fragment.kind.value in {"inner_gap", "outer_gap"} else {}),
        }
        for fragment in window.fragments
    ]
    identity_provenance = {
        item.character_id: {"source": item.source, "user_confirmed": item.user_confirmed,
                            "confirmation_source": item.confirmation_source}
        for item in state.identity_characters
    }
    speaker_records = [
        {
            "speaker_ref": slot.display_label,
            "character_id": slot.character_id,
            "canonical_name": slot.canonical_name or None,
            "description": slot.description or "未说明",
            "first_quote_id": stable_to_alias.get(slot.first_quote_id),
            "is_pov": slot.character_id is not None
            and slot.character_id == state.pov_character_id,
            "evidence_refs": [alias(ref) for ref in slot.evidence_refs if ref in stable_to_alias],
            "identity_provenance": identity_provenance.get(
                slot.character_id,
                {"source": "unknown", "user_confirmed": None, "confirmation_source": "unknown"},
            ),
        }
        for slot in state.participants
    ]
    confirmed_records = [item.prompt_record() for item in state.confirmed_characters]
    pov_character = next(
        (
            item.prompt_record()
            for item in state.confirmed_characters
            if item.character_id == state.pov_character_id
        ),
        None,
    )
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
        confirmed_characters=confirmed_records,
        book_characters=[item.prompt_record() for item in state.book_characters],
        pov_character=pov_character,
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
                    (correction if correction.startswith("复核说明：")
                     else "上一次输出无效：" + correction)
                    + "。请重新只输出符合 schema 的 JSON 对象，"
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
    expression_task=None,  # noqa: ANN001 - explicitly prepared CompactTask
    owner_approvals: Mapping[str, bool] | None = None,
) -> WindowRunResult:
    """跑一个窗口：最多 ``1 + max_format_retries`` 次调用，然后在同一事务里应用结果。"""

    policy = retry_policy or RetryPolicy()
    protocol = None
    if expression_task is not None:
        from ..evaluation.owner_constraints import VERSION, ConstrainedOwnerProtocol
        from ..llm.expression_compiler import compile_expression_output

        expression_task = deepcopy(expression_task)
        owner_approvals = dict(owner_approvals) if owner_approvals is not None else None
        _validate_expression_task(expression_task, window, state, owner_approvals)
        protocol = ConstrainedOwnerProtocol(expression_task)
    elif owner_approvals is not None:
        raise ValueError("Owner approval requires an explicit expression task")
    result = WindowRunResult(
        application=WindowApplication(
            window_id=window.window_id,
            scene_state=state,
            validation_ok=False,
        ),
        adapter=getattr(adapter, "name", "none"),
        prompt_version=VERSION if protocol is not None else LABELING_PROMPT_VERSION,
    )
    correction: str | None = None
    attempt = 0

    while True:
        attempt += 1
        result.attempts = attempt
        messages = (_messages_for(
                window=window, state=state, locked_summary=locked_summary, correction=correction
            ) if protocol is None else protocol.messages())
        if protocol is not None and correction:
            messages.append({"role": "user", "content": "上一次输出无效：" + correction})
        payload = {
            "messages": messages,
            "max_tokens": LABELING_MAX_TOKENS,
            "json_object": True,
            "target_quote_ids": list(window.target_quote_ids),
        }
        try:
            returned = await adapter.generate_labels(payload)
            raw = returned if protocol is not None else _restore_output_references(returned, window)
        except ProviderError as exc:
            result.error_code = exc.code.value
            result.application.warnings.append(f"{exc.code.value}: {exc.message}")
            if protocol is not None:
                failed_usage = exc.details.get("usage")
                if isinstance(failed_usage, Mapping):
                    result.usage_records.append(dict(failed_usage))
                if isinstance(exc.details.get("body"), str):
                    result.raw_outputs.append(exc.details["body"])
                if (_known_expression_usage(failed_usage)
                        and policy.should_retry(exc, retries_used=attempt - 1)):
                    correction = exc.message
                    continue
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
        compilation = None
        expected_version = "1.0"
        try:
            if protocol is not None:
                compilation = compile_expression_output(
                    payload_for_validation, expression_task, owner_approvals=owner_approvals,
                )
                payload_for_validation = compilation.output
                expected_version = "1.1"
            report = parse_and_validate(payload_for_validation, _targets_for(window, state),
                                        expected_schema_version=expected_version)
        except (InvalidModelOutput, ValidationError) as exc:
            error = InvalidModelOutput(str(exc))
            result.error_code = error.code.value
            result.application.validation_codes = ["invalid_expression_output"]
            result.application.warnings.append(error.message)
            if (not _known_expression_usage(usage)
                    or not policy.should_retry(error, retries_used=attempt - 1)):
                return result
            correction = error.message
            continue
        except ValueError as exc:
            result.error_code = InvalidModelOutput(str(exc)).code.value
            result.application.validation_codes = ["invalid_owner_approval"]
            result.application.warnings.append(str(exc))
            return result
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
                expected_schema_version=expected_version,
                acceptance_ceilings=compilation.acceptance_ceilings if compilation else None,
            )
            if compilation is not None:
                result.compiler_fingerprint = compilation.fingerprint()
                if result.application.validation_ok:
                    result.error_code = None
            if isinstance(raw, Mapping):
                repairs = raw.get("_reference_repairs")
                if isinstance(repairs, list):
                    result.application.warnings.extend(str(item) for item in repairs)
            return result

        result.application.validation_ok = False
        result.application.validation_codes = report.error_codes
        result.application.warnings.extend(report.messages[:5])
        error = InvalidModelOutput("；".join(report.messages[:3]) or "输出不符合 schema")
        result.error_code = error.code.value
        if (protocol is not None and not _known_expression_usage(usage)
                or not policy.should_retry(error, retries_used=attempt - 1)):
            return result
        correction = error.message


def _validate_expression_task(task, window, state, approvals):  # noqa: ANN001
    from ..llm.expression_compiler import validate_initial_identity_fields

    validate_initial_identity_fields(task)
    if tuple(task.references[q] for q in task.quote_ids) != tuple(window.target_quote_ids):
        raise ValueError("Expression targets differ from the actual window")
    fragments = {f.fragment_id: f for f in window.fragments}
    if set(task.references.values()) != set(fragments) or task.scene_ref != state.scene_ref:
        raise ValueError("Expression references or scene differ from the actual window")
    for row in task.context:
        fragment = fragments[task.references[row["ref"]]]
        if (row.get("start_cp"), row.get("end_cp"), row.get("text"), row.get("kind")) != (
            fragment.start_cp, fragment.end_cp, fragment.text, fragment.kind.value,
        ):
            raise ValueError("Expression context differs from the actual original window")
    actual_gaps = {f.fragment_id for f in window.fragments
                   if f.kind.value in {"inner_gap", "outer_gap"}}
    if {task.references[g] for g in task.gap_next_quote} != actual_gaps:
        raise ValueError("Expression boundaries differ from the actual window")
    identities = {c.character_id for c in state.identity_characters}
    slots = {slot.display_label for slot in state.participants}
    if any(c.character_id and c.character_id not in identities for c in task.candidates):
        raise ValueError("Expression candidate was not provided by the current state")
    if any(c.existing_ref and c.existing_ref not in slots for c in task.candidates):
        raise ValueError("Expression candidate cites an unprovided scene slot")
    if any(c.existing_ref and c.character_id != state.find(c.existing_ref).character_id
           for c in task.candidates):
        raise ValueError("Expression candidate identity differs from the provided scene slot")
    if approvals is not None and (
        set(approvals) != set(window.target_quote_ids)
        or any(type(v) is not bool for v in approvals.values())
    ):
        raise ValueError("Complete explicit owner approvals required")


def _known_expression_usage(usage) -> bool:  # noqa: ANN001
    return (isinstance(usage, Mapping) and not usage.get("unknown")
            and type(usage.get("total_tokens")) is int and usage["total_tokens"] >= 0)


def _targets_for(window, state: SceneState):  # noqa: ANN001, ANN202
    from ..llm.validation import LabelingTargets

    return LabelingTargets(
        require_display_names=True,
        character_ids=tuple(item.character_id for item in state.identity_characters),
        quote_ids=tuple(window.target_quote_ids),
        gap_ids=tuple(
            fragment.fragment_id
            for fragment in window.fragments
            if fragment.kind.value in {"inner_gap", "outer_gap"}
        ),
        scene_refs=(state.scene_ref,),
        speaker_refs=tuple(slot.display_label for slot in state.participants),
        evidence_ids=tuple(window.fragment_ids),
        confirmed_names=tuple(
            name
            for character in state.confirmed_characters
            for name in (character.canonical_name, *character.aliases)
            if name
        ),
    )
