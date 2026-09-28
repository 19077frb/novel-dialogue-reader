"""场景状态、接受策略与单窗口推理引擎。"""

from __future__ import annotations

from .acceptance import (
    ACCEPTANCE_POLICY_VERSION,
    AcceptanceDecision,
    compute_visible_from_cp,
    decide_acceptance,
    is_direct_identity_evidence,
)
from .engine import ENGINE_VERSION, WindowApplication, apply_window
from .runner import LABELING_MAX_TOKENS, WindowRunResult, run_window
from .state import SCENE_STATE_VERSION, SceneState, SceneTransition, SpeakerSlot

__all__ = [
    "ACCEPTANCE_POLICY_VERSION",
    "ENGINE_VERSION",
    "LABELING_MAX_TOKENS",
    "SCENE_STATE_VERSION",
    "AcceptanceDecision",
    "SceneState",
    "SceneTransition",
    "SpeakerSlot",
    "WindowApplication",
    "WindowRunResult",
    "apply_window",
    "compute_visible_from_cp",
    "decide_acceptance",
    "is_direct_identity_evidence",
    "run_window",
]
