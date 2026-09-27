"""B0 基线：**仅明确归属规则**（无 LLM，PLAN B0）。

规则（刻意保持简单、可解释，弱点也会被指标如实暴露）：

1. 场景：按引语之间的叙述长度切分——间隔超过 `scene_gap_cp` 视为新场景；否则沿用当前场景。
2. 归属：只看引语**紧随其后**的一段叙述（`lookahead_cp` 内），在其中找显式归属动词
   （「说/道/问/答/喊/叫…」），取动词前最多 6 个码点、以标点或空白结尾的名词短语作为说话人表面形式。
   同一个场景内，相同表面形式视为同一分组；找不到就**拒答**（UNKNOWN）。
3. 类型：B0 不分类，一律按 speech 处理。

表面形式规则会把「少女」与「她」当成两个分组——这是 B0 的真实弱点，评测会通过
`extra_groups` / pairwise 指标暴露，不做美化。
"""

from __future__ import annotations

from dataclasses import dataclass

RULE_BASELINE_VERSION = "b0-rule-1"
ATTRIBUTION_VERBS = (
    "低声说",
    "忽然说",
    "低声回答",
    "开口说道",
    "接着说",
    "回答说",
    "说道",
    "喊道",
    "说道",
    "说",
    "道",
    "问",
    "答",
    "喊",
    "叫",
)
NAME_BOUNDARY = "。！？；，、,.!?;「」『』（）()《》〈〉“”\"'：: \n\t…—"


@dataclass(frozen=True)
class RuleBaselineOptions:
    lookahead_cp: int = 40
    scene_gap_cp: int = 200
    max_name_cp: int = 6


def _surface_form(tail: str) -> tuple[str | None, str | None]:
    """在紧跟引语的叙述里找显式归属；返回 ``(说话人表面形式, 命中的动词)``。

    取**最早出现**且“表面形式干净”的候选：表面形式必须是不含标点/空白/换行的名词短语，
    因此「少年没有回答」这类叙述不会被误当成归属（这是 B0 保守性的来源）。
    """

    candidates: list[tuple[int, str, str]] = []
    for verb in ATTRIBUTION_VERBS:
        start = 0
        while True:
            index = tail.find(verb, start)
            if index < 0:
                break
            candidate = tail[:index]
            cursor = len(candidate)
            while cursor > 0 and candidate[cursor - 1] in NAME_BOUNDARY:
                cursor -= 1
            name = candidate[cursor - 6 : cursor]
            if name and not any(char in NAME_BOUNDARY for char in name):
                candidates.append((index, name, verb))
                break
            start = index + 1
    if not candidates:
        return None, None
    _index, name, verb = min(candidates, key=lambda item: (item[0], item[2]))
    return name, verb

def rule_baseline_predictions(
    *, book_id: str, text: str, quotes: list[dict], options: RuleBaselineOptions | None = None
) -> dict:
    """对一批候选引语跑 B0 规则；返回与金标准同坐标的预测结构。"""

    resolved = options or RuleBaselineOptions()
    ordered = sorted(quotes, key=lambda row: (int(row["start_cp"]), int(row["end_cp"])))
    rows: list[dict] = []
    scene_index = 0
    previous_end: int | None = None
    for quote in ordered:
        start = int(quote["start_cp"])
        end = int(quote["end_cp"])
        if previous_end is not None and start - previous_end > resolved.scene_gap_cp:
            scene_index += 1
        previous_end = max(previous_end or 0, end)
        tail = text[end : end + resolved.lookahead_cp]
        name, verb = _surface_form(tail)
        rows.append(
            {
                "quote_id": str(quote.get("quote_id")),
                "start_cp": start,
                "end_cp": end,
                "kind": "speech",
                "scene_key": f"b0-scene-{scene_index}",
                "group_key": name,
                "status": "ACCEPTED" if name else "UNKNOWN",
                "evidence": verb,
            }
        )
    return {
        "book_id": book_id,
        "provider": RULE_BASELINE_VERSION,
        "quality_evidence": False,
        "notes": "B0 规则基线（无 LLM）：只做显式归属规则，找不到就拒答。",
        "quotes": rows,
        # 没有调用任何模型：calls=0 是事实（不是“未知用量”）
        "usage": {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "provider": "none",
        },
    }
