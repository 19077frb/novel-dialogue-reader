"""ORM 模型包；导入即把全部表注册到 ``Base.metadata``（Alembic autogenerate 依赖这一点）。

表结构与约束见 DEVELOPMENT.md；迁移只追加，不删库重建。
"""

from __future__ import annotations

from ..base import Base
from .book import Book, BookVersion, Chapter, ContentNode, Resource
from .bookmark import Bookmark
from .characters import BookCharacter, ChapterCharacterRoster
from .dialogue import Gap, Participant, Quote, Scene, SceneMembership, SpeakerGroup
from .exports import ExportArtifact, ExportSnapshot
from .jobs import InferenceRun, Job, JobWindow, ResultCache
from .labeling import Annotation, AnnotationHistory, IdentityRevision
from .mapping import TextMapping
from .modeling import ModelProfile
from .quote_normalization import QuoteNormalization
from .review import Correction, ReviewItem

__all__ = [
    "Annotation",
    "AnnotationHistory",
    "Base",
    "Book",
    "Bookmark",
    "BookCharacter",
    "BookVersion",
    "Chapter",
    "ChapterCharacterRoster",
    "ContentNode",
    "Correction",
    "ExportArtifact",
    "ExportSnapshot",
    "Gap",
    "IdentityRevision",
    "InferenceRun",
    "Job",
    "JobWindow",
    "ModelProfile",
    "Participant",
    "Quote",
    "QuoteNormalization",
    "Resource",
    "ResultCache",
    "ReviewItem",
    "Scene",
    "SceneMembership",
    "SpeakerGroup",
    "TextMapping",
]
