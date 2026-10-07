"""Whole bounded chapter text, or explicit original separator units, never a summary."""

import re
from dataclasses import replace

from ..domain.enums import ReadingMode
from .budget import BudgetItemKind, BudgetLedger, policy_version_for
from .source_selection import ContextFragment
from .window_builder import ProcessingWindow, WindowPlan, _hash

SEPARATOR = re.compile(r"^\s*(?:\*{3,}|(?:\*\s+){2,}\*|[＊◇◆☆★※]{3,})\s*$")


class FullContextError(ValueError):
    pass


def _units(inputs, ranges):
    for start, end in ranges:
        if (
            inputs.estimator.estimate(inputs.canonical_text[start:end])
            <= inputs.policy.context_tokens
        ):
            yield start, end
            continue
        position, cuts = start, [start]
        for raw in inputs.canonical_text[start:end].splitlines(keepends=True):
            after = position + len(raw)
            if (
                start < after < end
                and SEPARATOR.fullmatch(raw.strip())
                and not any(quote.start_cp < after < quote.end_cp for quote in inputs.quotes)
            ):
                cuts.append(after)
            position = after
        cuts.append(end)
        yield from zip(cuts, cuts[1:], strict=False)


def _fragments(inputs, start, end, targets):
    cursor, fragments = start, []
    for quote in targets:
        if cursor < quote.start_cp:
            kind = BudgetItemKind.OUTER_GAP if cursor == start else BudgetItemKind.INNER_GAP
            fragments.append(
                ContextFragment(
                    f"source:{cursor}:{quote.start_cp}",
                    kind,
                    cursor,
                    quote.start_cp,
                    inputs.canonical_text[cursor : quote.start_cp],
                    "complete_source",
                )
            )
        fragments.append(
            ContextFragment(
                quote.quote_id,
                BudgetItemKind.TARGET_QUOTE,
                quote.start_cp,
                quote.end_cp,
                inputs.canonical_text[quote.start_cp : quote.end_cp],
                "target",
            )
        )
        cursor = quote.end_cp
    if cursor < end:
        fragments.append(
            ContextFragment(
                f"source:{cursor}:{end}",
                BudgetItemKind.OUTER_GAP,
                cursor,
                end,
                inputs.canonical_text[cursor:end],
                "complete_source",
            )
        )
    return tuple(fragments)


def plan_full_source(inputs, *, target_quote_ids):
    if inputs.policy.gap_compression:
        raise FullContextError("完整上下文策略不能同时压缩正文")
    if len(set(target_quote_ids)) != len(target_quote_ids):
        raise FullContextError("完整上下文目标不能重复")
    quotes = {q.quote_id: q for q in inputs.quotes}
    if any(q not in quotes for q in target_quote_ids):
        raise FullContextError("完整上下文包含不存在的目标对白")
    ordered = sorted((quotes[q] for q in target_quote_ids), key=lambda q: (q.start_cp, q.end_cp))
    if any(a.end_cp > b.start_cp for a, b in zip(ordered, ordered[1:], strict=False)):
        raise FullContextError("完整上下文目标范围重叠，不能截断或重复发送")
    ranges, previous = [], 0
    for start, end in inputs.source_ranges:
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(inputs.canonical_text)
            or start < previous
        ):
            raise FullContextError("完整上下文需要有序、不重叠且合法的章节范围")
        previous = end
        if inputs.reading_mode is ReadingMode.INITIAL and inputs.visible_horizon_cp is not None:
            end = min(end, inputs.visible_horizon_cp)
        if start < end:
            ranges.append((start, end))
    if ordered and any(
        not any(a <= q.start_cp and q.end_cp <= b for a, b in ranges) for q in ordered
    ):
        raise FullContextError("目标对白不在完整原文范围内，或超出初读可见位置")
    windows, covered = [], []
    version = policy_version_for(inputs.policy)
    units = []
    if inputs.policy.dialogue_blocks:
        from .dialogue_blocks import dialogue_units

        for start, end in ranges:
            targets = [q for q in ordered if start <= q.start_cp and q.end_cp <= end]
            units.extend(
                dialogue_units(
                    inputs,
                    start,
                    end,
                    targets,
                    separator=SEPARATOR,
                    error=FullContextError,
                )
            )
    else:
        units = [(start, end, ()) for start, end in _units(inputs, ranges)]
    for start, end, context in units:
        targets = [q for q in ordered if start <= q.start_cp and q.end_cp <= end]
        if not targets:
            continue
        fragments = _fragments(inputs, start, end, targets) + context
        ledger = BudgetLedger(policy=inputs.policy, estimator=inputs.estimator)
        for fragment in fragments:
            item = ledger.register(
                item_id=fragment.fragment_id,
                kind=fragment.kind,
                text=fragment.text,
                reason=fragment.reason,
            )
            item.included = True
        if ledger.context_tokens > inputs.policy.context_tokens:
            raise FullContextError(
                f"完整章节或分段（字符位置{start}至{end}）超过正文预算，"
                "请缩小范围或使用支持更长上下文的策略；未截断正文，也未调用模型"
            )
        target_ids = tuple(q.quote_id for q in targets)
        horizon = end if inputs.reading_mode is ReadingMode.INITIAL else None
        dependency = _hash(
            inputs.book_version_id,
            target_ids,
            start,
            end,
            [(f.fragment_id, f.text) for f in fragments],
            inputs.policy.as_key(),
            inputs.prompt_version,
            inputs.reading_mode.value,
            horizon,
            inputs.scene_ref,
            sorted(inputs.speaker_refs),
        )
        window_id = (
            "w"
            + _hash(
                inputs.book_version_id,
                target_ids,
                start,
                end,
                inputs.policy.as_key(),
                inputs.prompt_version,
                inputs.reading_mode.value,
                horizon,
                inputs.scene_ref,
            )[:16]
        )
        summary = ledger.summary()
        summary["source_range"] = [start, end]
        if inputs.policy.dialogue_blocks:
            summary["boundary_ranges"] = [[f.start_cp, f.end_cp] for f in context]
        estimates = {
            key: summary[key]
            for key in (
                "context_tokens",
                "prompt_tokens",
                "output_tokens",
                "total_tokens",
            )
        }
        estimates.update(
            target_tokens=sum(
                inputs.estimator.estimate(inputs.canonical_text[q.start_cp : q.end_cp])
                for q in targets
            ),
            overlap_tokens=sum(inputs.estimator.estimate(f.text) for f in context),
        )
        window = ProcessingWindow(
            window_id,
            target_ids,
            fragments,
            (),
            (),
            inputs.scene_ref,
            inputs.reading_mode,
            horizon,
            inputs.prompt_version,
            version,
            dependency,
            estimates,
            summary,
        )
        if windows:
            window = replace(
                window,
                carry_from_window_id=windows[-1].window_id,
                carry_last_quote_id=windows[-1].target_quote_ids[-1],
            )
        windows.append(window)
        covered.extend(target_ids)
    if covered != [q.quote_id for q in ordered]:
        raise FullContextError("完整原文分段没有覆盖全部目标，未提交不完整计划")
    return WindowPlan(
        tuple(windows),
        (),
        _hash(
            inputs.book_version_id,
            [w.dependency_hash for w in windows],
            version,
            inputs.policy.as_key(),
        ),
        inputs.policy,
        version,
        {
            "windows": len(windows),
            "targets": len(covered),
            "oversized_targets": 0,
            "omitted_fragments": 0,
            "total_context_tokens": sum(w.budget["context_tokens"] for w in windows),
        },
    )
