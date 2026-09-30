"""Validated, per-execution generation overrides (never credentials)."""

from typing import Literal

from .common import ApiModel


class InferenceOptions(ApiModel):
    thinking_mode: Literal["default", "disabled", "enabled", "adaptive"] = "default"
    reasoning_effort: Literal["default", "low", "medium", "high"] = "default"
