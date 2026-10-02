"""评测运行：离线校验与评分；真实调用必须显式允许。

- `validate`：校验清单 + 金标准（结构、引用、作品划分），不加载模型。
- `run`：按配置产出预测（B0 规则基线 / 外部预测文件 / 显式允许的真实运行），
  再计算按作品、按难例类别与总体的指标，写出带版本、用量与配置指纹的报告。

**真实性**：报告里 `quality_evidence` 只有在真实模型运行且样本足够时才为 true；
离线基线（B0/外部假提供方）一律为 false，并把原因写进 `blocks`。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import __version__
from ..config import Settings, get_settings
from ..context.budget import CONTEXT_POLICY_COMPRESSED, CONTEXT_POLICY_CONSERVATIVE
from ..context.window_builder import CONTEXT_POLICY_VERSION
from ..llm.prompts.labeling import LABELING_PROMPT_VERSION
from ..llm.prompts.roster import ROSTER_PROMPT_VERSION
from ..quotes.scanner import SCANNER_VERSION
from ..scenes.engine import ENGINE_VERSION
from .baselines import RULE_BASELINE_VERSION, RuleBaselineOptions, rule_baseline_predictions
from .configs import EvalConfig, load_config
from .gold_standard import GoldIssue, load_gold_standard, read_canonical_text
from .live import run_live_predictions
from .manifest import load_manifest, validate_manifest
from .metrics import compute_metrics, targets_met

EVALUATION_VERSION = "evaluation-2"

RUN_STATES = ("COMPLETED", "OFFLINE_BASELINE", "NOT_RUN", "LIVE_FAILED")


@dataclass
class BookResult:
    book_id: str
    work_id: str
    state: str
    reason: str = ""
    metrics: dict[str, Any] | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    provider: str | None = None
    hard_case_stats: dict[str, dict[str, int]] = field(default_factory=dict)
    model_config: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunOptions:
    manifest_path: Path
    config_path: Path | None = None
    output_path: Path | None = None
    predictions_path: Path | None = None
    splits: list[str] | None = None
    allow_live: bool = False
    profile_id: str | None = None
    min_sample: int = 30
    settings: Settings | None = None


def versions() -> dict[str, Any]:
    """报告里记录的所有版本线索（复现实验必需）。"""

    return {
        "evaluation": EVALUATION_VERSION,
        "app": __version__,
        "engine": ENGINE_VERSION,
        "prompt": LABELING_PROMPT_VERSION,
        "roster_prompt": ROSTER_PROMPT_VERSION,
        "scanner": SCANNER_VERSION,
        # 默认（保守）策略版本 + 本次评测可选的两版策略（B3/B4 消融用）
        "context_policy": CONTEXT_POLICY_VERSION,
        "context_policy_versions": [CONTEXT_POLICY_CONSERVATIVE, CONTEXT_POLICY_COMPRESSED],
        "gold_schema": "1.0",
        "rule_baseline": RULE_BASELINE_VERSION,
    }


def load_predictions_file(path: str | Path) -> dict[str, dict[str, Any]]:
    """读取外部预测文件：`{book_id: prediction}`，单本书时也允许直接给 prediction。"""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("预测文件必须是 JSON 对象")
    if "quotes" in payload:
        book_id = str(payload.get("book_id") or "")
        return {book_id: payload}
    return {str(key): value for key, value in payload.items()}


def merge_gold(manifest_books: list[Any], golds: dict[str, dict], *, prefix: bool = True) -> dict:
    """把多本书的金标准合并成一份（键加书前缀，避免跨书撞键）。"""

    quotes: list[dict] = []
    for book in manifest_books:
        gold = golds[book.book_id]
        for row in gold.get("quotes", []) or []:
            quotes.append(
                {
                    **row,
                    "quote_id": f"{book.book_id}:{row['quote_id']}" if prefix else row["quote_id"],
                    "scene_id": (
                        f"{book.book_id}:{row['scene_id']}"
                        if prefix and row.get("scene_id")
                        else None
                    ),
                    "hard_cases": list(book.hard_cases),
                }
            )
    return {"quotes": quotes}


def merge_predictions(
    manifest_books: list[Any], predictions: dict[str, dict], *, prefix: bool = True
) -> dict:
    quotes: list[dict] = []
    for book in manifest_books:
        prediction = predictions.get(book.book_id) or {}
        for row in prediction.get("quotes", []) or []:
            quotes.append(
                {
                    **row,
                    "quote_id": f"{book.book_id}:{row['quote_id']}" if prefix else row["quote_id"],
                    "scene_key": (
                        f"{book.book_id}:{row['scene_key']}"
                        if prefix and row.get("scene_key")
                        else None
                    ),
                }
            )
    return {"quotes": quotes}


def _hard_case_stats(gold: dict, metrics: dict) -> dict[str, dict[str, int]]:
    """按难例类别统计金标准数量（把每类样本量如实写进报告）。"""

    stats: dict[str, dict[str, int]] = {}
    for row in gold.get("quotes", []) or []:
        for case in row.get("hard_cases", []) or []:
            entry = stats.setdefault(case, {"gold": 0, "resolvable": 0})
            entry["gold"] += 1
            if row.get("resolvable", True):
                entry["resolvable"] += 1
    _ = metrics
    return stats


def _offline_predictions(
    *,
    config: EvalConfig,
    book,  # noqa: ANN001 - ManifestBook
    text: str,
    quotes: list[dict],
) -> tuple[dict | None, str, str]:
    if config.strategy == "rule_baseline":
        prediction = rule_baseline_predictions(
            book_id=book.book_id, text=text, quotes=quotes, options=RuleBaselineOptions()
        )
        return prediction, "OFFLINE_BASELINE", "B0 规则基线（无 LLM）：结果不能作为效果数字"
    return None, "NOT_RUN", "该配置需要真实模型调用（加 --allow-live 并提供 --profile-id）"


def build_report(options: RunOptions) -> dict[str, Any]:
    """执行一次评测并返回报告（不写文件）。"""

    manifest = load_manifest(options.manifest_path)
    issues = validate_manifest(manifest)
    errors = [item for item in issues if item.is_error]
    if errors:
        raise ValueError(
            "清单/金标准有问题：" + "；".join(f"{item.code}: {item.message}" for item in errors[:5])
        )

    config = load_config(options.config_path) if options.config_path else None
    books = manifest.books(options.splits)
    external = load_predictions_file(options.predictions_path) if options.predictions_path else {}

    results: list[BookResult] = []
    golds: dict[str, dict] = {}
    predictions: dict[str, dict] = {}
    blocks: list[str] = []

    for book in books:
        gold = load_gold_standard(book.gold_path)
        golds[book.book_id] = gold
        text, _encoding = read_canonical_text(book.text_path)
        quotes = [
            {"quote_id": f"c{index}", "start_cp": row["start_cp"], "end_cp": row["end_cp"]}
            for index, row in enumerate(
                sorted(gold.get("quotes", []), key=lambda item: item["start_cp"]), start=1
            )
        ]
        prediction: dict | None = None
        state = "NOT_RUN"
        reason = ""
        if config is None and external:
            prediction = external.get(book.book_id)
            state = "COMPLETED" if prediction else "NOT_RUN"
            reason = "" if prediction else "预测文件里没有这本书"
        elif config is not None and config.strategy == "rule_baseline":
            prediction, state, reason = _offline_predictions(
                config=config, book=book, text=text, quotes=quotes
            )
        elif config is not None:
            prediction, state, reason = run_live_predictions(
                settings=options.settings or get_settings(),
                book_path=book.text_path,
                profile_id=options.profile_id or "",
                config_id=config.config_id,
                reading_mode=config.reading_mode,
                context_policy=config.context_policy,
                budget=config.budget,
                inference_options=config.inference_options,
                allow_live=options.allow_live,
            )
        else:
            reason = "既没有 --config 也没有 --predictions"

        if prediction is None or state == "LIVE_FAILED":
            results.append(
                BookResult(
                    book_id=book.book_id,
                    work_id=book.work_id,
                    state=state,
                    reason=reason,
                    usage=dict((prediction or {}).get("usage") or {}),
                    provider=(prediction or {}).get("provider"),
                    model_config=dict((prediction or {}).get("model_config") or {}),
                )
            )
            blocks.append(f"{book.book_id}: {state}（{reason}）")
            continue

        if not prediction.get("quality_evidence", False):
            blocks.append(
                f"{book.book_id}: 预测来自 {prediction.get('provider', 'unknown')}，"
                "不是真实模型效果（quality_evidence=false）"
            )
        predictions[book.book_id] = prediction
        book_metrics = compute_metrics(gold, prediction, min_sample=options.min_sample)
        results.append(
            BookResult(
                book_id=book.book_id,
                work_id=book.work_id,
                state=state,
                reason=reason,
                metrics=book_metrics,
                usage=dict(prediction.get("usage", {}) or {}),
                provider=prediction.get("provider"),
                model_config=dict(prediction.get("model_config") or {}),
                hard_case_stats=_hard_case_stats(gold, book_metrics),
            )
        )

    scored_books = [book for book in books if book.book_id in predictions]
    overall: dict[str, Any] | None = None
    if scored_books:
        overall = compute_metrics(
            merge_gold(scored_books, golds),
            merge_predictions(scored_books, predictions),
            min_sample=options.min_sample,
        )

    usage_total = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    unknown_usage = 0
    for result in results:
        usage = result.usage or {}
        if usage.get("provider") in {None, "none"} and not usage:
            continue
        if usage.get("unknown_runs") or any(
            usage.get(key) is None for key in ("input_tokens", "output_tokens")
        ):
            unknown_usage += 1
        usage_total["calls"] += int(usage.get("calls") or 0)
        usage_total["input_tokens"] += int(usage.get("input_tokens") or 0)
        usage_total["output_tokens"] += int(usage.get("output_tokens") or 0)
        usage_total["total_tokens"] += int(usage.get("total_tokens") or 0)

    quality_evidence = bool(
        overall
        and overall["sample"]["sample_sufficient"]
        and all(result.state == "COMPLETED" for result in results)
        # 测试用提供方（fake-provider）永远不算效果证据
        and all((result.provider or "") not in {"", "fake-provider"} for result in results)
        and any((result.usage or {}).get("provider") not in {None, "none"} for result in results)
    )
    report = {
        "report_version": EVALUATION_VERSION,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "manifest": str(options.manifest_path),
        "manifest_version": manifest.version,
        "config": config.as_dict() if config else None,
        "config_fingerprint": config.fingerprint if config else None,
        "versions": versions(),
        "splits": options.splits or sorted({work.split for work in manifest.works}),
        "quality_evidence": quality_evidence,
        "blocks": blocks,
        "books": [
            {
                "book_id": result.book_id,
                "work_id": result.work_id,
                "state": result.state,
                "reason": result.reason,
                "provider": result.provider,
                "model_config": result.model_config,
                "usage": result.usage,
                "hard_cases": result.hard_case_stats,
                "metrics": result.metrics,
            }
            for result in results
        ],
        "overall": overall,
        "usage_total": usage_total,
        "usage_unknown_books": unknown_usage,
        "targets": overall["targets"] if overall else None,
        "targets_met": targets_met(overall) if overall else None,
        "notes": (
            "指标由 ndr.evaluation.metrics 计算，不是前端模拟数据；"
            "样本不足或未达目标时报告如实给出 null/false。"
        ),
    }
    return report


def write_report(report: dict[str, Any], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def validate_command(manifest_path: str | Path) -> tuple[int, dict[str, Any]]:
    """`validate` 子命令：只校验数据，不加载模型。"""

    manifest = load_manifest(manifest_path)
    issues: list[GoldIssue] = validate_manifest(manifest)
    errors = [item for item in issues if item.is_error]
    payload = {
        "manifest": str(manifest_path),
        "manifest_version": manifest.version,
        "works": len(manifest.works),
        "books": len(manifest.books()),
        "splits": sorted({work.split for work in manifest.works}),
        "errors": [item.as_dict() for item in errors],
        "warnings": [item.as_dict() for item in issues if not item.is_error],
    }
    return (1 if errors else 0), payload
