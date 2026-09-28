"""评测指标。

在**同一套坐标**上比较「预测的分组」与「金标准」：

- 指标在**匿名标签置换**下不变：只看“是否同组”，不看标签字符串（S1/S2 换成 A/B 结果一样）。
- 预测的分组键带**场景命名空间**（`(scene_key, group_key)`），避免不同场景里重复的 S1 被当成一致。
- **拒答**（UNKNOWN / 无分组）不计入“已接受准确率”，但计入覆盖率分母。
- 退化预测（全拒答 / 全合并）会被显式标记：任何单一指标都不能用来宣布达标。

所有函数都是纯函数，便于用手算的小样例验证。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

MATCH_IOU = 0.5
MIN_SAMPLE_FOR_TARGETS = 30  # 少于这么多“可确定”金标准对白时，不能宣布达标（DEV 7.3）
TARGET_ACCEPTED_ACCURACY = 0.97
TARGET_COVERAGE = 0.70


@dataclass(frozen=True)
class EvalQuote:
    """评测用的一条对白（金标准或预测）。"""

    key: str
    start_cp: int
    end_cp: int
    scene_key: str | None
    group_key: str | None
    accepted: bool = True
    resolvable: bool = True
    hard_cases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def span(self) -> int:
        return max(0, self.end_cp - self.start_cp)


def iou(left: EvalQuote, right: EvalQuote) -> float:
    overlap = min(left.end_cp, right.end_cp) - max(left.start_cp, right.start_cp)
    if overlap <= 0:
        return 0.0
    union = max(left.end_cp, right.end_cp) - min(left.start_cp, right.start_cp)
    return overlap / union if union > 0 else 0.0


def match_quotes(
    gold: list[EvalQuote], predicted: list[EvalQuote], *, threshold: float = MATCH_IOU
) -> list[tuple[EvalQuote, EvalQuote]]:
    """按重叠度一对一匹配（贪心、确定性；先高 IoU，再按位置）。"""

    candidates: list[tuple[float, int, int]] = []
    for gold_index, gold_quote in enumerate(gold):
        for pred_index, pred_quote in enumerate(predicted):
            score = iou(gold_quote, pred_quote)
            if score >= threshold:
                candidates.append((score, gold_index, pred_index))
    candidates.sort(
        key=lambda item: (-item[0], gold[item[1]].start_cp, predicted[item[2]].start_cp)
    )
    used_gold: set[int] = set()
    used_pred: set[int] = set()
    pairs: list[tuple[EvalQuote, EvalQuote]] = []
    for _score, gold_index, pred_index in candidates:
        if gold_index in used_gold or pred_index in used_pred:
            continue
        used_gold.add(gold_index)
        used_pred.add(pred_index)
        pairs.append((gold[gold_index], predicted[pred_index]))
    return pairs


def gold_quotes(gold: dict[str, Any]) -> list[EvalQuote]:
    quotes: list[EvalQuote] = []
    for row in gold.get("quotes", []) or []:
        quotes.append(
            EvalQuote(
                key=str(row.get("quote_id")),
                start_cp=int(row.get("start_cp", 0)),
                end_cp=int(row.get("end_cp", 0)),
                scene_key=str(row.get("scene_id")) if row.get("scene_id") else None,
                group_key=str(row["group_id"]) if row.get("group_id") else None,
                accepted=True,
                resolvable=bool(row.get("resolvable", True)),
                hard_cases=tuple(row.get("hard_cases", ()) or ()),
            )
        )
    return sorted(quotes, key=lambda item: (item.start_cp, item.end_cp, item.key))


def prediction_quotes(prediction: dict[str, Any]) -> list[EvalQuote]:
    quotes: list[EvalQuote] = []
    for row in prediction.get("quotes", []) or []:
        group = row.get("group_key")
        status = str(row.get("status") or "").upper()
        accepted = bool(
            row.get("accepted", group is not None and status in {"", "ACCEPTED", "USER_CONFIRMED"})
        )
        quotes.append(
            EvalQuote(
                key=str(row.get("quote_id")),
                start_cp=int(row.get("start_cp", 0)),
                end_cp=int(row.get("end_cp", 0)),
                scene_key=str(row.get("scene_key")) if row.get("scene_key") else None,
                group_key=str(group) if group else None,
                accepted=accepted,
                resolvable=True,
            )
        )
    return sorted(quotes, key=lambda item: (item.start_cp, item.end_cp, item.key))

def _safe_div(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _wilson(successes: int, total: int, *, z: float = 1.96) -> dict[str, float] | None:
    """已接受准确率的 Wilson 95% 置信区间（样本少时给出不确定性，而不是“达标”）。"""

    if total <= 0:
        return None
    phat = successes / total
    denominator = 1 + z * z / total
    centre = (phat + z * z / (2 * total)) / denominator
    margin = (z * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total))) / denominator
    return {"low": max(0.0, centre - margin), "high": min(1.0, centre + margin), "n": total}


def _greedy_group_mapping(
    votes: dict[tuple[str, str], int]
) -> dict[tuple[str, str], str]:
    """(场景, 金标准分组) → 预测分组 的最佳映射（贪心最大票数，一对一）。"""

    mapping: dict[tuple[str, str], str] = {}
    used_pred: set[tuple[str, str]] = set()
    ranked = sorted(
        (
            (scene, gold_group, pred_group, count)
            for (scene, gold_group, pred_group), count in votes.items()
        ),
        key=lambda item: (-item[3], item[0], item[1], item[2]),
    )
    for scene, gold_group, pred_group, _count in ranked:
        if (scene, gold_group) in mapping or (scene, pred_group) in used_pred:
            continue
        mapping[(scene, gold_group)] = pred_group
        used_pred.add((scene, pred_group))
    return mapping


@dataclass(frozen=True)
class SceneError:
    left: str
    right: str
    kind: str  # wrong_split / wrong_join


def compute_metrics(
    gold: dict[str, Any],
    prediction: dict[str, Any],
    *,
    threshold: float = MATCH_IOU,
    min_sample: int = MIN_SAMPLE_FOR_TARGETS,
) -> dict[str, Any]:
    """计算一份预测的全部指标（纯函数）。"""

    gold_list = gold_quotes(gold)
    pred_list = prediction_quotes(prediction)
    pairs = match_quotes(gold_list, pred_list, threshold=threshold)
    matched_gold = {gold_quote.key: pred_quote for gold_quote, pred_quote in pairs}

    # ---- 提取 ----
    matched = len(pairs)
    extraction = {
        "precision": _safe_div(matched, len(pred_list)),
        "recall": _safe_div(matched, len(gold_list)),
        "f1": None,
    }
    if extraction["precision"] and extraction["recall"]:
        extraction["f1"] = (
            2 * extraction["precision"] * extraction["recall"]
            / (extraction["precision"] + extraction["recall"])
        )

    # ---- 场景边界（相邻金标准对白之间）----
    ordered = gold_list
    scene_errors: list[SceneError] = []
    compared_pairs = 0
    for left, right in zip(ordered, ordered[1:], strict=False):
        left_pred = matched_gold.get(left.key)
        right_pred = matched_gold.get(right.key)
        if left_pred is None or right_pred is None:
            continue
        compared_pairs += 1
        gold_same = left.scene_key == right.scene_key
        pred_same = left_pred.scene_key == right_pred.scene_key
        if gold_same and not pred_same:
            scene_errors.append(SceneError(left.key, right.key, "wrong_split"))
        elif not gold_same and pred_same:
            scene_errors.append(SceneError(left.key, right.key, "wrong_join"))
    scenes = {
        "compared_pairs": compared_pairs,
        "wrong_split": sum(1 for item in scene_errors if item.kind == "wrong_split"),
        "wrong_join": sum(1 for item in scene_errors if item.kind == "wrong_join"),
        "accuracy": _safe_div(compared_pairs - len(scene_errors), compared_pairs),
        "errors": [
            {"left": item.left, "right": item.right, "kind": item.kind}
            for item in scene_errors
        ],
    }

    # ---- 分组（在匹配到、已接受、且金标准可确定的对白上）----
    votes: dict[tuple[str, str, str], int] = {}
    for gold_quote, pred_quote in pairs:
        if not gold_quote.resolvable or gold_quote.group_key is None or not pred_quote.accepted:
            continue
        if pred_quote.group_key is None:
            continue
        scene = gold_quote.scene_key or "-"
        key = (scene, gold_quote.group_key, pred_quote.group_key)
        votes[key] = votes.get(key, 0) + 1
    mapping = _greedy_group_mapping(votes)

    accepted_total = 0
    accepted_correct = 0
    for gold_quote, pred_quote in pairs:
        if not gold_quote.resolvable or gold_quote.group_key is None or not pred_quote.accepted:
            continue
        accepted_total += 1
        scene = gold_quote.scene_key or "-"
        if mapping.get((scene, gold_quote.group_key)) == pred_quote.group_key:
            accepted_correct += 1

    # 同人 pairwise F1（标签置换不变）
    tp = fp = fn = 0
    for scene in {gold_quote.scene_key for gold_quote, _ in pairs}:
        group = [
            (gold_quote, pred_quote)
            for gold_quote, pred_quote in pairs
            if gold_quote.scene_key == scene
            and gold_quote.resolvable
            and gold_quote.group_key is not None
            and pred_quote.accepted
        ]
        for (gold_a, pred_a), (gold_b, pred_b) in combinations(group, 2):
            gold_same = gold_a.group_key == gold_b.group_key
            pred_same = pred_a.group_key == pred_b.group_key
            if gold_same and pred_same:
                tp += 1
            elif pred_same and not gold_same:
                fp += 1
            elif gold_same and not pred_same:
                fn += 1
    pairwise_precision = _safe_div(tp, tp + fp)
    pairwise_recall = _safe_div(tp, tp + fn)
    pairwise_f1 = None
    if pairwise_precision and pairwise_recall:
        pairwise_f1 = (
            2 * pairwise_precision * pairwise_recall / (pairwise_precision + pairwise_recall)
        )

    # 新人物误建 / 漏建（按金标准场景统计）
    extra_groups = 0
    missing_groups = 0
    for scene in {item.scene_key for item in gold_list if item.scene_key}:
        gold_groups = {
            item.group_key
            for item in gold_list
            if item.scene_key == scene and item.resolvable and item.group_key
        }
        pred_groups = {
            pred_quote.group_key
            for gold_quote, pred_quote in pairs
            if gold_quote.scene_key == scene
            and gold_quote.resolvable
            and gold_quote.group_key
            and pred_quote.accepted
            and pred_quote.group_key
        }
        extra_groups += max(0, len(pred_groups) - len(gold_groups))
        missing_groups += max(0, len(gold_groups) - len(pred_groups))

    # ---- 覆盖率 / 拒答 / 未知强标 ----
    gold_unresolvable = [item for item in gold_list if not item.resolvable]
    forced = 0
    for gold_quote in gold_unresolvable:
        pred_quote = matched_gold.get(gold_quote.key)
        if pred_quote is not None and pred_quote.group_key is not None:
            forced += 1
    accepted_matched = sum(
        1 for _gold_quote, pred_quote in pairs if pred_quote.accepted
    )
    coverage = {
        "coverage": _safe_div(accepted_matched, len(gold_list)),
        "refusal_rate": _safe_div(len(gold_list) - accepted_matched, len(gold_list)),
        "unknown_force_rate": _safe_div(forced, len(gold_unresolvable)),
        "forced_unresolvable": forced,
        "gold_unresolvable": len(gold_unresolvable),
    }

    predicted_groups = {item.group_key for item in pred_list if item.group_key and item.accepted}
    gold_groups_all = {item.group_key for item in gold_list if item.group_key and item.resolvable}
    degenerate = {
        "all_refusal": bool(pred_list) and accepted_matched == 0,
        "single_group": len(predicted_groups) == 1,
        "all_merge": len(gold_groups_all) > 1 and len(predicted_groups) == 1,
        "forced_all_unresolvable": bool(gold_unresolvable) and forced == len(gold_unresolvable),
    }

    gold_resolvable = sum(1 for item in gold_list if item.resolvable)
    sample = {
        "gold_quotes": len(gold_list),
        "gold_resolvable": gold_resolvable,
        "gold_unresolvable": len(gold_unresolvable),
        "gold_works": 1,
        "predicted_quotes": len(pred_list),
        "matched": matched,
        "missed": len(gold_list) - matched,
        "extra": len(pred_list) - matched,
        "min_sample": min_sample,
        "sample_sufficient": gold_resolvable >= min_sample,
    }
    accepted_accuracy = _safe_div(accepted_correct, accepted_total)
    return {
        "counts": sample,
        "extraction": extraction,
        "scenes": scenes,
        "grouping": {
            "accepted_accuracy": accepted_accuracy,
            "accepted_correct": accepted_correct,
            "accepted_total": accepted_total,
            "group_mapping": {
                f"{scene}|{group}": pred for (scene, group), pred in sorted(mapping.items())
            },
            "pairwise_precision": pairwise_precision,
            "pairwise_recall": pairwise_recall,
            "pairwise_f1": pairwise_f1,
            "pairwise_tp": tp,
            "pairwise_fp": fp,
            "pairwise_fn": fn,
            "extra_groups": extra_groups,
            "missing_groups": missing_groups,
        },
        "coverage": coverage,
        "degenerate": degenerate,
        "sample": sample,
        "accepted_accuracy_ci": _wilson(accepted_correct, accepted_total),
        "targets": {
            "accepted_accuracy": TARGET_ACCEPTED_ACCURACY,
            "coverage": TARGET_COVERAGE,
        },
    }


def targets_met(metrics: dict[str, Any]) -> bool | None:
    """样本足够且达到目标才返回 True；样本不足或没有已接受样本返回 None（不宣布达标）。"""

    if not metrics["sample"]["sample_sufficient"]:
        return None
    accuracy = metrics["grouping"]["accepted_accuracy"]
    coverage = metrics["coverage"]["coverage"]
    if accuracy is None or coverage is None:
        return None
    return accuracy >= TARGET_ACCEPTED_ACCURACY and coverage >= TARGET_COVERAGE
