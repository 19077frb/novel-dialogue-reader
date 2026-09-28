"""单元测试：离线证据账（压缩删了什么、是否删到金标准 must_keep）。

用仓库内的原创合成样例（tmp_path）验证：压缩确实丢行文时会逐段留痕，
金标准 must_keep 与丢失区间重叠时会被计成 must_keep_violations；
关闭压缩（context-1）时不丢任何原文。
"""

from __future__ import annotations

import json
from pathlib import Path

from ndr.evaluation.loss import loss_report

OPENING = "「雨停了。」"
CLOSING = "「……谢谢。」"
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
GAP = "".join([*HEAD, *MIDDLE, CUE, *TAIL])
TEXT = f"{OPENING}{GAP}{CLOSING}"
DROPPED_MARKER = "她把收好的雨伞靠在门边"


def _write_fixture(tmp_path: Path) -> Path:
    text_path = tmp_path / "text.txt"
    text_path.write_text(TEXT, encoding="utf-8")
    gap_start = len(OPENING)
    gap_end = len(OPENING) + len(GAP)
    marker_start = TEXT.index(DROPPED_MARKER)
    gold = {
        "schema_version": "1.0",
        "book": {"book_id": "b-loss", "work_id": "w-loss"},
        "quotes": [
            {"quote_id": "c1", "start_cp": 0, "end_cp": len(OPENING)},
            {"quote_id": "c2", "start_cp": gap_end, "end_cp": len(TEXT)},
        ],
        "gaps": [
            {
                "gap_id": "g1",
                "left_quote_id": "c1",
                "right_quote_id": "c2",
                "decision": "CONTINUE",
                # 金标准声明这句叙述必须保留；压缩如果丢掉它就必须被计成删错
                "must_keep": [
                    {"start_cp": marker_start, "end_cp": marker_start + len(DROPPED_MARKER)}
                ],
            }
        ],
    }
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(json.dumps(gold, ensure_ascii=False), encoding="utf-8")
    manifest = {
        "manifest_version": "1.0",
        "works": [
            {
                "work_id": "w-loss",
                "title": "合成样例",
                "split": "dev",
                "books": [
                    {
                        "book_id": "b-loss",
                        "text": "text.txt",
                        "gold": "gold.json",
                        "format": "TXT",
                        "hard_cases": ["long_gap"],
                    }
                ],
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    assert gap_start == len(OPENING)
    return manifest_path


def test_loss_report_records_dropped_spans_and_must_keep_conflicts(tmp_path: Path) -> None:
    manifest_path = _write_fixture(tmp_path)
    report = loss_report(manifest_path, context_policy="context-2")

    assert report["gap_compression"] is True
    summary = report["summary"]
    assert summary["gaps_scanned"] == 1
    assert summary["compressed_gaps"] == 1
    assert summary["dropped_spans"] >= 1
    assert summary["dropped_cp"] > 0
    assert summary["must_keep_violations"] >= 1

    book = report["books"][0]
    assert book["gap_source"] == "gold"
    assert any(DROPPED_MARKER in item["text"] for item in book["drops"])
    hit = next(item for item in book["drops"] if item["must_keep_hits"])
    assert hit["looks_like_evidence"] is False
    assert hit["tokens"] > 0


def test_loss_report_is_empty_when_compression_is_off(tmp_path: Path) -> None:
    manifest_path = _write_fixture(tmp_path)
    report = loss_report(manifest_path, context_policy="context-1")

    assert report["gap_compression"] is False
    assert report["summary"]["dropped_spans"] == 0
    assert report["summary"]["must_keep_violations"] == 0
