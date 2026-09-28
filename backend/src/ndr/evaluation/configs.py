"""评测配置（T16）：把 B0/B1/B2 的参数固化成可复现的 JSON，并给出配置指纹。

`fingerprint` 与缓存键用的是同一套规范化哈希：报告里记录它可以证明“这份数字是这组参数跑出来的”。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..domain.enums import ReadingMode
from ..storage.cache import fingerprint

CONFIG_VERSION = "1.0"
KNOWN_STRATEGIES = ("rule_baseline", "llm")


@dataclass(frozen=True)
class EvalConfig:
    config_id: str
    label: str
    strategy: str
    scene_state: bool = True
    prompt_version: str = "labeling-2"
    context_policy: str = "context-1"
    reading_mode: ReadingMode = ReadingMode.REREAD
    budget: dict = field(default_factory=dict)
    model: str | None = None
    notes: str = ""

    def as_dict(self) -> dict:
        return {
            "config_version": CONFIG_VERSION,
            "config_id": self.config_id,
            "label": self.label,
            "strategy": self.strategy,
            "scene_state": self.scene_state,
            "prompt_version": self.prompt_version,
            "context_policy": self.context_policy,
            "reading_mode": self.reading_mode.value,
            "budget": dict(self.budget),
            "model": self.model,
            "notes": self.notes,
        }

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.as_dict())


def load_config(path: str | Path) -> EvalConfig:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("配置必须是 JSON 对象")
    strategy = str(payload.get("strategy", ""))
    if strategy not in KNOWN_STRATEGIES:
        raise ValueError(f"未知的 strategy：{strategy}（可用：{sorted(KNOWN_STRATEGIES)}）")
    return EvalConfig(
        config_id=str(payload["config_id"]),
        label=str(payload.get("label", payload["config_id"])),
        strategy=strategy,
        scene_state=bool(payload.get("scene_state", True)),
        prompt_version=str(payload.get("prompt_version", "labeling-2")),
        context_policy=str(payload.get("context_policy", "context-1")),
        reading_mode=ReadingMode(str(payload.get("reading_mode", ReadingMode.REREAD.value))),
        budget=dict(payload.get("budget", {}) or {}),
        model=payload.get("model"),
        notes=str(payload.get("notes", "")),
    )
