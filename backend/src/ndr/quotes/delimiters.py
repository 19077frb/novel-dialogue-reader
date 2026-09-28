"""引号/括号配对表。

扫描器只提出候选：除了排版约定足够明确的《》〈〉（引用/标题）与()（括注）之外，
其余引号**不给类型提示**（kind_hint 为 None）——「」里可能是对白、心声或强调，
说话人类型必须等模型/人工判定，扫描器不猜。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.enums import QuoteKind


@dataclass(frozen=True)
class DelimiterPair:
    opening: str
    closing: str
    name: str
    kind_hint: QuoteKind | None = None

    def hint_for_depth(self, depth: int) -> QuoteKind | None:
        """『』在嵌套里通常是被引用的内容；单独出现时仍可能是对白，因此不给提示。"""

        if self.name == "corner_bracket_double" and depth > 0:
            return QuoteKind.QUOTATION
        return self.kind_hint


DELIMITER_PAIRS: tuple[DelimiterPair, ...] = (
    DelimiterPair("「", "」", "corner_bracket"),
    DelimiterPair("『", "』", "corner_bracket_double"),
    DelimiterPair("“", "”", "curly_double"),
    DelimiterPair("‘", "’", "curly_single"),
    DelimiterPair("〈", "〉", "angle_single", QuoteKind.QUOTATION),
    DelimiterPair("《", "》", "angle_double", QuoteKind.QUOTATION),
    DelimiterPair("（", "）", "paren_fullwidth", QuoteKind.OTHER),
    DelimiterPair("(", ")", "paren_ascii", QuoteKind.OTHER),
)

OPENING_TO_PAIR: dict[str, DelimiterPair] = {pair.opening: pair for pair in DELIMITER_PAIRS}
CLOSING_TO_PAIR: dict[str, DelimiterPair] = {pair.closing: pair for pair in DELIMITER_PAIRS}
