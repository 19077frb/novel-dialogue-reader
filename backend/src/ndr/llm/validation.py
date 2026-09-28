"""模型输出的解析与程序校验（DEVELOPMENT.md 4.5 第 1～2 步）。

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

from ..domain.enums import Assignment, GapDecision, QuoteKind
from .errors import InvalidModelOutput, ProviderError, ProviderErrorKind
from .schemas import LlmOutput, QuoteLabel

MAX_LABEL_ATTEMPTS = 2


@dataclass(frozen=True)
class LabelingTargets:
    """本次调用允许引用的 ID 集合（由程序生成，模型不得越界）。"""

    quote_ids: tuple[str, ...]
    gap_ids: tuple[str, ...] = ()
    scene_refs: tuple[str, ...] = ("scene_current",)
    speaker_refs: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()


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
    _codes: tuple[str, ...] = field(default=(), repr=False)

    @property
    def error_codes(self) -> list[str]:
        return list(self._codes)

    @property
    def messages(self) -> list[str]:
        return [issue.message for issue in self.issues]


@dataclass(frozen=True)
class RetryPolicy:
    """格式/结构错误最多重试一次；鉴权与限流不在这里重试（T10/T14 处理退避）。"""

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

    只接受两种形态（DEVELOPMENT 4.5）：整段 JSON 对象，或整段被 ``` 包裹的 JSON 代码块；
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
                    f"场景起点引用了未发送的对白：{update.starts_at_quote_id}",
                    update.temp_ref,
                )
            )
    allowed_scenes |= new_scenes

    # 2) 新人物
    new_speakers: dict[str, str] = {}
    for speaker in output.new_speakers:
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
        if label.kind is QuoteKind.SPEECH and label.assignment is not None:
            if label.assignment is Assignment.EXISTING:
                # EXISTING 可以指向已建立的稳定分组，也可以指向**本次输出里刚声明**的临时人物
                # （同一窗口里同一新声音的第二句就是这种情况）。
                if (
                    label.speaker_ref not in allowed_speakers
                    and label.speaker_ref not in new_speakers
                ):
                    issues.append(
                        ValidationIssue(
                            "unknown_speaker",
                            f"对白 {label.quote_id} 引用了未知说话人 {label.speaker_ref}",
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
        issue = ValidationIssue("invalid_output", str(exc))
        return ValidationReport(
            ok=False, output=None, issues=(issue,), accepted_labels=(), _codes=("invalid_output",)
        )
    return validate_output(output, targets)
