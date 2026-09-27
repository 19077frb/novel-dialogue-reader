"""T16 指标单元测试：用手算的小型聚类样例检验各项指标。

覆盖 DEVELOPMENT 7.3 要求的最小回归：匿名标签置换、错误分场（切断/连接）、
全拒答、全合并、漏提取，以及「拒答不计已接受准确率但计入覆盖率分母」。
"""

from __future__ import annotations

import copy

from ndr.evaluation.metrics import compute_metrics, targets_met

# 金标准：1 个场景、2 个分组 + 1 条不可确定；共 5 条对白（4 可确定）
GOLD = {
    "quotes": [
        {"quote_id": "q1", "start_cp": 0, "end_cp": 10, "scene_id": "s1", "group_id": "S1"},
        {"quote_id": "q2", "start_cp": 20, "end_cp": 30, "scene_id": "s1", "group_id": "S1"},
        {"quote_id": "q3", "start_cp": 40, "end_cp": 50, "scene_id": "s1", "group_id": "S2"},
        {
            "quote_id": "q4",
            "start_cp": 60,
            "end_cp": 70,
            "scene_id": "s1",
            "group_id": None,
            "resolvable": False,
        },
        {"quote_id": "q5", "start_cp": 80, "end_cp": 90, "scene_id": "s1", "group_id": "S2"},
    ]
}

PERFECT = {
    "quotes": [
        {"quote_id": "p1", "start_cp": 0, "end_cp": 10, "scene_key": "s1", "group_key": "S1"},
        {"quote_id": "p2", "start_cp": 20, "end_cp": 30, "scene_key": "s1", "group_key": "S1"},
        {"quote_id": "p3", "start_cp": 40, "end_cp": 50, "scene_key": "s1", "group_key": "S2"},
        {"quote_id": "p4", "start_cp": 60, "end_cp": 70, "scene_key": "s1", "group_key": None, "status": "UNKNOWN"},
        {"quote_id": "p5", "start_cp": 80, "end_cp": 90, "scene_key": "s1", "group_key": "S2"},
    ]
}


def test_perfect_prediction_scores_one() -> None:
    metrics = compute_metrics(GOLD, PERFECT, min_sample=2)
    assert metrics["extraction"]["precision"] == 1.0
    assert metrics["extraction"]["recall"] == 1.0
    assert metrics["extraction"]["f1"] == 1.0
    assert metrics["scenes"]["accuracy"] == 1.0
    assert metrics["grouping"]["accepted_accuracy"] == 1.0
    assert metrics["grouping"]["accepted_total"] == 4
    assert metrics["grouping"]["pairwise_f1"] == 1.0
    assert metrics["coverage"]["coverage"] == 0.8  # 4/5：拒答算入分母
    assert metrics["coverage"]["unknown_force_rate"] == 0.0
    assert metrics["degenerate"]["all_refusal"] is False
    assert metrics["degenerate"]["all_merge"] is False
    assert targets_met(metrics) is True
    ci = metrics["accepted_accuracy_ci"]
    assert ci and ci["n"] == 4 and ci["low"] < 1.0 and ci["high"] == 1.0


def test_anonymous_label_permutation_does_not_change_metrics() -> None:
    renamed = copy.deepcopy(PERFECT)
    mapping = {"S1": "A", "S2": "B"}
    for row in renamed["quotes"]:
        if row["group_key"]:
            row["group_key"] = mapping[row["group_key"]]
    base = compute_metrics(GOLD, PERFECT, min_sample=2)
    permuted = compute_metrics(GOLD, renamed, min_sample=2)
    for key in ("extraction", "scenes", "coverage", "degenerate", "sample"):
        assert base[key] == permuted[key], key
    for key in ("accepted_accuracy", "pairwise_f1", "extra_groups", "missing_groups"):
        assert base["grouping"][key] == permuted["grouping"][key], key


def test_wrong_scene_split_is_reported() -> None:
    split = copy.deepcopy(PERFECT)
    split["quotes"][2]["scene_key"] = "s2"  # q3 被错误地切到另一个场景
    metrics = compute_metrics(GOLD, split, min_sample=2)
    assert metrics["scenes"]["wrong_split"] == 2  # (q2,q3) 与 (q3,q4)
    assert metrics["scenes"]["wrong_join"] == 0
    assert metrics["scenes"]["accuracy"] == 0.5  # 4 对相邻里错 2 对
    assert {item["kind"] for item in metrics["scenes"]["errors"]} == {"wrong_split"}


def test_wrong_scene_join_is_reported() -> None:
    two_scenes = copy.deepcopy(GOLD)
    two_scenes["quotes"][3]["scene_id"] = "s2"
    two_scenes["quotes"][4]["scene_id"] = "s2"
    joined = copy.deepcopy(PERFECT)
    for row in joined["quotes"]:
        row["scene_key"] = "s1"  # 预测把两个场景合成一个
    metrics = compute_metrics(two_scenes, joined, min_sample=2)
    assert metrics["scenes"]["wrong_join"] == 1  # 只有 (q4,q5) 跨场景
    assert metrics["scenes"]["wrong_split"] == 0


def test_all_refusal_scores_zero_coverage_without_accuracy() -> None:
    refusal = {
        "quotes": [
            {
                "quote_id": row["quote_id"],
                "start_cp": row["start_cp"],
                "end_cp": row["end_cp"],
                "scene_key": row["scene_id"],
                "group_key": None,
                "status": "UNKNOWN",
            }
            for row in GOLD["quotes"]
        ]
    }
    metrics = compute_metrics(GOLD, refusal, min_sample=2)
    assert metrics["coverage"]["coverage"] == 0.0
    assert metrics["coverage"]["refusal_rate"] == 1.0
    assert metrics["grouping"]["accepted_accuracy"] is None  # 没有已接受样本，不做除法
    assert metrics["degenerate"]["all_refusal"] is True
    assert metrics["extraction"]["recall"] == 1.0  # 拒答仍然“提取到了对白”
    assert targets_met(metrics) is None  # 不宣布达标


def test_all_merge_is_penalized_and_flagged() -> None:
    merged = {
        "quotes": [
            {
                "quote_id": row["quote_id"],
                "start_cp": row["start_cp"],
                "end_cp": row["end_cp"],
                "scene_key": row["scene_id"],
                "group_key": "G",
            }
            for row in GOLD["quotes"]
        ]
    }
    metrics = compute_metrics(GOLD, merged, min_sample=2)
    assert metrics["degenerate"]["all_merge"] is True
    assert metrics["degenerate"]["single_group"] is True
    assert metrics["grouping"]["missing_groups"] == 1
    assert metrics["grouping"]["pairwise_precision"] == 2 / 6
    assert metrics["grouping"]["pairwise_recall"] == 1.0
    assert metrics["grouping"]["pairwise_f1"] == 0.5
    assert metrics["grouping"]["accepted_accuracy"] == 0.5
    assert targets_met(metrics) is False


def test_missed_extraction_lowers_recall_and_is_reported() -> None:
    partial = copy.deepcopy(PERFECT)
    partial["quotes"] = [row for row in partial["quotes"] if row["quote_id"] != "p5"]
    metrics = compute_metrics(GOLD, partial, min_sample=2)
    assert metrics["counts"]["missed"] == 1
    assert metrics["extraction"]["precision"] == 1.0
    assert metrics["extraction"]["recall"] == 0.8
    assert metrics["coverage"]["coverage"] == 0.6  # q5 没有预测，覆盖率下降
    assert metrics["grouping"]["accepted_accuracy"] == 1.0


def test_forcing_labels_on_unresolvable_quotes_is_measured() -> None:
    forced = copy.deepcopy(PERFECT)
    forced["quotes"][3]["group_key"] = "S1"  # 金标准说不可确定，预测却给了分组
    metrics = compute_metrics(GOLD, forced, min_sample=2)
    assert metrics["coverage"]["unknown_force_rate"] == 1.0
    assert metrics["coverage"]["gold_unresolvable"] == 1
    assert metrics["grouping"]["accepted_accuracy"] == 1.0  # 不可确定的样本不计入已接受准确率
    assert metrics["degenerate"]["forced_all_unresolvable"] is True


def test_small_samples_do_not_claim_targets() -> None:
    metrics = compute_metrics(GOLD, PERFECT)  # 默认 min_sample=30，本例只有 4 条
    assert metrics["sample"]["sample_sufficient"] is False
    assert targets_met(metrics) is None
