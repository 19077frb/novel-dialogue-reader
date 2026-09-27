"""声明式基类与公共列 mixin。

ID 是不透明字符串（UUID4）；所有可变实体带递增 version，写请求必须携带期望版本。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import Enum as SAEnum
from sqlalchemy import MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .types import UtcDateTime

# 统一命名约定：迁移与 SQLite 都需要稳定、可推断的约束名。
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def new_id() -> str:
    """不透明主键；数据库内部使用 UUID4 字符串。"""

    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class IdMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class VersionMixin:
    """可变实体的乐观并发版本；初始版本为 1。"""

    version: Mapped[int] = mapped_column(default=1, nullable=False)

def enum_type(enum_cls: type[Enum], *, name: str) -> SAEnum:
    """以 VARCHAR + CHECK 保存枚举字符串值（SQLite 没有原生枚举）。

    ``values_callable`` 让数据库里存的是 ``.value``（如 ``TXT``/``speech``），
    而不是 Python 成员名，避免迁移与 API 契约出现两套写法。
    """

    return SAEnum(
        enum_cls,
        native_enum=False,
        values_callable=lambda cls: [member.value for member in cls],
        name=name,
        validate_strings=True,
        length=max(len(member.value) for member in enum_cls),
    )
