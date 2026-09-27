"""T17 离线证据账：记录长 Gap 压缩**实际丢掉了哪些原文**，以及是否删到金标准必留片段。

只做本地计算（清单 + 金标准 + 原文），不调用模型：在没有真实凭据之前也能留下
「这次压缩丢了什么」的可复现记录，供人工复核。

如实说明：

- 优先用金标准 ``gaps[]`` 的区间；没有可用的 Gap 记录时退回「相邻对白之间」的近似，
  并在报告里标成 ``gap_source=approximation``；
- ``must_keep`` 是金标准声明的「上下文筛选必须保留的证据片段」，因此
  ``must_keep_violations`` 是**删错**的离线计数；它只说明删错，不说明压缩有没有收益。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..context.budget import estimate_tokens, policy_for_version
from ..context.source_selection import compress_gap_spans, looks_like_evidence
from .gold_standard import read_canonical_text
from .manifest import load_manifest

LOSS_REPORT_VERSION = "context-loss-1"


def _quote_spans(gold: dict[str, Any]) -> dict[str, tuple[int, int]]:
    spans: dict[str, tuple[int, int]] = {}
    for row in gold.get("quotes") or []:
        quote_id = row.get("quote_id")
        if quote_id is None:
            continue
        spans[str(quote_id)] = (int(row["start_cp"]), int(row["end_cp"]))
    return spans


def _overlaps(start_cp: int, end_cp: int, item: dict[str, Any]) -> bool:
    return int(item["start_cp"]) < end_cp and int(item["end_cp"]) > start_cp


def _gold_gaps(gold: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """金标准 Gap（推荐）；缺失时退回相邻对白之间的近似区间。"""

    spans = _quote_spans(gold)
    gaps: list[dict[str, Any]] = []
    for row in gold.get("gaps") or []:
        left = spans.get(str(row.get("left_quote_id")))
        right = spans.get(str(row.get("right_quote_id")))
        if left is None or right is None or right[0] <= left[1]:
            continue
        gaps.append(
            {
                "gap_id": str(row.get("gap_id")),
                "start_cp": left[1],
                "end_cp": right[0],
                "must_keep": list(row.get("must_keep") or []),
            }
        )
    if gaps:
        return gaps, "gold"
    ordered = sorted(
        [row for row in (gold.get("quotes") or [])], key=lambda row: int(row["start_cp"])
    )
    for left, right in zip(ordered, ordered[1:], strict=False):
        if int(right["start_cp"]) <= int(left["end_cp"]):
            continue
        gaps.append(
            {
                "gap_id": f"{left['quote_id']}..{right['quote_id']}",
                "start_cp": int(left["end_cp"]),
                "end_cp": int(right["start_cp"]),
                "must_keep": [],
            }
        )
    return gaps, "approximation"


def loss_report(
    manifest_path: str | Path,
    *,
    context_policy: str = "context-2",
    splits: list[str] | None = None,
) -> dict[str, Any]:
    """按策略重放长 Gap 压缩，列出丢掉的行文与金标准 ``must_keep`` 冲突。"""

    manifest = load_manifest(manifest_path)
    policy = policy_for_version(context_policy)
    books: list[dict[str, Any]] = []
    totals = {
        "gaps_scanned": 0,
        "compressed_gaps": 0,
        "dropped_spans": 0,
        "dropped_cp": 0,
        "must_keep_violations": 0,
    }
    for book in manifest.books(splits):
        text, _encoding = read_canonical_text(book.text_path)
        gold = json.loads(Path(book.gold_path).read_text(encoding="utf-8"))
        gaps, gap_source = _gold_gaps(gold)
        dropped_records: list[dict[str, Any]] = []
        compressed_gaps = 0
        for gap in gaps:
            width = gap["end_cp"] - gap["start_cp"]
            if not policy.gap_compression or width <= policy.gap_compression_threshold_cp:
                continue
            _kept, dropped = compress_gap_spans(
                text[gap["start_cp"] : gap["end_cp"]],
                threshold_cp=policy.gap_compression_threshold_cp,
                margin_sentences=policy.gap_compression_margin_sentences,
                max_ratio=policy.gap_compression_max_ratio,
            )
            if not dropped:
                continue
            compressed_gaps += 1
            for rel_start, rel_end, span_text in dropped:
                start_cp = gap["start_cp"] + rel_start
                end_cp = gap["start_cp"] + rel_end
                hits = [
                    item for item in gap["must_keep"] if _overlaps(start_cp, end_cp, item)
                ]
                dropped_records.append(
                    {
                        "gap_id": gap["gap_id"],
                        "start_cp": start_cp,
                        "end_cp": end_cp,
                        "text": span_text,
                        "tokens": estimate_tokens(span_text),
                        "looks_like_evidence": looks_like_evidence(span_text),
                        "must_keep_hits": hits,
                    }
                )
        violations = sum(1 for item in dropped_records if item["must_keep_hits"])
        books.append(
            {
                "book_id": book.book_id,
                "work_id": book.work_id,
                "gap_source": gap_source,
                "gaps_scanned": len(gaps),
                "compressed_gaps": compressed_gaps,
                "dropped_spans": len(dropped_records),
                "dropped_cp": sum(item["end_cp"] - item["start_cp"] for item in dropped_records),
                "must_keep_violations": violations,
                "drops": dropped_records,
            }
        )
        totals["gaps_scanned"] += len(gaps)
        totals["compressed_gaps"] += compressed_gaps
        totals["dropped_spans"] += len(dropped_records)
        totals["dropped_cp"] += sum(
            item["end_cp"] - item["start_cp"] for item in dropped_records
        )
        totals["must_keep_violations"] += violations
    return {
        "report_version": LOSS_REPORT_VERSION,
        "manifest": str(manifest_path),
        "context_policy": context_policy,
        "policy": policy.as_key(),
        "gap_compression": policy.gap_compression,
        "books": books,
        "summary": totals,
        "notes": (
            "这是离线证据账：只说明压缩删了什么、是否删到金标准 must_keep，"
            "不能替代真实准确率/覆盖率/费用对比。"
        ),
    }
