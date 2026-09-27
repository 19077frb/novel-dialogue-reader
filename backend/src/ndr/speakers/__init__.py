"""场景内匿名分组与身份修订（T09）。"""

from __future__ import annotations

from .groups import SpeakerRegistry
from .revisions import (
    IdentityDecision,
    IdentityProposalView,
    evaluate_identity_proposal,
)

__all__ = [
    "IdentityDecision",
    "IdentityProposalView",
    "SpeakerRegistry",
    "evaluate_identity_proposal",
]
