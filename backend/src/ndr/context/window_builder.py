"""处理窗口构建（DEVELOPMENT.md 4.3 / PLAN 6.3）。

- 预算边界**不是**场景边界：窗口只是调用边界，场景状态由 T09 管理。
- 目标对白绝不截断；放不下就拆成多个窗口。
  单个目标本身就超预算时给它独立窗口并把该目标标为 ``oversized_quote``（保留待定，绝不截断后猜测）。
- 窗口之间携带少量重叠与「上一窗口最后一个目标」的接力点，长场景因此可以跨窗口延续。
- 依赖哈希覆盖（原文版本、目标、证据、策略、提示版本、阅读模式、horizon、场景引用），
  任一变化都会得到不同的哈希，避免错误复用缓存。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..domain.enums import ReadingMode
from ..llm.prompts import LABELING_PROMPT_VERSION
from .budget import (
    CONTEXT_POLICY_CONSERVATIVE,
    DEFAULT_ESTIMATOR,
    DEFAULT_POLICY,
    BudgetItemKind,
    BudgetPolicy,
    TokenEstimator,
    estimate_tokens,
    policy_version_for,
)
from .source_selection import (
    ContextFragment,
    GapView,
    OmittedRecord,
    ParagraphView,
    QuoteView,
    select_evidence,
)

# 默认（保守）上下文策略的版本号。窗口/计划真正使用的版本由 `policy_version_for(policy)` 计算：
# 开启 T17 压缩策略（context-2）会得到不同的窗口 ID、依赖哈希与缓存键，避免错误复用缓存。
CONTEXT_POLICY_VERSION = CONTEXT_POLICY_CONSERVATIVE
OVERSIZED_WARNING = "oversized_quote"


@dataclass(frozen=True)
class WindowInputs:
    book_version_id: str
    canonical_text: str
    quotes: Sequence[QuoteView]
    gaps: Sequence[GapView] = ()
    paragraphs: Sequence[ParagraphView] = ()
    reading_mode: ReadingMode = ReadingMode.INITIAL
    visible_horizon_cp: int | None = None
    scene_ref: str = "scene_current"
    scene_state: str | None = None
    locked_summary: str | None = None
    speaker_refs: Sequence[str] = ()
    policy: BudgetPolicy = DEFAULT_POLICY
    prompt_version: str = LABELING_PROMPT_VERSION
    estimator: TokenEstimator = DEFAULT_ESTIMATOR


@dataclass(frozen=True)
class ProcessingWindow:
    window_id: str
    target_quote_ids: tuple[str, ...]
    fragments: tuple[ContextFragment, ...]
    omitted: tuple[OmittedRecord, ...]
    warnings: tuple[str, ...]
    scene_ref: str
    reading_mode: ReadingMode
    visible_horizon_cp: int | None
    prompt_version: str
    policy_version: str
    dependency_hash: str
    estimated_tokens: dict[str, int]
    budget: dict[str, Any]
    carry_from_window_id: str | None = None
    carry_last_quote_id: str | None = None

    @property
    def fragment_ids(self) -> tuple[str, ...]:
        return tuple(fragment.fragment_id for fragment in self.fragments)

    def as_dict(self) -> dict[str, Any]:
        return {
            "window_id": self.window_id,
            "target_quote_ids": list(self.target_quote_ids),
            "fragments": [fragment.as_dict() for fragment in self.fragments],
            "omitted": [record.as_dict() for record in self.omitted],
            "warnings": list(self.warnings),
            "scene_ref": self.scene_ref,
            "reading_mode": self.reading_mode.value,
            "visible_horizon_cp": self.visible_horizon_cp,
            "prompt_version": self.prompt_version,
            "policy_version": self.policy_version,
            "dependency_hash": self.dependency_hash,
            "estimated_tokens": self.estimated_tokens,
            "budget": self.budget,
            "carry_from_window_id": self.carry_from_window_id,
            "carry_last_quote_id": self.carry_last_quote_id,
        }


@dataclass(frozen=True)
class WindowPlan:
    windows: tuple[ProcessingWindow, ...]
    warnings: tuple[str, ...]
    dependency_hash: str
    policy: BudgetPolicy
    policy_version: str = CONTEXT_POLICY_VERSION
    stats: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "windows": [window.as_dict() for window in self.windows],
            "warnings": list(self.warnings),
            "dependency_hash": self.dependency_hash,
            "policy_version": self.policy_version,
            "policy": self.policy.as_key(),
            "stats": self.stats,
        }


def _hash(*parts: Any) -> str:
    blob = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _window_id(*, book_version_id: str, target_ids: Sequence[str], policy_version: str,
               prompt_version: str, reading_mode: ReadingMode, horizon: int | None,
               scene_ref: str) -> str:
    digest = _hash(
        book_version_id,
        list(target_ids),
        policy_version,
        prompt_version,
        reading_mode.value,
        horizon,
        scene_ref,
    )
    return f"w{digest[:16]}"


def _inner_gap_ids(gaps: Sequence[GapView], targets: Sequence[QuoteView]) -> list[str]:
    if len(targets) < 2:
        return []
    first, last = targets[0].start_cp, targets[-1].end_cp
    return [
        gap.gap_id
        for gap in gaps
        if gap.start_cp >= first and gap.end_cp <= last and gap.end_cp > gap.start_cp
    ]


def _required_tokens(
    *,
    text: str,
    targets: Sequence[QuoteView],
    gaps: Sequence[GapView],
    estimator: TokenEstimator,
) -> int:
    total = sum(estimator.estimate(text[quote.start_cp : quote.end_cp]) for quote in targets)
    for gap_id in _inner_gap_ids(gaps, targets):
        gap = next(item for item in gaps if item.gap_id == gap_id)
        total += estimator.estimate(text[gap.start_cp : gap.end_cp])
    return total


def _split_targets(
    *,
    inputs: WindowInputs,
    ordered_targets: list[QuoteView],
) -> tuple[list[list[QuoteView]], list[str]]:
    """按“必留片段”的 token 成本贪心分组；单个目标放不下时单独成组并告警。"""

    warnings: list[str] = []
    groups: list[list[QuoteView]] = []
    current: list[QuoteView] = []

    for quote in ordered_targets:
        quote_tokens = inputs.estimator.estimate(
            inputs.canonical_text[quote.start_cp : quote.end_cp]
        )
        if quote_tokens > inputs.policy.context_tokens:
            warnings.append(f"{OVERSIZED_WARNING}:{quote.quote_id}")
            if current:
                groups.append(current)
                current = []
            groups.append([quote])  # 单个目标独立成窗口，绝不截断
            continue

        candidate = [*current, quote]
        cost = _required_tokens(
            text=inputs.canonical_text,
            targets=candidate,
            gaps=inputs.gaps,
            estimator=inputs.estimator,
        )
        if current and cost > inputs.policy.context_tokens:
            groups.append(current)
            current = [quote]
        else:
            current = candidate

    if current:
        groups.append(current)
    return groups, warnings


def plan_windows(
    inputs: WindowInputs,
    *,
    target_quote_ids: Sequence[str],
    overlap_between_windows: bool = True,
) -> WindowPlan:
    """把目标对白与证据组织成受预算约束的窗口列表。"""

    quote_by_id = {quote.quote_id: quote for quote in inputs.quotes}
    ordered = [
        quote_by_id[quote_id] for quote_id in target_quote_ids if quote_id in quote_by_id
    ]
    ordered.sort(key=lambda quote: quote.start_cp)
    missing = [quote_id for quote_id in target_quote_ids if quote_id not in quote_by_id]

    groups, split_warnings = _split_targets(inputs=inputs, ordered_targets=ordered)
    warnings = [*missing_warnings(missing), *split_warnings]
    policy_version = policy_version_for(inputs.policy)

    windows: list[ProcessingWindow] = []
    previous_quote_id: str | None = None
    previous_window_id: str | None = None
    for index, group in enumerate(groups):
        selection = select_evidence(
            canonical_text=inputs.canonical_text,
            quotes=list(inputs.quotes),
            gaps=list(inputs.gaps),
            target_quote_ids=[quote.quote_id for quote in group],
            policy=inputs.policy,
            reading_mode=inputs.reading_mode,
            visible_horizon_cp=inputs.visible_horizon_cp,
            paragraphs=list(inputs.paragraphs),
            scene_state=inputs.scene_state if index == 0 else None,
            locked_summary=inputs.locked_summary if index == 0 else None,
            overlap_before=bool(index > 0 and overlap_between_windows),
            overlap_after=True,
            estimator=inputs.estimator,
        )
        target_ids = tuple(quote.quote_id for quote in group)
        window_id = _window_id(
            book_version_id=inputs.book_version_id,
            target_ids=target_ids,
            policy_version=policy_version,
            prompt_version=inputs.prompt_version,
            reading_mode=inputs.reading_mode,
            horizon=selection.horizon_cp,
            scene_ref=inputs.scene_ref,
        )
        dependency_hash = _hash(
            inputs.book_version_id,
            target_ids,
            selection.fragment_ids,
            [_record.fragment_id for _record in selection.omitted],
            inputs.policy.as_key(),
            policy_version,
            inputs.prompt_version,
            inputs.reading_mode.value,
            selection.horizon_cp,
            inputs.scene_ref,
            sorted(inputs.speaker_refs),
        )
        summary = selection.ledger.summary()
        estimated = {
            "context_tokens": summary["context_tokens"],
            "prompt_tokens": summary["prompt_tokens"],
            "output_tokens": summary["output_tokens"],
            "total_tokens": summary["total_tokens"],
            "target_tokens": sum(
                inputs.estimator.estimate(
                    inputs.canonical_text[quote.start_cp : quote.end_cp]
                )
                for quote in group
            ),
            "overlap_tokens": sum(
                selection.ledger.estimator.estimate(fragment.text)
                for fragment in selection.fragments
                if fragment.kind is BudgetItemKind.OVERLAP
            ),
        }
        windows.append(
            ProcessingWindow(
                window_id=window_id,
                target_quote_ids=target_ids,
                fragments=tuple(selection.fragments),
                omitted=tuple(selection.omitted),
                warnings=tuple(selection.warnings),
                scene_ref=inputs.scene_ref,
                reading_mode=inputs.reading_mode,
                visible_horizon_cp=selection.horizon_cp,
                prompt_version=inputs.prompt_version,
                policy_version=policy_version,
                dependency_hash=dependency_hash,
                estimated_tokens={key: int(value) for key, value in estimated.items()},
                budget=summary,
                carry_from_window_id=previous_window_id,
                carry_last_quote_id=previous_quote_id,
            )
        )
        previous_quote_id = group[-1].quote_id
        previous_window_id = window_id

    covered = [quote_id for window in windows for quote_id in window.target_quote_ids]
    if sorted(covered) != sorted(set(target_quote_ids) - set(missing)):
        warnings.append("target_coverage_gap")

    plan_hash = _hash(
        inputs.book_version_id,
        sorted(target_quote_ids),
        [window.dependency_hash for window in windows],
        policy_version,
        inputs.prompt_version,
        inputs.reading_mode.value,
        inputs.visible_horizon_cp if inputs.reading_mode is ReadingMode.INITIAL else None,
    )
    stats = {
        "windows": len(windows),
        "targets": len(covered),
        "oversized_targets": sum(
            1 for window in windows if any(w.startswith(OVERSIZED_WARNING) for w in window.warnings)
        ),
        "omitted_fragments": sum(len(window.omitted) for window in windows),
        "total_context_tokens": sum(
            window.budget["context_tokens"] for window in windows
        ),
    }
    return WindowPlan(
        windows=tuple(windows),
        warnings=tuple(warnings),
        dependency_hash=plan_hash,
        policy=inputs.policy,
        policy_version=policy_version,
        stats=stats,
    )


def missing_warnings(missing: Sequence[str]) -> list[str]:
    return [f"unknown_target:{quote_id}" for quote_id in missing]


def estimate_window_text(inputs: WindowInputs, window: ProcessingWindow) -> int:
    """窗口正文的 token 估算（与账本同一口径，供测试与 UI 复核）。"""

    return sum(estimate_tokens(fragment.text, inputs.estimator) for fragment in window.fragments)
