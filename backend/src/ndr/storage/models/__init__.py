"""ORM 模型包；导入即把全部表注册到 ``Base.metadata``（Alembic autogenerate 依赖这一点）。

表结构与约束见 DEVELOPMENT.md 3.2；迁移只追加，不删库重建。
"""

from __future__ import annotations

from ..base import Base
from .book import Book, BookVersion, Chapter, ContentNode, Resource
from .dialogue import Gap, Participant, Quote, Scene, SceneMembership, SpeakerGroup
from .jobs import InferenceRun, Job, JobWindow, ResultCache
from .labeling import Annotation, AnnotationHistory, IdentityRevision
from .modeling import ModelProfile
from .review import Correction, ReviewItem

__all__ = [
    "Annotation",
    "AnnotationHistory",
    "Base",
    "Book",
    "BookVersion",
    "Chapter",
    "ContentNode",
    "Correction",
    "Gap",
    "IdentityRevision",
    "InferenceRun",
    "Job",
    "JobWindow",
    "ModelProfile",
    "Participant",
    "Quote",
    "Resource",
    "ResultCache",
    "ReviewItem",
    "Scene",
    "SceneMembership",
    "SpeakerGroup",
]
