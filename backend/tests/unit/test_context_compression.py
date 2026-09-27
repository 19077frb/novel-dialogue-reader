"""T17 单元测试：长 Gap 保守筛选、证据补回与策略版本（context-1 / context-2）。

门槛相关：

- 压缩只在**真的省得下来**时发生，只丢叙述、保留可能揭示说话人的句子（含前后各补一句）；
- 丢掉的行文必须留痕（``OmittedRecord(reason="gap_compression")``），保留 + 丢弃恰好等于原文：
  既不丢字符，也不编造内容；
- 策略版本进入窗口 ID、依赖哈希与缓存键：context-1 与 context-2 必须得到不同的键。
"""

from __future__ import annotations

from ndr.context.budget import COMPRESSED_POLICY, DEFAULT_POLICY, BudgetItemKind, policy_version_for
from ndr.context.source_selection import (
    COMPRESSION_OMIT_REASON,
    COMPRESSION_SKIPPED_WARNING,
    GapView,
    QuoteView,
    compress_gap_spans,
    select_evidence,
    split_sentences,
)
from ndr.context.window_builder import WindowInputs, plan_windows
from ndr.storage.cache import CacheKeyParts, compute_cache_key, fingerprint

OPENING = "「雨停了。」"
CLOSING = "「……谢谢。」"
# 12 句：首尾句与线索句的前后各一句会被保留，中间的长叙述才是可丢的部分
HEAD = ["雨点落在窗沿上。", "屋檐还在滴水。"]
MIDDLE = [
    "她把收好的雨伞靠在门边又看了一眼窗外。",
    "远处钟楼的钟声穿过雨后的空气慢慢传来。",
    "路灯把路边水洼照得发亮而街上很安静。",
    "两个人谁都没有先动只是站着。",
]
CUE = "少女低声说。"
TAIL = [
    "夜风带着凉意吹过走廊又吹动了门帘。",
    "街上的店铺都已经关了门。",
    "远处传来自行车经过的铃声。",
    "她把伞收好放在门边。",
    "两人站在门口没有动。",
]
LONG_GAP = "".join([*HEAD, *MIDDLE, CUE, *TAIL])
DROPPED_MARKER = "她把收好的雨伞靠在门边"
KEPT_MARKER = "夜风带着凉意吹过走廊"
CUE_HEAVY_GAP = CUE * 20


def _inputs_for(gap_text: str, policy) -> WindowInputs:  # noqa: ANN001
    text = f"{OPENING}{gap_text}{CLOSING}"
    quote_end = len(OPENING)
    closing_start = quote_end + len(gap_text)
    return WindowInputs(
        book_version_id="v1",
        canonical_text=text,
        quotes=[
            QuoteView("q1", 0, quote_end),
            QuoteView("q2", closing_start, closing_start + len(CLOSING)),
        ],
        gaps=[GapView("g1", quote_end, closing_start)],
        policy=policy,
    )


def _inputs(policy=DEFAULT_POLICY) -> WindowInputs:  # noqa: ANN001
    return _inputs_for(LONG_GAP, policy)


def _select(inputs: WindowInputs):  # noqa: ANN202
    return select_evidence(
        canonical_text=inputs.canonical_text,
        quotes=list(inputs.quotes),
        gaps=list(inputs.gaps),
        target_quote_ids=["q1", "q2"],
        policy=inputs.policy,
    )


def test_split_sentences_is_lossless() -> None:
    sentences = split_sentences(LONG_GAP)
    assert "".join(item[2] for item in sentences) == LONG_GAP
    assert len(sentences) == 12


def test_short_gap_is_never_compressed() -> None:
    short = LONG_GAP[:40]
    kept, dropped = compress_gap_spans(short, threshold_cp=80, margin_sentences=1, max_ratio=0.6)
    assert dropped == []
    assert kept == [(0, len(short), short)]


def test_long_gap_keeps_cues_and_drops_narration() -> None:
    kept, dropped = compress_gap_spans(LONG_GAP, threshold_cp=80, margin_sentences=1, max_ratio=0.6)
    kept_text = "".join(item[2] for item in kept)
    dropped_text = "".join(item[2] for item in dropped)

    assert dropped, "长叙述必须真的丢掉一些内容，否则不算压缩"
    assert CUE in kept_text  # 「少女低声说。」可能揭示说话人，必须保留
    assert DROPPED_MARKER in dropped_text  # 纯叙述被丢掉
    assert KEPT_MARKER in kept_text  # 线索句后补回一句

    spans = sorted([*kept, *dropped], key=lambda item: item[0])
    assert spans[0][0] == 0
    assert spans[-1][1] == len(LONG_GAP)
    for (_start, end, _text), (next_start, _end, _text2) in zip(spans, spans[1:], strict=False):
        assert end == next_start, "保留/丢弃片段必须首尾相接，不能凭空跳过原文"
    assert "".join(item[2] for item in spans) == LONG_GAP


def test_compression_is_abandoned_when_savings_are_too_small() -> None:
    kept, dropped = compress_gap_spans(
        CUE_HEAVY_GAP, threshold_cp=80, margin_sentences=1, max_ratio=0.6
    )
    assert dropped == []
    assert kept == [(0, len(CUE_HEAVY_GAP), CUE_HEAVY_GAP)]


def test_selection_records_dropped_span_and_keeps_evidence() -> None:
    inputs = _inputs(COMPRESSED_POLICY)
    result = _select(inputs)
    gap_start = next(gap.start_cp for gap in inputs.gaps)
    gap_end = next(gap.end_cp for gap in inputs.gaps)

    kept = [fragment for fragment in result.fragments if fragment.kind is BudgetItemKind.INNER_GAP]
    dropped = [record for record in result.omitted if record.reason == COMPRESSION_OMIT_REASON]
    assert len(kept) >= 2
    assert dropped
    assert all(record.kind == "inner_gap" for record in dropped)
    assert sum(record.tokens for record in dropped) > 0
    assert any("少女低声说" in fragment.text for fragment in kept)

    spans = sorted(
        [(fragment.start_cp, fragment.end_cp) for fragment in kept]
        + [(record.start_cp, record.end_cp) for record in dropped]
    )
    assert spans[0][0] == gap_start
    assert spans[-1][1] == gap_end
    for (_start, end), (next_start, _end) in zip(spans, spans[1:], strict=False):
        assert end == next_start


def test_selection_without_savings_warns_and_keeps_whole_gap() -> None:
    inputs = _inputs_for(CUE_HEAVY_GAP, COMPRESSED_POLICY)
    result = _select(inputs)
    assert any(warning.startswith(COMPRESSION_SKIPPED_WARNING) for warning in result.warnings)
    assert not [record for record in result.omitted if record.reason == COMPRESSION_OMIT_REASON]
    assert any(fragment.text == CUE_HEAVY_GAP for fragment in result.fragments)


def test_compression_disabled_by_default_keeps_whole_gap() -> None:
    inputs = _inputs(DEFAULT_POLICY)
    result = _select(inputs)
    assert any(fragment.fragment_id == "g1" for fragment in result.fragments)
    assert not result.omitted
    assert any(fragment.text == LONG_GAP for fragment in result.fragments)


def test_policy_version_changes_window_and_dependency_hash() -> None:
    conservative = plan_windows(_inputs(DEFAULT_POLICY), target_quote_ids=["q1", "q2"])
    compressed = plan_windows(_inputs(COMPRESSED_POLICY), target_quote_ids=["q1", "q2"])

    assert conservative.policy_version == "context-1"
    assert compressed.policy_version == "context-2"
    assert policy_version_for(compressed.policy) == compressed.policy_version
    assert conservative.dependency_hash != compressed.dependency_hash
    assert conservative.windows[0].window_id != compressed.windows[0].window_id
    assert (
        compressed.windows[0].budget["context_tokens"]
        < conservative.windows[0].budget["context_tokens"]
    )
    assert compressed.stats["omitted_fragments"] >= 1
    assert conservative.stats["omitted_fragments"] == 0


def test_policy_version_changes_cache_key() -> None:
    plans = [
        plan_windows(_inputs(policy), target_quote_ids=["q1", "q2"])
        for policy in (DEFAULT_POLICY, COMPRESSED_POLICY)
    ]
    keys = []
    for plan in plans:
        window = plan.windows[0]
        keys.append(
            compute_cache_key(
                CacheKeyParts(
                    book_version_id="v1",
                    target_ids=list(window.target_quote_ids),
                    input_fingerprint=fingerprint(
                        [fragment.text for fragment in window.fragments]
                    ),
                    model="m",
                    params={},
                    protocol_version="chat-completions-compatible",
                    prompt_version=window.prompt_version,
                    schema_version="1.0",
                    policy_version=window.policy_version,
                    dependency_hash=window.dependency_hash,
                    reading_mode=window.reading_mode.value,
                    visible_horizon_cp=window.visible_horizon_cp,
                )
            )
        )
    assert keys[0] != keys[1]
