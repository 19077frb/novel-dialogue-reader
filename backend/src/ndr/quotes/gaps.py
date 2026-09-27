"""Gap 构造（DEVELOPMENT.md 4.2）。

Gap 以**相邻的外层候选对白**为锚，包含中间叙述：

- 只用 ``nesting_depth == 0`` 的候选，嵌套引用不会自动成为新的发言轮次。
- 相邻但紧挨着的候选（中间没有正文）不产生 Gap。
- **不按章节截断**：跨章节也照常产生，Gap 是否结束场景由 T09 判断。
- 扫描器不猜 Gap 语义，``decision`` 一律是 ``UNCERTAIN``。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.enums import GapDecision
from .ids import gap_id_for
from .scanner import ScannedQuote


@dataclass(frozen=True)
class ScannedGap:
    gap_id: str
    left_quote_id: str
    right_quote_id: str
    start_cp: int
    end_cp: int
    decision: GapDecision

    @property
    def length_cp(self) -> int:
        return self.end_cp - self.start_cp


def build_gaps(
    canonical_text: str,
    quotes: tuple[ScannedQuote, ...],
    *,
    book_version_id: str,
    scanner_version: str,
) -> tuple[ScannedGap, ...]:
    """在相邻外层候选之间构造 Gap（中间必须有实际叙述文本）。"""

    top_level = sorted(
        (quote for quote in quotes if quote.nesting_depth == 0), key=lambda quote: quote.start_cp
    )
    gaps: list[ScannedGap] = []
    for left, right in zip(top_level, top_level[1:], strict=False):
        start_cp = left.end_cp
        end_cp = right.start_cp
        if end_cp <= start_cp:
            continue
        narration = canonical_text[start_cp:end_cp]
        if not narration.strip():
            continue
        gaps.append(
            ScannedGap(
                gap_id=gap_id_for(book_version_id, start_cp, end_cp, scanner_version),
                left_quote_id=left.quote_id,
                right_quote_id=right.quote_id,
                start_cp=start_cp,
                end_cp=end_cp,
                decision=GapDecision.UNCERTAIN,
            )
        )
    return tuple(gaps)
