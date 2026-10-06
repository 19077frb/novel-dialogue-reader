"""Complete source paragraphs/turn blocks, not certified semantic scenes."""

from array import array
from bisect import bisect_left, bisect_right

from ..domain.enums import ReadingMode
from .budget import BudgetItemKind, TokenEstimator, _is_cjk_like
from .source_selection import ContextFragment


def dialogue_units(inputs, start, end, targets, *, separator, error):
    """Keep complete turns and account for every actual fragment, including overlap.

    Content-node whitespace belongs to its preceding paragraph. Consecutive
    quote paragraphs stay together; a budget boundary is never a scene change.
    """
    budget = inputs.policy.context_tokens
    if type(budget) is not int or budget <= 0:
        raise error("完整对话块需要正整数正文预算")
    if not targets:
        return ()
    quotes = sorted(
        (
            q
            for q in inputs.quotes
            if q.nesting_depth == 0 and q.start_cp < end and q.end_cp > start
        ),
        key=lambda q: q.start_cp,
    )
    if any(q.start_cp < start or q.end_cp > end for q in quotes):
        raise error("完整对话块范围不能截断原文引语")
    if any(a.end_cp > b.start_cp for a, b in zip(quotes, quotes[1:], strict=False)):
        raise error("完整对话块的原文引语范围重叠")
    starts = [q.start_cp for q in quotes]
    ends = [q.end_cp for q in quotes]
    target_starts = [q.start_cp for q in targets]

    # Exact heuristic cost for arbitrary source slices in O(1). Every target
    # and intervening gap still rounds separately, just like the final ledger.
    # A custom estimator keeps its own semantics, rather than assuming additivity.
    cjk = array("I", [0])
    if type(inputs.estimator) is TokenEstimator:
        total = 0
        for char in inputs.canonical_text[start:end]:
            total += _is_cjk_like(char)
            cjk.append(total)

    def interval(a, b):
        if len(cjk) == 1:
            return inputs.estimator.estimate(inputs.canonical_text[a:b])
        count = cjk[b - start] - cjk[a - start]
        return count + (b - a - count + 3) // 4

    quote_cost, gap_cost = [0], [0]
    for index, q in enumerate(targets):
        quote_cost.append(quote_cost[-1] + interval(q.start_cp, q.end_cp))
        if index + 1 < len(targets):
            gap_cost.append(gap_cost[-1] + interval(q.end_cp, targets[index + 1].start_cp))

    def body_cost(a, b):
        left, right = bisect_left(target_starts, a), bisect_left(target_starts, b)
        if left == right:
            return interval(a, b)
        return (
            quote_cost[right]
            - quote_cost[left]
            + gap_cost[right - 1]
            - gap_cost[left]
            + interval(a, targets[left].start_cp)
            + interval(targets[right - 1].end_cp, b)
        )

    if body_cost(start, end) <= budget:
        return ((start, end, ()),)

    boundaries = {start, end}
    nodes = [p for p in inputs.paragraphs if start <= p.start_cp < end]
    boundaries.update(p.start_cp for p in nodes)
    if not nodes:
        position = start
        for line in inputs.canonical_text[start:end].splitlines(keepends=True):
            if line.strip():
                boundaries.add(position)
            position += len(line)
    position = start
    explicit = set()
    for line in inputs.canonical_text[start:end].splitlines(keepends=True):
        position += len(line)
        if start < position < end and separator.fullmatch(line.strip()):
            boundaries.add(position)
            explicit.add(position)
    safe = []
    for boundary in sorted(boundaries):
        index = bisect_right(starts, boundary - 1) - 1
        if index < 0 or not quotes[index].start_cp < boundary < quotes[index].end_cp:
            safe.append(boundary)
    explicit.intersection_update(safe)
    paragraphs = list(zip(safe, safe[1:], strict=False))
    blocks = []
    for a, b in paragraphs:
        index = bisect_right(ends, a)
        has_quote = index < len(quotes) and quotes[index].end_cp <= b
        if blocks and has_quote and blocks[-1][2] and a not in explicit:
            blocks[-1] = (blocks[-1][0], b, True)
        else:
            blocks.append((a, b, has_quote))
    before = {b: (a, b) for a, b in paragraphs}
    after = {a: (a, b) for a, b in paragraphs}

    def overlap(a, b):
        ranges = [before[a]] if a > start else []
        if b < end and inputs.reading_mode is ReadingMode.REREAD:
            ranges.append(after[b])
        return tuple(
            ContextFragment(
                f"boundary:{x}:{y}",
                BudgetItemKind.OVERLAP,
                x,
                y,
                inputs.canonical_text[x:y],
                "complete_boundary_source",
            )
            for x, y in ranges
        )

    units, index = [], 0
    while index < len(blocks):
        a, picked, following = blocks[index][0], None, index
        while following < len(blocks):
            if following > index and blocks[following][0] in explicit:
                break
            b = blocks[following][1]
            body_tokens = body_cost(a, b)
            if body_tokens > budget:
                break
            context = overlap(a, b)
            if body_tokens + sum(interval(f.start_cp, f.end_cp) for f in context) <= budget:
                picked = (a, b, context, following + 1)
            following += 1
        if picked is None:
            raise error(
                f"完整对话块及边界原文（字符位置{a}至{blocks[index][1]}）超过正文预算；"
                "请缩小范围或使用更长上下文，未截断正文，也未调用模型"
            )
        a, b, context, index = picked
        units.append((a, b, context))
    return tuple(units)
