"""领域枚举与 API schema 包。

后端是枚举与 schema 的权威定义（DEVELOPMENT.md 3.1）；前端类型由 OpenAPI 生成，
不维护第二份会漂移的枚举。
"""

from __future__ import annotations

from .common import (
    ApiModel,
    CursorPage,
    DataEnvelope,
    ErrorBody,
    ErrorEnvelope,
)
from .enums import (
    AnnotationSource,
    AnnotationStatus,
    Assignment,
    BookFormat,
    ContentNodeType,
    CorrectionAction,
    CorrectionTargetType,
    CredentialMode,
    DatabaseState,
    ErrorCode,
    ExportFormat,
    ExportStylePreset,
    GapDecision,
    IdentityOperation,
    ImportStatus,
    InferenceRunState,
    JobKind,
    JobPurpose,
    JobState,
    QuoteKind,
    ReadingMode,
    ReviewQueueStatus,
    ReviewReason,
    ReviewTargetType,
    SceneStatus,
    SpeakerBasis,
    VisibilityPolicy,
)

__all__ = [
    "AnnotationSource",
    "AnnotationStatus",
    "ApiModel",
    "Assignment",
    "BookFormat",
    "ContentNodeType",
    "CorrectionAction",
    "CorrectionTargetType",
    "CredentialMode",
    "CursorPage",
    "DataEnvelope",
    "DatabaseState",
    "ErrorBody",
    "ErrorCode",
    "ErrorEnvelope",
    "ExportFormat",
    "ExportStylePreset",
    "GapDecision",
    "IdentityOperation",
    "ImportStatus",
    "InferenceRunState",
    "JobKind",
    "JobPurpose",
    "JobState",
    "QuoteKind",
    "ReadingMode",
    "ReviewQueueStatus",
    "ReviewReason",
    "ReviewTargetType",
    "SceneStatus",
    "SpeakerBasis",
    "VisibilityPolicy",
]
