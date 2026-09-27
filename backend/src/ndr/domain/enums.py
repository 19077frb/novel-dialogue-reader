"""领域枚举（DEVELOPMENT.md 3.3）。

取值字符串就是 API 契约的一部分：所有枚举继承 ``StrEnum``，序列化即为字符串值。
大写取值用于状态机（SceneStatus、JobState…），小写取值用于协议字段
（quote kind、reading mode、visibility policy 等），与 DEVELOPMENT.md 的示例一致。
"""

from __future__ import annotations

from enum import StrEnum


class DatabaseState(StrEnum):
    READY = "READY"
    NOT_INITIALIZED = "NOT_INITIALIZED"
    OUTDATED = "OUTDATED"
    ERROR = "ERROR"


class ContentNodeType(StrEnum):
    """统一文档树的受限节点类型（DEVELOPMENT.md 4.1）。

    ruby/rb/rt/rp 单独保留，注音不重复进入模型正文。
    """

    PARAGRAPH = "paragraph"
    HEADING = "heading"
    IMAGE = "image"
    RUBY = "ruby"
    RB = "rb"
    RT = "rt"
    RP = "rp"
    FOOTNOTE = "footnote"
    SEPARATOR = "separator"
    OTHER = "other"


class BookFormat(StrEnum):
    TXT = "TXT"
    EPUB = "EPUB"


class ImportStatus(StrEnum):
    """导入状态与推理状态分离。"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class SceneStatus(StrEnum):
    OPEN = "OPEN"
    PENDING_BOUNDARY = "PENDING_BOUNDARY"
    CLOSED = "CLOSED"


class GapDecision(StrEnum):
    CONTINUE = "CONTINUE"
    UPDATE = "UPDATE"
    BREAK = "BREAK"
    UNCERTAIN = "UNCERTAIN"


class QuoteKind(StrEnum):
    SPEECH = "speech"
    THOUGHT = "thought"
    QUOTATION = "quotation"
    GROUP = "group"
    OTHER = "other"
    UNKNOWN = "unknown"


class Assignment(StrEnum):
    """普通 speech 的归属结论；其他类型 conclusion 为 None，不强行指定说话人。"""

    EXISTING = "EXISTING"
    NEW = "NEW"
    UNKNOWN = "UNKNOWN"


class SpeakerBasis(StrEnum):
    """证据类型，不是校准后的置信概率（DEVELOPMENT.md 4.4）。"""

    DIRECT = "DIRECT"
    COREFERENCE = "COREFERENCE"
    RESPONSE_LINK = "RESPONSE_LINK"
    STYLE_ONLY = "STYLE_ONLY"
    INSUFFICIENT = "INSUFFICIENT"


class AnnotationStatus(StrEnum):
    PROVISIONAL = "PROVISIONAL"
    ACCEPTED = "ACCEPTED"
    USER_CONFIRMED = "USER_CONFIRMED"
    UNKNOWN = "UNKNOWN"


class AnnotationSource(StrEnum):
    MODEL = "MODEL"
    RULE = "RULE"
    USER = "USER"


class ReviewQueueStatus(StrEnum):
    PENDING = "PENDING"
    DEFERRED = "DEFERRED"
    RESOLVED = "RESOLVED"


class ReviewTargetType(StrEnum):
    QUOTE = "quote"
    GAP = "gap"


class ReviewReason(StrEnum):
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    AMBIGUOUS_SPEAKER = "AMBIGUOUS_SPEAKER"
    UNKNOWN_SPEAKER = "UNKNOWN_SPEAKER"
    POSSIBLE_NEW_SPEAKER = "POSSIBLE_NEW_SPEAKER"
    SCENE_BOUNDARY = "SCENE_BOUNDARY"
    STALE_DEPENDENCY = "STALE_DEPENDENCY"
    USER_FLAGGED = "USER_FLAGGED"
    OTHER = "OTHER"


class IdentityOperation(StrEnum):
    MERGE = "MERGE"
    SPLIT = "SPLIT"


class CorrectionTargetType(StrEnum):
    QUOTE = "quote"
    GAP = "gap"
    SCENE = "scene"
    ANNOTATION = "annotation"


class CorrectionAction(StrEnum):
    """人工更正动作（DEVELOPMENT.md 5.3）。"""

    ASSIGN_EXISTING = "assign_existing"
    CREATE_SPEAKER = "create_speaker"
    SET_KIND = "set_kind"
    MARK_UNKNOWN = "mark_unknown"
    SET_GAP_DECISION = "set_gap_decision"
    MERGE_SPEAKERS = "merge_speakers"
    SPLIT_SPEAKERS = "split_speakers"
    UNDO = "undo"


class JobKind(StrEnum):
    IMPORT = "IMPORT"
    INFERENCE = "INFERENCE"
    RECHECK = "RECHECK"
    RECOMPUTE = "RECOMPUTE"
    EXPORT = "EXPORT"


class JobPurpose(StrEnum):
    """INFERENCE 的目的；preview/process 共用同一识别引擎与缓存。"""

    PREVIEW = "preview"
    PROCESS = "process"
    NONE = "none"


class JobState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSING = "PAUSING"
    PAUSED = "PAUSED"
    PARTIAL = "PARTIAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    NEEDS_RECONCILIATION = "NEEDS_RECONCILIATION"


class InferenceRunState(StrEnum):
    """每次尝试的状态；未知用量不能被写成 0。"""

    PREPARED = "PREPARED"
    DISPATCHED = "DISPATCHED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"


class CredentialMode(StrEnum):
    SESSION = "session"
    SYSTEM = "system"
    NONE = "none"


class ReadingMode(StrEnum):
    INITIAL = "initial"
    REREAD = "reread"


class VisibilityPolicy(StrEnum):
    POSITION_SAFE = "position_safe"
    REREAD = "reread"


class ExportFormat(StrEnum):
    EPUB = "epub"
    HTML = "html"


class ExportStylePreset(StrEnum):
    COLOR_AND_LABEL = "color_and_label"
    COLOR_ONLY = "color_only"
    LABEL_ONLY = "label_only"


class ErrorCode(StrEnum):
    """稳定业务错误码；上游错误必须映射到这些值，不透传原始响应。"""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    RESOURCE_CONFLICT = "RESOURCE_CONFLICT"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    PROVIDER_AUTH_FAILED = "PROVIDER_AUTH_FAILED"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    INVALID_MODEL_OUTPUT = "INVALID_MODEL_OUTPUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"
