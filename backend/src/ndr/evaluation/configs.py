"""当前章节评测配置与配置指纹。

`fingerprint` 与缓存键用的是同一套规范化哈希：报告里记录它可以证明“这份数字是这组参数跑出来的”。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..domain.enums import ReadingMode
from ..domain.inference_options import InferenceOptions
from ..domain.jobs import BudgetIn
from ..storage.cache import fingerprint

CONFIG_VERSION = "2.0"
KNOWN_STRATEGIES = ("rule_baseline", "llm")


@dataclass(frozen=True)
class EvalConfig:
    config_id: str
    label: str
    strategy: str
    context_policy: str = "context-1"
    reading_mode: ReadingMode = ReadingMode.REREAD
    budget: dict = field(default_factory=dict)
    inference_options: dict = field(default_factory=dict)
    notes: str = ""

    def as_dict(self) -> dict:
        return {
            "config_version": CONFIG_VERSION,
            "config_id": self.config_id,
            "label": self.label,
            "strategy": self.strategy,
            "context_policy": self.context_policy,
            "reading_mode": self.reading_mode.value,
            "budget": dict(self.budget),
            "inference_options": dict(self.inference_options),
            "notes": self.notes,
        }

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.as_dict())


def load_config(path: str | Path) -> EvalConfig:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("配置必须是 JSON 对象")
    if not isinstance(payload.get("budget", {}), dict):
        raise ValueError("budget 必须是 JSON 对象")
    obsolete = {"scene_state", "prompt_version", "model"} & payload.keys()
    if obsolete or "max_rechecks" in (payload.get("budget") or {}):
        raise ValueError(
            "过时评测配置：删除 scene_state/prompt_version/model；"
            "用 budget.max_recheck_rounds 设置整窗口复核轮次，不能沿用条数"
        )
    allowed = {
        "config_version",
        "config_id",
        "label",
        "strategy",
        "context_policy",
        "reading_mode",
        "budget",
        "inference_options",
        "notes",
    }
    if payload.keys() - allowed:
        raise ValueError(f"未知配置字段：{sorted(payload.keys() - allowed)}")
    if payload.get("config_version") != CONFIG_VERSION:
        raise ValueError(f"评测配置必须使用 config_version={CONFIG_VERSION}")
    if payload.get("context_policy", "context-1") not in {"context-1", "context-2"}:
        raise ValueError("context_policy 必须为 context-1 或 context-2")
    raw_budget = payload.get("budget") or {}
    if not isinstance(raw_budget, dict):
        raise ValueError("budget 必须是 JSON 对象")
    if raw_budget.keys() - {
        "max_input_tokens",
        "max_output_tokens",
        "max_recheck_rounds",
        "max_format_retries",
    }:
        raise ValueError("未知 budget 字段")
    budget = BudgetIn.model_validate({"max_recheck_rounds": 0, **raw_budget}).model_dump(
        exclude={"max_rechecks"}
    )
    if budget["max_recheck_rounds"] is None:
        raise ValueError("max_recheck_rounds 必须是非负轮次，关闭复核请填 0")
    options = InferenceOptions.model_validate(payload.get("inference_options") or {}).model_dump()
    strategy = str(payload.get("strategy", ""))
    if strategy not in KNOWN_STRATEGIES:
        raise ValueError(f"未知的 strategy：{strategy}（可用：{sorted(KNOWN_STRATEGIES)}）")
    return EvalConfig(
        config_id=str(payload["config_id"]),
        label=str(payload.get("label", payload["config_id"])),
        strategy=strategy,
        context_policy=str(payload.get("context_policy", "context-1")),
        reading_mode=ReadingMode(str(payload.get("reading_mode", ReadingMode.REREAD.value))),
        budget=budget,
        inference_options=options,
        notes=str(payload.get("notes", "")),
    )
