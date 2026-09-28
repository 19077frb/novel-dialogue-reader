"""模型配置。

本表**没有任何存放密钥的列**：只保存 ``credential_mode`` 与 ``credential_ref``，
明文 Key 由凭据服务交给系统凭据库或仅存活于会话内存。
"""

from __future__ import annotations

from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...domain.enums import CredentialMode
from ..base import Base, IdMixin, TimestampMixin, VersionMixin, enum_type


class ModelProfile(IdMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "model_profiles"
    __table_args__ = (UniqueConstraint("name", name="uq_model_profiles_name"),)

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    protocol: Mapped[str] = mapped_column(String(64), nullable=False)
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    model: Mapped[str] = mapped_column(String(256), nullable=False)
    params_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    credential_mode: Mapped[CredentialMode] = mapped_column(
        enum_type(CredentialMode, name="credential_mode"),
        nullable=False,
        default=CredentialMode.NONE,
    )
    # 只保存引用（例如 keyring 条目名或会话 ID），不保存密钥本身。
    credential_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
