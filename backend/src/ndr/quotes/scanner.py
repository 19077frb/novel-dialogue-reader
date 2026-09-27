"""引号扫描：只提出候选引语（DEVELOPMENT.md 4.2）。

规则：

- 用栈处理嵌套与跨段；记录 delimiter 与 nesting_depth，父引语通过开引号位置回填。
- **不给说话人、不做轮流分配、不做人名匹配**：本模块只输出位置、层级、配对与弱类型提示。
- 长度保护：单个候选超过 ``max_length_cp`` 个码点，或跨过超过 ``max_span_paragraphs`` 个换行，
  一律丢弃并给出警告；开引号停留过久也会被放弃（防止一个异常引号吞掉整章）。
- 收尾时仍未配对的开引号 / 找不到开引号的闭引号都写入警告，绝不静默忽略。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from ..domain.enums import QuoteKind
from .delimiters import CLOSING_TO_PAIR, OPENING_TO_PAIR, DelimiterPair
from .ids import quote_id_for

SCANNER_VERSION = "quote-scan-1"

DEFAULT_MAX_QUOTE_LENGTH_CP = 1200
DEFAULT_MAX_SPAN_PARAGRAPHS = 3
DEFAULT_MAX_NESTING_DEPTH = 8


@dataclass(frozen=True)
class ScanLimits:
    max_length_cp: int = DEFAULT_MAX_QUOTE_LENGTH_CP
    # 硬上限：开引号超过 4 倍长度上限仍未闭合就放弃（防止异常引号吞掉整章）。
    hard_ceiling_factor: int = 4
    max_span_paragraphs: int = DEFAULT_MAX_SPAN_PARAGRAPHS
    max_nesting_depth: int = DEFAULT_MAX_NESTING_DEPTH


@dataclass(frozen=True)
class ScanWarning:
    code: str
    position_cp: int
    delimiter: str
    detail: str


@dataclass(frozen=True)
class ScannedQuote:
    quote_id: str
    start_cp: int
    end_cp: int
    delimiter: str
    opening: str
    closing: str
    nesting_depth: int
    parent_quote_id: str | None
    kind_hint: QuoteKind | None

    @property
    def inner_start_cp(self) -> int:
        return self.start_cp + len(self.opening)

    @property
    def inner_end_cp(self) -> int:
        return self.end_cp - len(self.closing)

    @property
    def length_cp(self) -> int:
        return self.end_cp - self.start_cp


@dataclass(frozen=True)
class ScanResult:
    quotes: tuple[ScannedQuote, ...]
    warnings: tuple[ScanWarning, ...]
    stats: dict[str, int] = field(default_factory=dict)


@dataclass
class _OpenQuote:
    pair: DelimiterPair
    start_cp: int
    entry_id: int
    parent_entry_id: int | None


@dataclass
class _RawQuote:
    start_cp: int
    end_cp: int
    pair: DelimiterPair
    depth: int
    parent_start_cp: int | None


def _paragraph_breaks(text: str) -> int:
    return text.count("\n")


def scan_quotes(
    canonical_text: str,
    *,
    book_version_id: str,
    scanner_version: str = SCANNER_VERSION,
    limits: ScanLimits | None = None,
) -> ScanResult:
    """扫描候选引语；返回候选与警告（不涉及任何说话人判断）。"""

    limits = limits or ScanLimits()
    stack: list[_OpenQuote] = []
    raw_quotes: list[_RawQuote] = []
    warnings: list[ScanWarning] = []
    next_entry_id = 1
    abandoned = 0
    stray_closes = 0
    unclosed = 0
    depth_limit_hits = 0

    for index, char in enumerate(canonical_text):
        # 硬上限保护：开口太久仍未闭合的开引号直接放弃，避免吞掉后文。
        hard_ceiling = limits.max_length_cp * limits.hard_ceiling_factor
        while stack and index - stack[-1].start_cp > hard_ceiling:
            dropped = stack.pop(0)
            abandoned += 1
            unclosed += 1
            warnings.append(
                ScanWarning(
                    code="unclosed_quote",
                    position_cp=dropped.start_cp,
                    delimiter=dropped.pair.name,
                    detail=(
                        f"开引号在 {hard_ceiling} 码点（长度上限的 "
                        f"{limits.hard_ceiling_factor} 倍）内没有闭合，已放弃该候选。"
                    ),
                )
            )

        opening_pair = OPENING_TO_PAIR.get(char)
        if opening_pair is not None:
            if len(stack) >= limits.max_nesting_depth:
                depth_limit_hits += 1
                warnings.append(
                    ScanWarning(
                        code="nesting_too_deep",
                        position_cp=index,
                        delimiter=opening_pair.name,
                        detail=f"嵌套超过 {limits.max_nesting_depth} 层，按正文处理。",
                    )
                )
                continue
            stack.append(
                _OpenQuote(
                    pair=opening_pair,
                    start_cp=index,
                    entry_id=next_entry_id,
                    parent_entry_id=stack[-1].entry_id if stack else None,
                )
            )
            next_entry_id += 1
            continue

        closing_pair = CLOSING_TO_PAIR.get(char)
        if closing_pair is None:
            continue

        match_index = next(
            (
                position
                for position in range(len(stack) - 1, -1, -1)
                if stack[position].pair is closing_pair
            ),
            -1,
        )
        if match_index < 0:
            stray_closes += 1
            warnings.append(
                ScanWarning(
                    code="stray_close",
                    position_cp=index,
                    delimiter=closing_pair.name,
                    detail="闭引号没有对应的开引号，已忽略。",
                )
            )
            continue

        # 上面未闭合的开引号：记录警告并丢弃（不人为配平）。
        for orphan in stack[match_index + 1 :]:
            unclosed += 1
            warnings.append(
                ScanWarning(
                    code="unclosed_quote",
                    position_cp=orphan.start_cp,
                    delimiter=orphan.pair.name,
                    detail="该开引号在匹配到闭引号前被更外层引号关闭，已放弃该候选。",
                )
            )
        del stack[match_index + 1 :]

        opened = stack.pop()
        end_cp = index + 1
        depth = len(stack)
        raw_quotes.append(
            _RawQuote(
                start_cp=opened.start_cp,
                end_cp=end_cp,
                pair=opened.pair,
                depth=depth,
                parent_start_cp=stack[-1].start_cp if stack else None,
            )
        )

    for orphan in reversed(stack):
        unclosed += 1
        warnings.append(
            ScanWarning(
                code="unclosed_quote",
                position_cp=orphan.start_cp,
                delimiter=orphan.pair.name,
                detail="文本结束时仍未闭合，已放弃该候选。",
            )
        )
    stack.clear()

    # 长度与跨段保护：超限的候选不输出（但已经在上面的警告里留痕）。
    kept: list[_RawQuote] = []
    too_long = 0
    too_many_paragraphs = 0
    for raw in raw_quotes:
        length = raw.end_cp - raw.start_cp
        if length > limits.max_length_cp:
            too_long += 1
            warnings.append(
                ScanWarning(
                    code="quote_too_long",
                    position_cp=raw.start_cp,
                    delimiter=raw.pair.name,
                    detail=f"候选长度 {length} 超过上限 {limits.max_length_cp}，已丢弃。",
                )
            )
            continue
        breaks = _paragraph_breaks(canonical_text[raw.start_cp : raw.end_cp])
        if breaks > limits.max_span_paragraphs:
            too_many_paragraphs += 1
            warnings.append(
                ScanWarning(
                    code="quote_spans_too_many_paragraphs",
                    position_cp=raw.start_cp,
                    delimiter=raw.pair.name,
                    detail=(
                        f"候选跨了 {breaks} 个换行，超过上限 "
                        f"{limits.max_span_paragraphs}，已丢弃。"
                    ),
                )
            )
            continue
        kept.append(raw)

    id_by_start = {
        raw.start_cp: quote_id_for(
            book_version_id, raw.start_cp, raw.end_cp, scanner_version
        )
        for raw in kept
    }

    quotes = tuple(
        ScannedQuote(
            quote_id=id_by_start[raw.start_cp],
            start_cp=raw.start_cp,
            end_cp=raw.end_cp,
            delimiter=raw.pair.name,
            opening=raw.pair.opening,
            closing=raw.pair.closing,
            nesting_depth=raw.depth,
            parent_quote_id=(
                id_by_start.get(raw.parent_start_cp) if raw.parent_start_cp is not None else None
            ),
            kind_hint=raw.pair.hint_for_depth(raw.depth),
        )
        for raw in sorted(kept, key=lambda item: item.start_cp)
    )

    stats = {
        "quotes": len(quotes),
        "top_level_quotes": sum(1 for quote in quotes if quote.nesting_depth == 0),
        "warnings": len(warnings),
        "abandoned_open_quotes": abandoned,
        "stray_closes": stray_closes,
        "unclosed_quotes": unclosed,
        "dropped_too_long": too_long,
        "dropped_too_many_paragraphs": too_many_paragraphs,
        "depth_limit_hits": depth_limit_hits,
    }
    return ScanResult(quotes=quotes, warnings=tuple(warnings), stats=stats)


def longest_quote_length(quotes: Iterable[ScannedQuote]) -> int:
    return max((quote.length_cp for quote in quotes), default=0)
