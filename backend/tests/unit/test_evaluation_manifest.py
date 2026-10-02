"""单元测试：清单校验、B0 规则基线与配置指纹。"""

from __future__ import annotations

import json
from pathlib import Path

from ndr.evaluation.baselines import RuleBaselineOptions, rule_baseline_predictions
from ndr.evaluation.configs import load_config
from ndr.evaluation.manifest import load_manifest, validate_manifest
from ndr.evaluation.runner import validate_command

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = REPO_ROOT / "evaluation" / "manifests" / "dev.json"


def test_shipped_manifest_validates() -> None:
    code, payload = validate_command(MANIFEST)
    assert code == 0, payload
    assert payload["books"] == 1
    assert payload["splits"] == ["dev"]
    assert payload["errors"] == []


def test_manifest_rejects_work_in_two_splits_and_missing_gold(tmp_path: Path) -> None:
    text = tmp_path / "a.txt"
    text.write_text("第一章\n「甲。」他说。\n", encoding="utf-8")
    manifest = {
        "manifest_version": "1.0",
        "works": [
            {
                "work_id": "w1",
                "split": "dev",
                "books": [
                    {"book_id": "b1", "text": str(text), "gold": str(tmp_path / "missing.json")}
                ],
            },
            {
                "work_id": "w1",
                "split": "test",
                "books": [
                    {"book_id": "b1", "text": str(text), "gold": str(tmp_path / "missing.json")}
                ],
            },
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    loaded = load_manifest(path)
    issues = validate_manifest(loaded)
    codes = {issue.code for issue in issues}
    assert "work_in_multiple_splits" in codes  # 作品级划分不能重叠
    assert "duplicate_book_id" in codes
    assert "missing_gold" in codes
    assert any(issue.is_error for issue in issues)


def test_rule_baseline_extracts_clean_attribution_and_refuses_otherwise() -> None:
    text = (
        "「雨停了。」少女合上伞。\n"
        "少年没有回答，只是把外套递了过去。\n"
        "「……谢谢。」她低声说。\n"
        "「不用谢。」\n"
        "远处传来钟声。\n"
        "「明天见。」少年忽然说。\n"
    )
    quotes = [
        {"quote_id": "q1", "start_cp": 0, "end_cp": 6},
        {"quote_id": "q2", "start_cp": text.index("「……谢谢。」"), "end_cp": text.index("她低声说")},
        {"quote_id": "q3", "start_cp": text.index("「不用谢。」"), "end_cp": text.index("「不用谢。」") + 6},
        {"quote_id": "q4", "start_cp": text.index("「明天见。」"), "end_cp": text.index("少年忽然说")},
    ]
    prediction = rule_baseline_predictions(book_id="b1", text=text, quotes=quotes)
    rows = {row["quote_id"]: row for row in prediction["quotes"]}

    # 「她低声说」/「少年忽然说」是干净归属；「少年没有回答」不是（含标点）
    assert rows["q2"]["group_key"] is not None
    assert rows["q2"]["status"] == "ACCEPTED"
    assert rows["q4"]["group_key"] == "少年"
    assert rows["q1"]["group_key"] is None
    assert rows["q1"]["status"] == "UNKNOWN"
    assert rows["q3"]["group_key"] is None
    # 规则基线没有调用任何模型：calls=0 是事实
    assert prediction["usage"]["calls"] == 0
    assert prediction["quality_evidence"] is False


def test_rule_baseline_splits_scenes_by_long_gap() -> None:
    text = "「甲。」\n" + "叙" * 300 + "\n「乙。」\n"
    quotes = [
        {"quote_id": "q1", "start_cp": 0, "end_cp": 4},
        {"quote_id": "q2", "start_cp": text.index("「乙。」"), "end_cp": text.index("「乙。」") + 4},
    ]
    prediction = rule_baseline_predictions(
        book_id="b1", text=text, quotes=quotes, options=RuleBaselineOptions(scene_gap_cp=200)
    )
    scenes = {row["quote_id"]: row["scene_key"] for row in prediction["quotes"]}
    assert scenes["q1"] != scenes["q2"]


def test_configs_have_stable_fingerprints() -> None:
    b0 = load_config(REPO_ROOT / "evaluation" / "configs" / "b0.json")
    b2 = load_config(REPO_ROOT / "evaluation" / "configs" / "b2.json")
    assert b0.strategy == "rule_baseline"
    assert b2.strategy == "llm" and b2.budget["max_recheck_rounds"] == 0
    assert b0.fingerprint != b2.fingerprint
    assert b0.fingerprint == load_config(REPO_ROOT / "evaluation" / "configs" / "b0.json").fingerprint
