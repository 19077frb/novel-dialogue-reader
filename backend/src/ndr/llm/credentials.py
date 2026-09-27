"""凭据服务（DEVELOPMENT.md 2.1 / T06）。

规则：

- 密钥**绝不**写入数据库、日志、缓存键或 API 响应；`model_profiles` 只保存
  ``credential_mode`` 与 ``credential_ref``。
- ``session`` 模式：只存在进程内存里，进程退出即失效。
- ``system`` 模式：交给系统凭据库（keyring / Windows 凭据管理器）。系统凭据库不可用时
  **降级为会话密钥并给出明确警告**，绝不把明文写进文件。
- 切换模式或删除配置时，两个存储里的同名条目都会被清理，避免残留旧密钥。

测试通过 ``CredentialService(system=FakeSystemStore())`` 注入假实现，不触碰真实系统凭据库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..domain.enums import CredentialMode

SERVICE_NAME = "novel-dialogue-reader"


class CredentialStoreError(RuntimeError):
    """系统凭据库读写失败（调用方应回退到会话密钥并告知用户）。"""


@runtime_checkable
class CredentialStore(Protocol):
    available: bool

    def set(self, ref: str, secret: str) -> None: ...

    def get(self, ref: str) -> str | None: ...

    def delete(self, ref: str) -> None: ...


class SessionCredentialStore:
    """进程内会话密钥存储：不落盘、不进日志。"""

    available = True

    def __init__(self) -> None:
        self._secrets: dict[str, str] = {}

    def set(self, ref: str, secret: str) -> None:
        self._secrets[ref] = secret

    def get(self, ref: str) -> str | None:
        return self._secrets.get(ref)

    def delete(self, ref: str) -> None:
        self._secrets.pop(ref, None)

    def clear(self) -> None:
        self._secrets.clear()


class SystemCredentialStore:
    """系统凭据库封装；不可用时 ``available=False``（不抛异常、不落盘）。"""

    def __init__(self, service_name: str = SERVICE_NAME, *, enabled: bool = True) -> None:
        self._service_name = service_name
        self._keyring = None
        self.available = False
        if not enabled:
            return
        try:
            import keyring

            backend = keyring.get_keyring()
            module = type(backend).__module__
            if "fail" in module or "null" in module:
                return
            self._keyring = keyring
            self.available = True
        except Exception:  # pragma: no cover - 依赖缺失/无可用后端
            self.available = False

    def set(self, ref: str, secret: str) -> None:
        if not self.available or self._keyring is None:
            raise CredentialStoreError("系统凭据库不可用")
        try:
            self._keyring.set_password(self._service_name, ref, secret)
        except Exception as exc:  # pragma: no cover - 后端运行时错误
            raise CredentialStoreError(f"写入系统凭据库失败：{type(exc).__name__}") from exc

    def get(self, ref: str) -> str | None:
        if not self.available or self._keyring is None:
            raise CredentialStoreError("系统凭据库不可用")
        try:
            return self._keyring.get_password(self._service_name, ref)
        except Exception as exc:  # pragma: no cover
            raise CredentialStoreError(f"读取系统凭据库失败：{type(exc).__name__}") from exc

    def delete(self, ref: str) -> None:
        if not self.available or self._keyring is None:
            return
        try:
            self._keyring.delete_password(self._service_name, ref)
        except Exception:  # pragma: no cover - 条目不存在等情况
            return


@dataclass(frozen=True)
class CredentialSaveResult:
    mode: CredentialMode
    stored: bool
    warning: str | None = None


@dataclass
class CredentialService:
    """会话/系统两套存储的统一入口。"""

    session: SessionCredentialStore = field(default_factory=SessionCredentialStore)
    system: CredentialStore = field(default_factory=SystemCredentialStore)

    @staticmethod
    def reference_for(profile_id: str) -> str:
        return f"model-profile/{profile_id}"

    def store(
        self, *, mode: CredentialMode, ref: str, secret: str | None
    ) -> CredentialSaveResult:
        """按模式保存密钥；系统凭据库不可用时降级为会话并返回警告。"""

        if mode is CredentialMode.NONE or secret is None or secret == "":
            self.remove(ref=ref)
            return CredentialSaveResult(mode=CredentialMode.NONE, stored=False)

        if mode is CredentialMode.SESSION:
            self.system.delete(ref)
            self.session.set(ref, secret)
            return CredentialSaveResult(mode=CredentialMode.SESSION, stored=True)

        # SYSTEM
        if not getattr(self.system, "available", False):
            self.session.set(ref, secret)
            return CredentialSaveResult(
                mode=CredentialMode.SESSION,
                stored=True,
                warning="系统凭据库不可用，密钥改为仅在本会话内保存（不会写入磁盘）。",
            )
        try:
            self.system.set(ref, secret)
        except CredentialStoreError:
            self.session.set(ref, secret)
            return CredentialSaveResult(
                mode=CredentialMode.SESSION,
                stored=True,
                warning="写入系统凭据库失败，已改为仅在本会话内保存。",
            )
        self.session.delete(ref)
        return CredentialSaveResult(mode=CredentialMode.SYSTEM, stored=True)

    def load(self, *, mode: CredentialMode, ref: str) -> str | None:
        """读取密钥；系统模式失败时回退到会话（降级保存过的密钥仍可用）。"""

        if mode is CredentialMode.SESSION:
            return self.session.get(ref)
        if mode is CredentialMode.SYSTEM:
            try:
                secret = self.system.get(ref)
            except CredentialStoreError:
                secret = None
            return secret or self.session.get(ref)
        return None

    def has(self, *, mode: CredentialMode, ref: str) -> bool:
        return bool(self.load(mode=mode, ref=ref))

    def remove(self, *, ref: str) -> None:
        self.session.delete(ref)
        self.system.delete(ref)
