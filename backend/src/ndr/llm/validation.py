"""模型输出的解析与程序校验。

原则：

- **不执行任何模型输出**，也不从长文本里“找看起来像 JSON 的片段”：
  只接受整段 JSON 对象，或整段被 ``` 包裹的 JSON 代码块。
- 允许引用的 ID 集合由程序生成（:class:`LabelingTargets`）；引用未发送的内容一律判错。
- 校验失败返回结构化问题，由调用方决定是否按 :class:`RetryPolicy` 有限重试。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from ..characters.names import valid_display_name
from ..domain.enums import Assignment, GapDecision, QuoteKind
from .errors import InvalidModelOutput, ProviderError, ProviderErrorKind
from .schemas import LlmOutput, NewSpeaker, QuoteLabel

MAX_LABEL_ATTEMPTS = 2


@dataclass(frozen=True)
class LabelingTargets:
    """本次调用允许引用的 ID 集合（由程序生成，模型不得越界）。"""

    quote_ids: tuple[str, ...]
    gap_ids: tuple[str, ...] = ()
    scene_refs: tuple[str, ...] = ("scene_current",)
    speaker_refs: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    confirmed_names: tuple[str, ...] = ()
    character_ids: tuple[str, ...] = ()
    require_display_names: bool = False


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    target_id: str | None = None


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    output: LlmOutput | None
    issues: tuple[ValidationIssue, ...] = ()
    accepted_labels: tuple[QuoteLabel, ...] = ()
    warnings: tuple[str, ...] = ()
    _codes: tuple[str, ...] = field(default=(), repr=False)

    @property
    def error_codes(self) -> list[str]:
        return list(self._codes)

    @property
    def messages(self) -> list[str]:
        return [issue.message for issue in self.issues]


@dataclass(frozen=True)
class RetryPolicy:
    """格式/结构错误最多重试一次；鉴权与限流不在这里重试。"""

    max_format_retries: int = 1

    def should_retry(self, error: ProviderError, *, retries_used: int) -> bool:
        """``max_format_retries`` 是**额外**允许的重试次数（不是总尝试次数）。"""

        if retries_used >= self.max_format_retries:
            return False
        return error.kind is ProviderErrorKind.INVALID_OUTPUT


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 3 or not lines[-1].strip().startswith("```"):
        raise InvalidModelOutput("代码块没有正确闭合，拒绝解析。")
    body = "\n".join(lines[1:-1])
    # 去掉语言标记行（```json）
    first_line = lines[0].strip().lstrip("`").strip().lower()
    if first_line in {"json", "json5", ""}:
        return body.strip()
    raise InvalidModelOutput(f"不支持的代码块标记：{lines[0].strip()[:20]}")


def load_json_object(payload: str) -> dict[str, Any]:
    """把模型返回的**整段**文本解析成 JSON 对象。

    只接受两种形态：整段 JSON 对象，或整段被 ``` 包裹的 JSON 代码块；
    绝不在长文本里“找看起来像 JSON 的片段”——那会把解释性文字当成数据。
    """

    text = _strip_code_fence(payload)
    if not text:
        raise InvalidModelOutput("模型返回空内容。")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidModelOutput(
            "模型输出不是合法 JSON（不接受从长文本中截取片段）。",
            details={
                "reason": f"{exc.msg} at line {exc.lineno} column {exc.colno}",
                "snippet": text[:200],
            },
        ) from exc
    if not isinstance(data, dict):
        raise InvalidModelOutput("模型输出的顶层必须是 JSON 对象。")
    return data


def parse_output(payload: str | Mapping[str, Any]) -> LlmOutput:
    """把模型输出解析成契约对象；坏 JSON/字段类型错都抛 :class:`InvalidModelOutput`。"""

    if isinstance(payload, str):
        payload = load_json_object(payload)

    try:
        return LlmOutput.model_validate(payload)
    except ValidationError as exc:
        issues = [
            {
                "loc": [str(part) for part in error.get("loc", ())],
                "type": str(error.get("type", "")),
                "message": str(error.get("msg", "")),
            }
            for error in exc.errors()[:10]
        ]
        raise InvalidModelOutput(
            "模型输出不符合 schema。", details={"issues": issues}
        ) from exc


REPAIR_DESCRIPTION = "（程序补齐声明：模型只写了 assignment=NEW，未声明 new_speakers）"


def repair_undeclared_speakers(
    output: LlmOutput, targets: LabelingTargets
) -> tuple[LlmOutput, list[str]]:
    """补齐「用了 NEW 但忘记声明 temp_ref」的输出（真实模型的高频遗漏）。

    只做**可确证的补齐**：标签已经明确写了 `assignment=NEW` 与 `speaker_ref`，缺的只是同一次输出里的
    `new_speakers` 声明；声明的 `first_quote_id` 取该标签自身，`description` 明确标注是程序补齐。
    `assignment=EXISTING` 引用未知说话人属于**语义不明**（已有分组还是笔误），
    不在这里修，交给纠错重发。
    """

    declared = {speaker.temp_ref for speaker in output.new_speakers}
    existing = set(targets.speaker_refs)
    missing: dict[str, str] = {}  # temp_ref -> scene_ref
    for label in output.labels:
        if label.kind is not QuoteKind.SPEECH or label.assignment is not Assignment.NEW:
            continue
        ref = label.speaker_ref
        if not ref or ref in declared or ref in existing or ref in missing:
            continue
        missing[ref] = label.scene_ref
    if not missing:
        return output, []

    first_quote_of: dict[str, str] = {}
    for label in output.labels:
        if label.assignment is Assignment.NEW and label.speaker_ref in missing:
            first_quote_of.setdefault(str(label.speaker_ref), label.quote_id)

    additions = [
        NewSpeaker(
            temp_ref=ref,
            scene_ref=scene_ref,
            first_quote_id=first_quote_of.get(ref, ""),
            description=REPAIR_DESCRIPTION,
        )
        for ref, scene_ref in missing.items()
    ]
    repaired = output.model_copy(update={"new_speakers": [*output.new_speakers, *additions]})
    warnings = [f"repaired_undeclared_speaker:{ref}" for ref in missing]
    return repaired, warnings


def repair_declared_first_speakers(
    output: LlmOutput, targets: LabelingTargets
) -> tuple[LlmOutput, list[str]]:
    """Normalize only a unique, explicit declaration at its actual first use."""
    declarations = {speaker.temp_ref: speaker for speaker in output.new_speakers}
    counts = {ref: sum(s.temp_ref == ref for s in output.new_speakers)
              for ref in declarations}
    positions = {ref: index for index, ref in enumerate(targets.quote_ids)}
    replacements = {}
    warnings = []
    for ref, speaker in declarations.items():
        uses = [label for label in output.labels if label.speaker_ref == ref
                and label.kind is QuoteKind.SPEECH]
        if not uses or counts[ref] != 1 or ref in targets.speaker_refs:
            continue
        first = min(uses, key=lambda label: positions.get(label.quote_id, len(positions)))
        if (first.assignment is Assignment.EXISTING
                and first.quote_id == speaker.first_quote_id
                and first.quote_id in positions and first.scene_ref == speaker.scene_ref
                and all(label.scene_ref == speaker.scene_ref for label in uses)):
            replacements[first.quote_id] = first.model_copy(update={"assignment": Assignment.NEW})
            warnings.append(f"repaired_declared_first_speaker:{ref}:{first.quote_id}")
    return output.model_copy(update={"labels": [replacements.get(label.quote_id, label)
                                               for label in output.labels]}), warnings


def repair_confirmed_speakers_after_break(
    output: LlmOutput, targets: LabelingTargets
) -> tuple[LlmOutput, list[str]]:
    """把新场景中“旧 S 编号 + 已确认姓名”改写为本场景临时人物。

    这是可确定的结构修复：姓名必须来自用户确认名单。没有姓名或姓名不在名单中时
    保持原样，随后由严格校验拒绝，避免凭旧编号猜人。
    """

    initial_scenes = set(targets.scene_refs)
    new_scene_refs = {update.temp_ref for update in output.scene_updates}
    confirmed = {name.strip().casefold() for name in targets.confirmed_names if name.strip()}
    if not new_scene_refs or not confirmed:
        return output, []

    used_refs = {speaker.temp_ref for speaker in output.new_speakers}
    replacements: dict[tuple[str, str, str], str] = {}
    additions: list[NewSpeaker] = []
    repaired_labels: list[QuoteLabel] = []
    warnings: list[str] = []

    def next_ref() -> str:
        index = 1
        while f"scene_new{index}" in used_refs:
            index += 1
        ref = f"scene_new{index}"
        used_refs.add(ref)
        return ref

    for label in output.labels:
        name_key = (label.speaker_name or "").strip().casefold()
        should_repair = (
            label.scene_ref in new_scene_refs
            and label.scene_ref not in initial_scenes
            and label.kind is QuoteKind.SPEECH
            and label.assignment is Assignment.EXISTING
            and label.speaker_ref in targets.speaker_refs
            and name_key in confirmed
        )
        if not should_repair:
            repaired_labels.append(label)
            continue

        key = (label.scene_ref, str(label.speaker_ref), name_key)
        temp_ref = replacements.get(key)
        first = temp_ref is None
        if first:
            temp_ref = next_ref()
            replacements[key] = temp_ref
            additions.append(
                NewSpeaker(
                    temp_ref=temp_ref,
                    scene_ref=label.scene_ref,
                    first_quote_id=label.quote_id,
                    description="（程序按用户确认姓名重建新场景人物）",
                    evidence_refs=list(label.evidence_refs),
                )
            )
        repaired_labels.append(
            label.model_copy(
                update={
                    "assignment": Assignment.NEW if first else Assignment.EXISTING,
                    "speaker_ref": temp_ref,
                }
            )
        )
        warnings.append(
            f"repaired_old_scene_speaker:{label.quote_id}:{label.speaker_ref}->{temp_ref}"
        )

    if not additions:
        return output, []
    return (
        output.model_copy(
            update={
                "new_speakers": [*output.new_speakers, *additions],
                "labels": repaired_labels,
            }
        ),
        warnings,
    )


def validate_output(output: LlmOutput, targets: LabelingTargets) -> ValidationReport:
    """按程序生成的允许集合校验输出；返回接受的对白标签与全部问题。"""

    issues: list[ValidationIssue] = []
    allowed_quotes = set(targets.quote_ids)
    allowed_gaps = set(targets.gap_ids)
    allowed_scenes = set(targets.scene_refs)
    allowed_speakers = set(targets.speaker_refs)
    allowed_evidence = set(targets.evidence_ids)

    # 1) 场景声明
    new_scenes: set[str] = set()
    scene_updates_by_gap: dict[str, str] = {}
    scene_starts: dict[str, str] = {}
    break_gaps = {
        decision.gap_id
        for decision in output.gap_decisions
        if decision.decision is GapDecision.BREAK
    }
    for update in output.scene_updates:
        if update.temp_ref in new_scenes or update.temp_ref in allowed_scenes:
            issues.append(
                ValidationIssue(
                    "duplicate_scene_ref", f"场景引用 {update.temp_ref} 重复", update.temp_ref
                )
            )
        new_scenes.add(update.temp_ref)
        if update.after_gap_id in scene_updates_by_gap:
            issues.append(
                ValidationIssue(
                    "duplicate_scene_update",
                    f"Gap {update.after_gap_id} 声明了多个新场景",
                    update.after_gap_id,
                )
            )
        scene_updates_by_gap[update.after_gap_id] = update.temp_ref
        if update.starts_at_quote_id in scene_starts:
            issues.append(
                ValidationIssue(
                    "duplicate_scene_start",
                    f"对白 {update.starts_at_quote_id} 被多个新场景作为起点",
                    update.starts_at_quote_id,
                )
            )
        scene_starts[update.starts_at_quote_id] = update.temp_ref
        if update.after_gap_id not in allowed_gaps:
            issues.append(
                ValidationIssue(
                    "unknown_gap_in_scene_update",
                    f"场景声明引用了未发送的 Gap：{update.after_gap_id}",
                    update.temp_ref,
                )
            )
        elif update.after_gap_id not in break_gaps:
            issues.append(
                ValidationIssue(
                    "scene_update_without_break",
                    f"新场景必须由 BREAK 决策触发，但 {update.after_gap_id} 不是 BREAK",
                    update.temp_ref,
                )
            )
        if update.starts_at_quote_id not in allowed_quotes:
            issues.append(
                ValidationIssue(
                    "unknown_quote_in_scene_update",
                    f"场景起点不是本窗口目标对白（只读上下文不能作为起点）："
                    f"{update.starts_at_quote_id}",
                    update.temp_ref,
                )
            )
    for gap_id in sorted(break_gaps):
        if gap_id not in scene_updates_by_gap:
            issues.append(
                ValidationIssue(
                    "break_without_scene_update",
                    f"BREAK {gap_id} 缺少 scene_updates 声明；切场景后旧 S 编号失效",
                    gap_id,
                )
            )

    allowed_scenes |= new_scenes
    initial_scene = targets.scene_refs[0] if targets.scene_refs else "scene_current"
    expected_scene_by_quote: dict[str, str] = {}
    current_scene = initial_scene
    for quote_id in targets.quote_ids:
        if quote_id in scene_starts:
            current_scene = scene_starts[quote_id]
        expected_scene_by_quote[quote_id] = current_scene

    # 2) 新人物
    new_speakers: dict[str, str] = {}
    for speaker in output.new_speakers:
        if speaker.character_id and speaker.character_id not in targets.character_ids:
            issues.append(ValidationIssue(
                "unknown_character_in_speaker", "人物引用了未提供的全书人物 ID",
                speaker.temp_ref,
            ))
        if speaker.character_id and not speaker.evidence_refs:
            issues.append(ValidationIssue(
                "missing_character_evidence", "关联已有全书人物必须提供原文证据",
                speaker.temp_ref,
            ))
        if targets.require_display_names and not valid_display_name(speaker.name):
            issues.append(ValidationIssue(
                "missing_speaker_name", "新人物必须在 name 填写简短姓名或称呼，详细描述另填",
                speaker.temp_ref,
            ))
        if speaker.real_name and (
            not valid_display_name(speaker.real_name) or not speaker.evidence_refs
        ):
            issues.append(ValidationIssue(
                "missing_real_name_evidence", "真实姓名必须提供原文证据且为简短姓名",
                speaker.temp_ref,
            ))
        if speaker.temp_ref in new_speakers:
            issues.append(
                ValidationIssue(
                    "duplicate_speaker_ref", f"人物引用 {speaker.temp_ref} 重复", speaker.temp_ref
                )
            )
        new_speakers[speaker.temp_ref] = speaker.scene_ref
        if speaker.scene_ref not in allowed_scenes:
            issues.append(
                ValidationIssue(
                    "unknown_scene_in_speaker",
                    f"新人物引用了未知场景：{speaker.scene_ref}",
                    speaker.temp_ref,
                )
            )
        if speaker.first_quote_id not in allowed_quotes:
            issues.append(
                ValidationIssue(
                    "unknown_quote_in_speaker",
                    f"新人物的首次发言不在目标里：{speaker.first_quote_id}",
                    speaker.temp_ref,
                )
            )

    # 3) Gap 决策
    seen_gaps: set[str] = set()
    for decision in output.gap_decisions:
        if decision.gap_id in seen_gaps:
            issues.append(
                ValidationIssue(
                    "duplicate_gap_decision",
                    f"Gap {decision.gap_id} 重复决策",
                    decision.gap_id,
                )
            )
        seen_gaps.add(decision.gap_id)
        if decision.gap_id not in allowed_gaps:
            issues.append(
                ValidationIssue(
                    "unknown_gap", f"引用了未发送的 Gap：{decision.gap_id}", decision.gap_id
                )
            )

    # 4) 对白标签：覆盖、唯一、引用合法
    positions = {ref: index for index, ref in enumerate(targets.quote_ids)}
    for speaker in output.new_speakers:
        uses = [label for label in output.labels if label.speaker_ref == speaker.temp_ref
                and label.kind is QuoteKind.SPEECH]
        if not uses or speaker.temp_ref in allowed_speakers:
            continue  # Name supplements need not create a second group.
        first = min(uses, key=lambda label: positions.get(label.quote_id, len(positions)))
        if (first.assignment is not Assignment.NEW
                or first.quote_id != speaker.first_quote_id):
            issues.append(ValidationIssue(
                "speaker_used_before_creation",
                f"人物 {speaker.temp_ref} 的首次引用必须与声明一致并使用 NEW",
                first.quote_id,
            ))
    seen_quotes: set[str] = set()
    accepted: list[QuoteLabel] = []
    for label in output.labels:
        if label.quote_id in seen_quotes:
            issues.append(
                ValidationIssue(
                    "duplicate_label", f"对白 {label.quote_id} 出现多条标签", label.quote_id
                )
            )
            continue
        seen_quotes.add(label.quote_id)
        if label.quote_id not in allowed_quotes:
            issues.append(
                ValidationIssue(
                    "unknown_quote", f"引用了未发送的对白：{label.quote_id}", label.quote_id
                )
            )
            continue
        if label.scene_ref not in allowed_scenes:
            issues.append(
                ValidationIssue(
                    "unknown_scene",
                    f"对白 {label.quote_id} 引用了未知场景 {label.scene_ref}",
                    label.quote_id,
                )
            )
            continue
        expected_scene = expected_scene_by_quote.get(label.quote_id, initial_scene)
        if label.scene_ref != expected_scene:
            issues.append(
                ValidationIssue(
                    "speaker_scene_mismatch",
                    (
                        f"对白 {label.quote_id} 应属于 {expected_scene}，"
                        f"不能继续使用 {label.scene_ref}"
                    ),
                    label.quote_id,
                )
            )
            continue
        if label.kind is QuoteKind.SPEECH and label.assignment is not None:
            if label.assignment is Assignment.EXISTING:
                # BREAK 后旧场景的 S1/S2 立即失效；新场景只能复用本次输出中
                # 已经以 NEW 声明、且属于同一 scene_ref 的临时人物。
                valid_existing = (
                    label.speaker_ref in allowed_speakers
                    if expected_scene == initial_scene
                    else new_speakers.get(str(label.speaker_ref)) == expected_scene
                )
                if not valid_existing and label.speaker_ref in new_speakers:
                    valid_existing = new_speakers[str(label.speaker_ref)] == expected_scene
                if not valid_existing:
                    issues.append(
                        ValidationIssue(
                            (
                                "old_scene_speaker"
                                if expected_scene != initial_scene
                                else "unknown_speaker"
                            ),
                            (
                                f"对白 {label.quote_id} 在切场景后仍引用旧人物 "
                                f"{label.speaker_ref}；"
                                "请先用 NEW + new_speakers 在新场景重新声明"
                                if expected_scene != initial_scene
                                else f"对白 {label.quote_id} 引用了未知说话人 {label.speaker_ref}"
                            ),
                            label.quote_id,
                        )
                    )
                    continue
            elif label.assignment is Assignment.NEW:
                if label.speaker_ref not in new_speakers:
                    issues.append(
                        ValidationIssue(
                            "undeclared_new_speaker",
                            f"对白 {label.quote_id} 使用了未声明的临时人物 {label.speaker_ref}",
                            label.quote_id,
                        )
                    )
                    continue
                if new_speakers[str(label.speaker_ref)] != label.scene_ref:
                    issues.append(
                        ValidationIssue(
                            "speaker_scene_mismatch",
                            f"临时人物 {label.speaker_ref} 属于其它场景",
                            label.quote_id,
                        )
                    )
                    continue
        if allowed_evidence:
            unknown_evidence = [ref for ref in label.evidence_refs if ref not in allowed_evidence]
            if unknown_evidence:
                issues.append(
                    ValidationIssue(
                        "unknown_evidence",
                        f"对白 {label.quote_id} 引用了未发送的证据：{'、'.join(unknown_evidence)}",
                        label.quote_id,
                    )
                )
                continue
        accepted.append(label)

    missing = sorted(allowed_quotes - seen_quotes)
    if missing:
        issues.append(
            ValidationIssue(
                "missing_targets",
                f"有 {len(missing)} 条目标对白没有标签：{'、'.join(missing[:5])}",
            )
        )

    for quote_id in output.needs_context:
        if quote_id not in allowed_quotes:
            issues.append(
                ValidationIssue(
                    "unknown_needs_context",
                    f"needs_context 引用了未发送的对白：{quote_id}",
                    quote_id,
                )
            )

    codes = tuple(dict.fromkeys(issue.code for issue in issues))
    return ValidationReport(
        ok=not issues,
        output=output,
        issues=tuple(issues),
        accepted_labels=tuple(accepted),
        _codes=codes,
    )


def parse_and_validate(
    payload: str | Mapping[str, Any], targets: LabelingTargets
) -> ValidationReport:
    """解析 + 校验；解析失败时返回一条带 ``invalid_output`` 的报告（不抛异常）。"""

    try:
        output = parse_output(payload)
    except InvalidModelOutput as exc:
        message = str(exc)
        schema_issues = exc.details.get("issues")
        if isinstance(schema_issues, list) and schema_issues:
            details: list[str] = []
            for item in schema_issues[:5]:
                if not isinstance(item, Mapping):
                    continue
                location = ".".join(str(part) for part in item.get("loc", ())) or "顶层"
                reason = str(item.get("message") or item.get("type") or "字段无效")
                details.append(f"{location}: {reason}")
            if details:
                message += " " + "；".join(details)
        issue = ValidationIssue("invalid_output", message)
        return ValidationReport(
            ok=False, output=None, issues=(issue,), accepted_labels=(), _codes=("invalid_output",)
        )
    output, warnings = repair_undeclared_speakers(output, targets)
    output, scene_warnings = repair_confirmed_speakers_after_break(output, targets)
    warnings.extend(scene_warnings)
    output, first_warnings = repair_declared_first_speakers(output, targets)
    warnings.extend(first_warnings)
    report = validate_output(output, targets)
    if not warnings:
        return report
    return ValidationReport(
        ok=report.ok,
        output=report.output,
        issues=report.issues,
        accepted_labels=report.accepted_labels,
        warnings=tuple(warnings),
        _codes=report._codes,
    )
