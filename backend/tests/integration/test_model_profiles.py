"""T06 集成测试：模型配置 CRUD、密钥三态与凭据降级。

注意：`migrated_client` 使用 `credential_backend="session"`，绝不触碰真实系统凭据库；
真实的 keyring 后端行为由 `test_credentials.py` 的替身覆盖。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from ndr.config import Settings
from ndr.domain.enums import CredentialMode, JobKind, JobState
from ndr.llm.credentials import CredentialService
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.models import Job
from ndr.storage.transactions import transaction

SECRET = "sk-super-secret-value-123"

PROFILE = {
    "name": "本地网关",
    "protocol": "chat-completions-compatible",
    "base_url": "https://api.example.com/v1/",
    "model": "example-chat-model",
    "params": {"temperature": 0.2},
    "credential_mode": "session",
}


def _create(client: TestClient, **overrides) -> dict:
    payload = {**PROFILE, **overrides}
    response = client.post("/api/model-profiles", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_create_profile_returns_no_secret(migrated_client: TestClient, migrated_settings: Settings) -> None:
    response = migrated_client.post(
        "/api/model-profiles", json={**PROFILE, "api_key": SECRET}
    )
    assert response.status_code == 201
    raw = response.text
    assert SECRET not in raw  # 绝不回传密钥
    assert "api_key" not in raw

    data = response.json()["data"]
    assert data["name"] == "本地网关"
    assert data["base_url"] == "https://api.example.com/v1"  # 规范化去掉尾部斜杠
    assert data["model"] == "example-chat-model"
    assert data["params"] == {"temperature": 0.2}
    assert data["credential_mode"] == "session"
    assert data["has_key"] is True
    assert data["version"] == 1
    assert "credential_ref" not in data  # 内部引用不暴露

    # 密钥确实进入了运行时的凭据服务（不是被丢弃）
    credentials: CredentialService = migrated_client.app.state.credentials
    ref = CredentialService.reference_for(data["id"])
    assert credentials.load(mode=CredentialMode.SESSION, ref=ref) == SECRET


def test_list_profiles_never_leaks_key(migrated_client: TestClient) -> None:
    created = _create(migrated_client, api_key=SECRET)

    response = migrated_client.get("/api/model-profiles")
    assert response.status_code == 200
    assert SECRET not in response.text
    items = response.json()["data"]
    assert [item["id"] for item in items] == [created["id"]]
    assert items[0]["has_key"] is True
    assert all("api_key" not in item and "credential_ref" not in item for item in items)


def test_profile_without_key_has_key_false(migrated_client: TestClient) -> None:
    data = _create(migrated_client)
    assert data["has_key"] is False
    assert migrated_client.get("/api/model-profiles").json()["data"][0]["has_key"] is False


def test_patch_keep_replace_and_remove_key(migrated_client: TestClient) -> None:
    created = _create(migrated_client, api_key=SECRET)
    profile_id = created["id"]
    credentials: CredentialService = migrated_client.app.state.credentials
    ref = CredentialService.reference_for(profile_id)

    # keep：只改字段，密钥保持
    keep = migrated_client.patch(
        f"/api/model-profiles/{profile_id}",
        json={"model": "another-model", "expected_version": created["version"]},
    )
    assert keep.status_code == 200
    assert keep.json()["data"]["model"] == "another-model"
    assert keep.json()["data"]["has_key"] is True
    assert SECRET not in keep.text
    assert credentials.load(mode=CredentialMode.SESSION, ref=ref) == SECRET

    # replace：换密钥
    replaced = migrated_client.patch(
        f"/api/model-profiles/{profile_id}",
        json={"api_key": "sk-new-value", "expected_version": keep.json()["data"]["version"]},
    )
    assert replaced.status_code == 200
    assert replaced.json()["data"]["has_key"] is True
    assert "sk-new-value" not in replaced.text
    assert credentials.load(mode=CredentialMode.SESSION, ref=ref) == "sk-new-value"

    # remove：清除密钥，模式回到 none
    removed = migrated_client.patch(
        f"/api/model-profiles/{profile_id}",
        json={
            "remove_api_key": True,
            "expected_version": replaced.json()["data"]["version"],
        },
    )
    assert removed.status_code == 200
    assert removed.json()["data"]["has_key"] is False
    assert removed.json()["data"]["credential_mode"] == "none"
    assert credentials.load(mode=CredentialMode.SESSION, ref=ref) is None


def test_stale_expected_version_returns_409(migrated_client: TestClient) -> None:
    created = _create(migrated_client)
    response = migrated_client.patch(
        f"/api/model-profiles/{created['id']}",
        json={"model": "x", "expected_version": created["version"] + 5},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VERSION_CONFLICT"
    assert response.json()["error"]["details"]["current_version"] == created["version"]


def test_system_mode_falls_back_to_session_with_warning(migrated_client: TestClient) -> None:
    """测试环境禁用系统凭据库 → 应降级为会话密钥并给出警告（不明文落盘）。"""

    response = migrated_client.post(
        "/api/model-profiles",
        json={**PROFILE, "name": "系统凭据尝试", "credential_mode": "system", "api_key": SECRET},
    )
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["credential_mode"] == "session"
    assert data["has_key"] is True
    assert data["credential_warning"] and "系统凭据库" in data["credential_warning"]
    assert SECRET not in response.text


def test_duplicate_name_and_unknown_protocol_are_rejected(migrated_client: TestClient) -> None:
    _create(migrated_client)

    duplicate = migrated_client.post("/api/model-profiles", json=PROFILE)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "RESOURCE_CONFLICT"

    unknown = migrated_client.post(
        "/api/model-profiles", json={**PROFILE, "name": "另一个", "protocol": "unknown-protocol"}
    )
    assert unknown.status_code == 422
    assert "unknown-protocol" in json.dumps(unknown.json()["error"]["details"], ensure_ascii=False)


def test_full_endpoint_base_url_is_rejected(migrated_client: TestClient) -> None:
    response = migrated_client.post(
        "/api/model-profiles",
        json={**PROFILE, "base_url": "https://api.example.com/v1/chat/completions"},
    )
    assert response.status_code == 422
    details = response.json()["error"]["details"]
    assert "根路径" in response.json()["error"]["message"]
    assert details["hint"]


def test_params_may_not_contain_secrets(migrated_client: TestClient) -> None:
    response = migrated_client.post(
        "/api/model-profiles",
        json={**PROFILE, "params": {"api_key": SECRET}},
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["offending_keys"] == ["api_key"]
    assert SECRET not in response.text


def test_delete_profile_and_missing_profile(migrated_client: TestClient) -> None:
    created = _create(migrated_client, api_key=SECRET)
    credentials: CredentialService = migrated_client.app.state.credentials
    ref = CredentialService.reference_for(created["id"])

    assert migrated_client.delete(f"/api/model-profiles/{created['id']}").status_code == 204
    assert migrated_client.get("/api/model-profiles").json()["data"] == []
    assert credentials.load(mode=CredentialMode.SESSION, ref=ref) is None  # 凭据一并清理

    assert migrated_client.delete(f"/api/model-profiles/{created['id']}").status_code == 404
    assert migrated_client.patch(
        f"/api/model-profiles/{created['id']}", json={"model": "x"}
    ).status_code == 404


def test_delete_is_refused_when_referenced_by_job(
    migrated_client: TestClient, migrated_settings: Settings
) -> None:
    created = _create(migrated_client)

    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            session.add(
                Job(
                    kind=JobKind.INFERENCE,
                    state=JobState.COMPLETED,
                    range_json="{}",
                    profile_snapshot_json=json.dumps({"profile_id": created["id"]}),
                )
            )
    finally:
        engine.dispose()

    response = migrated_client.delete(f"/api/model-profiles/{created['id']}")
    assert response.status_code == 409
    details = response.json()["error"]["details"]
    assert details["profile_id"] == created["id"]
    assert details["job_ids"]


def test_creating_profiles_does_not_start_any_job(
    migrated_client: TestClient, migrated_settings: Settings
) -> None:
    """T06 只写配置：默认不发起任何真实调用，也不产生任务。"""

    _create(migrated_client, api_key=SECRET)

    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            assert session.execute(select(func.count(Job.id))).scalar_one() == 0
    finally:
        engine.dispose()


def test_protocols_endpoint_declares_capabilities(migrated_client: TestClient) -> None:
    response = migrated_client.get("/api/model-profiles/protocols")
    assert response.status_code == 200
    items = {item["protocol"]: item for item in response.json()["data"]}

    chat = items["chat-completions-compatible"]
    # 兼容服务不声称支持严格 json_schema（T07 按实际提供方核对后再打开）
    assert chat["supports_json_schema"] is False
    assert chat["supports_json_object"] is True
    assert "根路径" in chat["notes"]

    fake = items["fake-provider"]
    assert "仅供测试" in fake["notes"]


def test_credentials_are_scoped_per_profile(migrated_client: TestClient) -> None:
    first = _create(migrated_client, name="配置一", api_key="sk-first")
    second = _create(migrated_client, name="配置二", api_key="sk-second")
    credentials: CredentialService = migrated_client.app.state.credentials

    assert credentials.load(
        mode=CredentialMode.SESSION, ref=CredentialService.reference_for(first["id"])
    ) == "sk-first"
    assert credentials.load(
        mode=CredentialMode.SESSION, ref=CredentialService.reference_for(second["id"])
    ) == "sk-second"


def test_session_secret_is_not_written_to_disk(
    migrated_client: TestClient, migrated_settings: Settings
) -> None:
    _create(migrated_client, api_key=SECRET)

    # 数据目录下不应出现包含密钥的文件（密钥只在内存里）
    for path in migrated_settings.data_dir.rglob("*"):
        if path.is_file() and path.suffix in {".json", ".txt", ".sqlite3", ".log"}:
            assert SECRET not in path.read_bytes().decode("utf-8", errors="ignore")
