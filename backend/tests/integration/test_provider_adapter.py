"""T07 集成测试：真实 HTTP 适配器（MockTransport）与连接测试端点（F13/F20）。

- 用 ``httpx.MockTransport`` 模拟 401/429/超时/500/坏 JSON/错 schema，验证错误映射与“绝不假成功”。
- 连接测试端点用显式启用的 FakeProvider（不发网络请求），并检查推理尝试记录与未知用量不被写成 0。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ndr.config import Settings
from ndr.domain.enums import CredentialMode, ErrorCode, InferenceRunState  # noqa: F401
from ndr.llm.adapters import ChatCompletionsAdapter
from ndr.llm.errors import KIND_TO_CODE, ProviderError, ProviderErrorKind
from ndr.llm.prompts.connection import ECHO_OBJECT
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.models import InferenceRun
from ndr.storage.transactions import transaction

VALID_BODY = {
    "choices": [{"message": {"content": json.dumps(ECHO_OBJECT, ensure_ascii=False)}}],
    "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
}


def _adapter(handler, *, api_key: str | None = "sk-test-key") -> ChatCompletionsAdapter:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ChatCompletionsAdapter(
        base_url="https://api.example.com/v1",
        model="example-model",
        api_key=api_key,
        client=client,
        timeout_seconds=1.0,
    )


def _run(coro):
    return asyncio.run(coro)


def test_successful_connection_test_returns_usage_and_latency() -> None:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.content.decode("utf-8")
        return httpx.Response(200, json=VALID_BODY)

    result = _run(_adapter(handler).test_connection())

    assert result.ok is True
    assert result.adapter == "chat-completions"
    assert result.latency_ms is not None
    assert result.usage is not None
    assert result.usage["input_tokens"] == 12
    assert result.usage["output_tokens"] == 8
    assert result.usage["total_tokens"] == 20
    assert result.usage["unknown"] is False
    assert seen["url"] == "https://api.example.com/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test-key"
    # 请求体里包含微型任务与 json_object 声明，但不含密钥
    assert "sk-test-key" not in (seen["body"] or "")
    assert "response_format" in (seen["body"] or "")


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [
        (401, ProviderErrorKind.AUTH),
        (403, ProviderErrorKind.AUTH),
        (404, ProviderErrorKind.MODEL_NOT_FOUND),
        (429, ProviderErrorKind.RATE_LIMITED),
        (500, ProviderErrorKind.UNAVAILABLE),
    ],
)
def test_http_errors_map_to_stable_codes(status: int, expected_code: ProviderErrorKind) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "boom"}})

    result = _run(_adapter(handler).test_connection())

    assert result.ok is False
    # 对外暴露的是稳定业务错误码（ErrorCode），不是内部 kind
    assert KIND_TO_CODE[expected_code].value in result.detail
    assert result.usage is None  # 失败不伪造用量


def test_timeout_is_mapped_and_not_reported_as_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("too slow", request=request)

    result = _run(_adapter(handler).test_connection())

    assert result.ok is False
    assert ErrorCode.PROVIDER_TIMEOUT.value in result.detail


def test_non_json_and_wrong_schema_are_rejected() -> None:
    def not_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>oops</html>")

    result = _run(_adapter(not_json).test_connection())
    assert result.ok is False
    assert ErrorCode.INVALID_MODEL_OUTPUT.value in result.detail

    def wrong_schema(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"schema_version": "9.9", "labels": 3}'}}]},
        )

    result = _run(_adapter(wrong_schema).test_connection())
    assert result.ok is False
    assert result.detail.startswith(ErrorCode.INVALID_MODEL_OUTPUT.value)


def test_connection_accepts_fenced_json_and_leaves_token_room() -> None:
    """真实提供方常把 JSON 包在 ```json 代码块里：解析要接受，且不能再用 64 token 把它截断。"""

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode("utf-8")
        fenced = "```json\n" + json.dumps(ECHO_OBJECT, ensure_ascii=False) + "\n```"
        return httpx.Response(200, json={"choices": [{"message": {"content": fenced}}]})

    result = _run(_adapter(handler).test_connection())

    assert result.ok is True
    assert json.loads(seen["body"])["max_tokens"] >= 128


def test_truncated_model_output_is_reported_with_snippet() -> None:
    """max_tokens 截断会得到不完整 JSON：失败详情必须带原始片段与解析原因，便于定位真实提供方。"""

    truncated = json.dumps(ECHO_OBJECT, ensure_ascii=False)[:35]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": truncated}}],
                "usage": {"prompt_tokens": 132, "completion_tokens": 64, "total_tokens": 196},
            },
        )

    result = _run(_adapter(handler).test_connection())

    assert result.ok is False
    assert result.detail.startswith(ErrorCode.INVALID_MODEL_OUTPUT.value)
    assert "原始输出片段" in result.detail
    assert truncated[:20] in result.detail
    # 真用量照实回报（不因为解析失败就丢掉提供方给的 usage）
    assert result.usage is not None and result.usage["output_tokens"] == 64


def test_empty_message_content_reports_response_snippet() -> None:
    """有的兼容服务把内容放在别的字段：content 为空时要把响应片段带出来。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "", "reasoning_content": "thinking..."}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            },
        )

    result = _run(_adapter(handler).test_connection())

    assert result.ok is False
    assert "响应片段" in result.detail
    assert "reasoning_content" in result.detail


def test_generate_labels_returns_parsed_object_and_surfaces_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"schema_version": "1.0", "labels": []})}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1},
            },
        )

    parsed = _run(_adapter(handler).generate_labels({"messages": [{"role": "user", "content": "x"}]}))
    assert parsed["schema_version"] == "1.0"
    assert parsed["_usage"]["total_tokens"] == 4

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "不是 JSON"}}]})

    with pytest.raises(ProviderError) as excinfo:
        _run(_adapter(broken).generate_labels({"messages": [{"role": "user", "content": "x"}]}))
    assert excinfo.value.kind is ProviderErrorKind.INVALID_OUTPUT


def test_error_details_never_contain_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized: Bearer sk-test-key is invalid")

    with pytest.raises(ProviderError) as excinfo:
        _run(
            _adapter(handler).generate_labels(
                {"messages": [{"role": "user", "content": "x"}]}
            )
        )
    error = excinfo.value
    assert error.kind is ProviderErrorKind.AUTH
    assert "sk-test-key" not in json.dumps(error.details, ensure_ascii=False)


# --- 连接测试端点（FakeProvider，不访问网络） ---


def test_connection_endpoint_with_fake_provider_records_run(
    fake_provider_client: TestClient, tmp_path
) -> None:
    created = fake_provider_client.post(
        "/api/model-profiles",
        json={
            "name": "测试提供方",
            "protocol": "fake-provider",
            "base_url": "http://127.0.0.1:1",
            "model": "fake-model",
            "credential_mode": "none",
        },
    )
    assert created.status_code == 201, created.text
    profile_id = created.json()["data"]["id"]

    response = fake_provider_client.post("/api/model-profiles/test", json={"profile_id": profile_id})
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["ok"] is True
    assert data["adapter"] == "fake-provider"
    assert data["usage_unknown"] is True  # FakeProvider 没有真实 usage
    assert data["run_id"]

    settings = fake_provider_client.app.state.settings
    engine = create_db_engine(settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            run = session.get(InferenceRun, data["run_id"])
            assert run is not None
            assert run.state is InferenceRunState.SUCCEEDED
            assert run.usage_json is None  # 未知用量保持 NULL，不写成 0
            snapshot = json.loads(run.profile_snapshot_json)
            assert snapshot["purpose"] == "connection-test"
            assert "api_key" not in run.profile_snapshot_json
    finally:
        engine.dispose()


def test_connection_endpoint_refuses_fake_provider_without_enablement(
    migrated_client: TestClient,
) -> None:
    profile_id = migrated_client.post(
        "/api/model-profiles",
        json={
            "name": "测试提供方",
            "protocol": "fake-provider",
            "base_url": "http://127.0.0.1:1",
            "model": "fake-model",
            "credential_mode": "none",
        },
    ).json()["data"]["id"]

    response = migrated_client.post("/api/model-profiles/test", json={"profile_id": profile_id})
    assert response.status_code == 422
    assert "FakeProvider 未启用" in response.json()["error"]["message"]


def test_connection_endpoint_accepts_draft_and_cleans_up_key(
    fake_provider_client: TestClient,
) -> None:
    response = fake_provider_client.post(
        "/api/model-profiles/test",
        json={
            "draft": {
                "name": "临时草稿",
                "protocol": "fake-provider",
                "base_url": "http://127.0.0.1:1",
                "model": "fake-model",
                "credential_mode": "session",
                "api_key": "sk-draft-secret",
            }
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["ok"] is True
    assert "sk-draft-secret" not in response.text

    credentials = fake_provider_client.app.state.credentials
    assert credentials.session.refs() == ()  # 草稿密钥用完即清理
    assert fake_provider_client.get("/api/model-profiles").json()["data"] == []  # 草稿不落库


def test_connection_endpoint_requires_profile_or_draft(fake_provider_client: TestClient) -> None:
    response = fake_provider_client.post("/api/model-profiles/test", json={})
    assert response.status_code == 422

    both = fake_provider_client.post(
        "/api/model-profiles/test",
        json={
            "profile_id": "x",
            "draft": {
                "name": "n",
                "protocol": "fake-provider",
                "base_url": "http://127.0.0.1:1",
                "model": "m",
            },
        },
    )
    assert both.status_code == 422


def test_connection_endpoint_reports_failure_without_fake_success(tmp_path) -> None:
    """真实适配器连不上时必须是失败：错误码 + 推理尝试 FAILED + usage 为 NULL。"""

    from ndr.app import create_app
    from ndr.storage.migrate import run_migrations

    settings = Settings(
        data_dir=tmp_path / "data",
        credential_backend="session",
        allow_fake_provider=False,
        llm_timeout_seconds=2.0,
    )
    run_migrations(settings)
    app = create_app(settings)
    with TestClient(app) as client:
        created = client.post(
            "/api/model-profiles",
            json={
                "name": "连不上的地址",
                "protocol": "chat-completions-compatible",
                "base_url": "http://127.0.0.1:9/v1",
                "model": "no-such-model",
                "credential_mode": "none",
            },
        ).json()["data"]

        response = client.post("/api/model-profiles/test", json={"profile_id": created["id"]})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ok"] is False
    assert data["error_code"] in {"PROVIDER_UNAVAILABLE", "PROVIDER_TIMEOUT"}
    assert data["usage"] is None and data["usage_unknown"] is True

    engine = create_db_engine(settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            runs = list(session.execute(select(InferenceRun)).scalars())
            assert len(runs) == 1
            assert runs[0].state is InferenceRunState.FAILED
            assert runs[0].usage_json is None
            assert runs[0].error_code in {"PROVIDER_UNAVAILABLE", "PROVIDER_TIMEOUT"}
    finally:
        engine.dispose()
