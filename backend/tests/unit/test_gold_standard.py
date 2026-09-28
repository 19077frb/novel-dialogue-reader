"""单元测试：金标准校验、模板生成与候选覆盖率。

这些检查只读文件，不触碰数据库、不调用模型。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from ndr.config import REPO_ROOT
from ndr.evaluation.gold_standard import (
    build_template,
    check_against_scanner,
    check_references,
    load_gold_standard,
    read_canonical_text,
    summarize,
    validate_schema,
)

EXAMPLE_DIR = REPO_ROOT / "evaluation" / "examples" / "minimal-txt-001"

SAMPLE_TEXT = (
    "第一章 雨夜\n"
    "「雨停了。」少女合上伞。\n"
    "少年没有回答。\n"
    "「……谢谢。」她低声说。\n"
)


def _load_cli():  # noqa: ANN202
    path = REPO_ROOT / "backend" / "scripts" / "gold_standard.py"
    spec = importlib.util.spec_from_file_location("ndr_gold_standard_cli", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _errors(issues) -> list:  # noqa: ANN001
    return [issue for issue in issues if issue.is_error]


def test_example_gold_standard_is_valid() -> None:
    gold = load_gold_standard(EXAMPLE_DIR / "gold.json")
    text = (EXAMPLE_DIR / "text.txt").read_text(encoding="utf-8")

    assert _errors(validate_schema(gold)) == []
    assert _errors(check_references(gold, canonical_text=text)) == []

    issues, coverage = check_against_scanner(gold, text)
    assert _errors(issues) == []
    assert coverage["gold_quotes"] == 5
    assert coverage["matched"] == 5  # 扫描器与示例样例的坐标完全一致
    assert coverage["missing"] == 0


def test_template_round_trip_passes_checks(tmp_path: Path) -> None:
    template = build_template(SAMPLE_TEXT, book_id="template-1", title="模板样例")

    assert _errors(validate_schema(template)) == []
    assert _errors(check_references(template, canonical_text=SAMPLE_TEXT)) == []
    assert all(quote["resolvable"] is False for quote in template["quotes"])
    assert all(quote["group_id"] is None for quote in template["quotes"])
    assert [quote["start_cp"] for quote in template["quotes"]] == sorted(
        quote["start_cp"] for quote in template["quotes"]
    )
    # Gap 引用的候选 ID 必须存在
    quote_ids = {quote["quote_id"] for quote in template["quotes"]}
    for gap in template["gaps"]:
        assert gap["left_quote_id"] in quote_ids
        assert gap["right_quote_id"] in quote_ids

    path = tmp_path / "template.json"
    path.write_text(json.dumps(template, ensure_ascii=False), encoding="utf-8")
    assert load_gold_standard(path)["book"]["book_id"] == "template-1"


def test_unknown_scene_reference_is_reported() -> None:
    gold = {
        "schema_version": "1.0",
        "book": {
            "book_id": "b",
            "work_id": "w",
            "title": "t",
            "format": "TXT",
            "language": "zh",
            "canonical_sha256": "0" * 64,
            "canonical_length_cp": 100,
        },
        "annotator": {
            "annotator_id": "a",
            "annotated_at": "2026-09-28T00:00:00+00:00",
            "guideline_version": "1.0",
        },
        "scenes": [{"scene_id": "scene_1", "start_cp": 0, "end_cp": 100, "participants": []}],
        "quotes": [
            {
                "quote_id": "q1",
                "scene_id": "scene_missing",
                "start_cp": 0,
                "end_cp": 5,
                "kind": "speech",
                "group_id": None,
                "resolvable": False,
                "evidence_refs": [],
            }
        ],
        "gaps": [],
    }
    codes = {issue.code for issue in _errors(check_references(gold))}
    assert "unknown_scene" in codes


def test_unresolvable_quote_with_group_is_reported() -> None:
    gold = load_gold_standard(EXAMPLE_DIR / "gold.json")
    gold["quotes"][2]["group_id"] = "S1"  # q3 是 resolvable=false
    codes = {issue.code for issue in _errors(check_references(gold))}
    assert "unresolvable_with_group" in codes
    assert "schema_violation" in {issue.code for issue in _errors(validate_schema(gold))}


def test_out_of_bounds_quote_is_reported() -> None:
    gold = load_gold_standard(EXAMPLE_DIR / "gold.json")
    gold["quotes"][0]["end_cp"] = gold["book"]["canonical_length_cp"] + 10
    codes = {issue.code for issue in _errors(check_references(gold))}
    assert "quote_range_out_of_bounds" in codes


def test_group_not_in_scene_is_reported() -> None:
    gold = load_gold_standard(EXAMPLE_DIR / "gold.json")
    gold["quotes"][0]["group_id"] = "S9"
    codes = {issue.code for issue in _errors(check_references(gold))}
    assert "group_not_in_scene" in codes


def test_scanner_coverage_reports_missing_quotes() -> None:
    gold = build_template(SAMPLE_TEXT, book_id="b1")
    # 把一条候选改到没有引号的位置 → 扫描器覆盖不到
    gold["quotes"][0]["start_cp"] = 0
    gold["quotes"][0]["end_cp"] = 3  # "第一章" 不是引号内容

    issues, coverage = check_against_scanner(gold, SAMPLE_TEXT)
    assert coverage["missing"] >= 1
    assert any(issue.code == "quote_missing_from_candidates" for issue in issues)


def test_summarize_counts_unresolvable_and_kinds() -> None:
    gold = load_gold_standard(EXAMPLE_DIR / "gold.json")
    summary = summarize(gold)
    assert summary["quotes"] == 5
    assert summary["resolvable_quotes"] == 4
    assert summary["unresolvable_quotes"] == 1
    assert summary["kinds"]["speech"] == 5


def test_read_canonical_text_detects_gb18030(tmp_path: Path) -> None:
    path = tmp_path / "gb18030.txt"
    path.write_bytes(SAMPLE_TEXT.encode("gb18030"))
    text, encoding = read_canonical_text(path)
    assert encoding == "gb18030"
    assert text == SAMPLE_TEXT


def test_cli_validate_and_scan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli = _load_cli()

    # 通过：示例样例
    exit_code = cli.main(
        [
            "validate",
            "--gold",
            str(EXAMPLE_DIR / "gold.json"),
            "--text",
            str(EXAMPLE_DIR / "text.txt"),
            "--json",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["candidate_coverage"]["matched"] == 5

    # 失败：把范围改坏 → 非零退出
    broken = tmp_path / "broken.json"
    gold = load_gold_standard(EXAMPLE_DIR / "gold.json")
    gold["quotes"][0]["end_cp"] = 999999
    broken.write_text(json.dumps(gold, ensure_ascii=False), encoding="utf-8")
    assert cli.main(["validate", "--gold", str(broken)]) == 1
    capsys.readouterr()  # 丢弃上一次的（人类可读）输出，避免与下面的 JSON 混在一起

    # 离线扫描：输出候选与统计
    text_path = tmp_path / "sample.txt"
    text_path.write_text(SAMPLE_TEXT, encoding="utf-8")
    assert cli.main(["scan", "--text", str(text_path), "--json"]) == 0
    scan_payload = json.loads(capsys.readouterr().out)
    assert scan_payload["stats"]["quotes"] == 2
    assert scan_payload["quotes"][0]["text"] == "「雨停了。」"
