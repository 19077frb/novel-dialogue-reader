"""T06 单元测试：凭据服务的会话/系统存储与降级行为（不触碰真实系统凭据库）。"""

from __future__ import annotations

import pytest

from ndr.domain.enums import CredentialMode
from ndr.llm.credentials import (
    CredentialService,
    CredentialStoreError,
    SessionCredentialStore,
)


class FakeSystemStore:
    """可控的系统凭据库替身。"""

    def __init__(self, *, available: bool = True, fail_on_set: bool = False) -> None:
        self.available = available
        self.fail_on_set = fail_on_set
        self.secrets: dict[str, str] = {}
        self.calls: list[str] = []

    def set(self, ref: str, secret: str) -> None:
        self.calls.append(f"set:{ref}")
        if self.fail_on_set:
            raise CredentialStoreError("模拟写入失败")
        self.secrets[ref] = secret

    def get(self, ref: str) -> str | None:
        self.calls.append(f"get:{ref}")
        return self.secrets.get(ref)

    def delete(self, ref: str) -> None:
        self.calls.append(f"delete:{ref}")
        self.secrets.pop(ref, None)


REF = "model-profile/test-1"


def test_session_store_roundtrip() -> None:
    store = SessionCredentialStore()
    store.set(REF, "sk-session")
    assert store.get(REF) == "sk-session"
    store.delete(REF)
    assert store.get(REF) is None


def test_session_mode_never_touches_system_store() -> None:
    system = FakeSystemStore()
    service = CredentialService(session=SessionCredentialStore(), system=system)

    result = service.store(mode=CredentialMode.SESSION, ref=REF, secret="sk-1")

    assert result.mode is CredentialMode.SESSION
    assert result.warning is None
    assert service.load(mode=CredentialMode.SESSION, ref=REF) == "sk-1"
    assert system.secrets == {}
    assert "set" not in {call.split(":")[0] for call in system.calls}


def test_system_mode_uses_system_store_and_clears_session() -> None:
    session = SessionCredentialStore()
    session.set(REF, "old-session")
    system = FakeSystemStore()
    service = CredentialService(session=session, system=system)

    result = service.store(mode=CredentialMode.SYSTEM, ref=REF, secret="sk-system")

    assert result.mode is CredentialMode.SYSTEM
    assert system.secrets[REF] == "sk-system"
    assert session.get(REF) is None  # 不留旧密钥
    assert service.load(mode=CredentialMode.SYSTEM, ref=REF) == "sk-system"


def test_system_mode_falls_back_to_session_when_unavailable() -> None:
    system = FakeSystemStore(available=False)
    service = CredentialService(session=SessionCredentialStore(), system=system)

    result = service.store(mode=CredentialMode.SYSTEM, ref=REF, secret="sk-fallback")

    assert result.mode is CredentialMode.SESSION
    assert result.stored is True
    assert result.warning and "系统凭据库不可用" in result.warning
    assert service.load(mode=CredentialMode.SESSION, ref=REF) == "sk-fallback"


def test_system_mode_falls_back_when_write_fails() -> None:
    system = FakeSystemStore(fail_on_set=True)
    service = CredentialService(session=SessionCredentialStore(), system=system)

    result = service.store(mode=CredentialMode.SYSTEM, ref=REF, secret="sk-fail")

    assert result.mode is CredentialMode.SESSION
    assert result.warning and "写入系统凭据库失败" in result.warning
    assert service.load(mode=CredentialMode.SESSION, ref=REF) == "sk-fail"


def test_system_load_falls_back_to_session_when_read_fails() -> None:
    system = FakeSystemStore(available=False)
    service = CredentialService(session=SessionCredentialStore(), system=system)
    service.session.set(REF, "sk-session")

    assert service.load(mode=CredentialMode.SYSTEM, ref=REF) == "sk-session"
    assert service.has(mode=CredentialMode.SYSTEM, ref=REF) is True


def test_empty_secret_removes_credential() -> None:
    system = FakeSystemStore()
    service = CredentialService(session=SessionCredentialStore(), system=system)
    service.store(mode=CredentialMode.SYSTEM, ref=REF, secret="sk-1")

    result = service.store(mode=CredentialMode.SYSTEM, ref=REF, secret="")

    assert result.mode is CredentialMode.NONE
    assert result.stored is False
    assert service.has(mode=CredentialMode.NONE, ref=REF) is False
    assert service.load(mode=CredentialMode.SYSTEM, ref=REF) is None


def test_remove_clears_both_stores() -> None:
    system = FakeSystemStore()
    session = SessionCredentialStore()
    service = CredentialService(session=session, system=system)
    service.store(mode=CredentialMode.SYSTEM, ref=REF, secret="sk-1")
    session.set(REF, "sk-leftover")

    service.remove(ref=REF)

    assert session.get(REF) is None
    assert REF not in system.secrets


def test_reference_is_derived_from_profile_id() -> None:
    assert CredentialService.reference_for("abc") == "model-profile/abc"


def test_credential_store_error_is_runtime_error() -> None:
    with pytest.raises(CredentialStoreError):
        raise CredentialStoreError("boom")
