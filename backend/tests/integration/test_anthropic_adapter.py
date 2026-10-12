"""Offline Messages protocol coverage; never use real keys or network."""

import asyncio
import json

import httpx
import pytest

from ndr.domain.enums import CredentialMode
from ndr.jobs.service import profile_snapshot
from ndr.llm.adapters import AdapterSpec, AnthropicMessagesAdapter, build_adapter
from ndr.llm.credentials import CredentialService
from ndr.llm.errors import ProviderError, ProviderErrorKind
from ndr.llm.prompts.connection import ECHO_OBJECT
from ndr.storage.models import ModelProfile

MESSAGES = [{"role": "system", "content": "规则一"},
            {"role": "system", "content": "规则二"},
            {"role": "user", "content": "原创测试"}]


def body(text=None, **extra):
    return {"type": "message", "role": "assistant", "stop_reason": "end_turn",
            "content": [{"type": "thinking", "thinking": "not JSON", "signature": "opaque"},
                        {"type": "text", "text": text or json.dumps(ECHO_OBJECT)}],
            "usage": {"input_tokens": 10, "output_tokens": 20}, **extra}


def invoke(handler, *, params=None, connection=False, payload=None, base_url=None):
    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = AnthropicMessagesAdapter(
                base_url=base_url or "https://example.com/v1", model="fixture-model",
                api_key="private-test-key", params=params, client=client,
            )
            if connection:
                return await adapter.test_connection()
            return await adapter.generate_labels(payload or {"messages": MESSAGES})
    return asyncio.run(execute())


@pytest.mark.parametrize("root", ["https://example.com", "https://example.com/v1/"])
def test_endpoint_headers_system_and_json_result(root):
    def handler(request):
        assert str(request.url) == "https://example.com/v1/messages"
        assert request.headers["x-api-key"] == "private-test-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert "authorization" not in request.headers
        data = json.loads(request.content)
        assert data["system"] == "规则一\n\n规则二"
        assert data["messages"] == [MESSAGES[-1]]
        assert data["model"] == "fixture-model"
        assert data["max_tokens"] == 256
        assert data["stream"] is False
        assert "response_format" not in data
        return httpx.Response(200, json=body('{"labels":[]}'))

    result = invoke(handler, base_url=root)
    assert result["labels"] == []
    assert result["_usage"]["total_tokens"] == 30
    assert result.receipt["response"]["content"][0]["type"] == "thinking"
    assert "thinking" not in json.dumps(dict(result))
    assert "private-test-key" not in json.dumps(result.receipt)


@pytest.mark.parametrize("mode", ["enabled", "adaptive", "disabled"])
def test_task_snapshot_thinking_mapping_and_output_override(mode):
    profile = ModelProfile(id="p", name="fixture", protocol="anthropic-messages",
                           base_url="https://example.com/v1", model="fixture",
                           credential_mode=CredentialMode.NONE,
                           params_json=json.dumps({"max_tokens": 10000, "temperature": 0.2,
                                                   "top_p": 0.5, "top_k": 20}))
    snapshot = profile_snapshot(profile, {"thinking_mode": mode, "reasoning_effort": "high"})

    def handler(request):
        data = json.loads(request.content)
        assert data["max_tokens"] == 6000
        assert "reasoning_effort" not in data
        assert data["thinking"]["type"] == mode
        if mode == "enabled":
            assert data["thinking"]["budget_tokens"] == 3000
            assert "output_config" not in data
        elif mode == "adaptive":
            assert data["output_config"]["effort"] == "high"
        else:
            assert "output_config" not in data
            assert data["temperature"] == 0.2
        if mode != "disabled":
            assert not {"temperature", "top_p", "top_k"} & data.keys()
        return httpx.Response(200, json=body())

    invoke(handler, params=snapshot["params"],
           payload={"messages": MESSAGES, "max_tokens_override": 6000})
    assert "thinking" not in json.loads(profile.params_json)


@pytest.mark.parametrize("params", [
    {"max_tokens": 1024, "thinking": {"type": "enabled"}},
    {"max_tokens": 4096, "thinking": {"type": "enabled", "budget_tokens": 4096}},
    {"thinking": "enabled"}, {"thinking": {"type": "invalid"}},
    {"stream": True}, {"frequency_penalty": 0.2}, {"output_config": "high"},
    {"max_tokens": True},
    {"reasoning_effort": ["high"]},
])
def test_invalid_parameters_fail_before_network(params):
    def handler(request):
        pytest.fail("invalid parameters must not send requests")
    with pytest.raises(ProviderError):
        invoke(handler, params=params)


def test_connection_uses_messages_and_preserves_usage():
    def handler(request):
        data = json.loads(request.content)
        assert data["max_tokens"] == 4096
        assert data["thinking"]["budget_tokens"] == 2048
        return httpx.Response(200, json=body())
    result = invoke(handler, params={"thinking": {"type": "enabled"}}, connection=True)
    assert result.ok
    assert result.protocol == result.adapter == "anthropic-messages"
    assert result.usage["total_tokens"] == 30


def test_text_blocks_are_combined_and_cache_input_is_counted_once():
    result = invoke(lambda request: httpx.Response(200, json=body(
        content=[{"type": "text", "text": '{"labels":'}, {"type": "text", "text": '[]}'}],
        usage={"input_tokens": 10, "output_tokens": 20, "cache_creation_input_tokens": 30,
               "cache_read_input_tokens": 40,
               "cache_creation": {"ephemeral_5m_input_tokens": 30}})))
    assert result["_usage"]["input_tokens"] == 80
    assert result["_usage"]["total_tokens"] == 100


@pytest.mark.parametrize("usage", [None, {}, {"output_tokens": 20},
                                  {"input_tokens": 1, "output_tokens": True},
                                  {"input_tokens": 10, "output_tokens": 20,
                                   "cache_read_input_tokens": -1}])
def test_missing_or_invalid_usage_stays_unknown(usage):
    result = invoke(lambda request: httpx.Response(200, json=body(usage=usage)))
    assert result["_usage"]["unknown"] is True
    assert result["_usage"]["total_tokens"] is None


@pytest.mark.parametrize("extra", [
    {"stop_reason": "max_tokens"}, {"stop_reason": "refusal"},
    {"stop_reason": "tool_use"}, {"stop_reason": "pause_turn"},
    {"content": [{"type": "thinking", "thinking": "private-test-key"}]},
    {"content": [{"type": "tool_use", "input": {}}]}, {"content": "wrong"},
])
def test_non_final_or_non_text_result_keeps_usage_and_redacted_receipt(extra):
    with pytest.raises(ProviderError) as caught:
        invoke(lambda request: httpx.Response(200, json=body(**extra)))
    error = caught.value
    assert error.details["usage"]["total_tokens"] == 30
    assert error.receipt["request"]["system"] == "规则一\n\n规则二"
    assert "private-test-key" not in json.dumps(error.receipt)
    assert "private-test-key" not in json.dumps(error.as_dict)
    if extra.get("stop_reason") == "max_tokens":
        assert error.details["finish_reason"] == "length"


@pytest.mark.parametrize("status, kind", [(401, ProviderErrorKind.AUTH),
                                         (402, ProviderErrorKind.QUOTA_EXHAUSTED),
                                         (429, ProviderErrorKind.RATE_LIMITED),
                                         (529, ProviderErrorKind.UNAVAILABLE)])
def test_http_errors_redact_keys_and_do_not_retry(status, kind):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="private-test-key " + "x" * 1000)
    with pytest.raises(ProviderError) as caught:
        invoke(handler)
    assert caught.value.kind is kind
    assert len(calls) == 1
    assert "private-test-key" not in json.dumps(caught.value.receipt)
    assert "private-test-key" not in json.dumps(caught.value.as_dict)


def test_timeout_records_request_but_no_invented_usage():
    def handler(request):
        assert request.extensions["timeout"]["read"] == 600
        raise httpx.ReadTimeout("fixture", request=request)
    with pytest.raises(ProviderError) as caught:
        invoke(handler, params={"timeout_seconds": 600})
    assert caught.value.kind is ProviderErrorKind.TIMEOUT
    assert caught.value.receipt["response"] is None
    assert caught.value.receipt["request"]["model"] == "fixture-model"
    assert "usage" not in caught.value.details


def test_invalid_json_keeps_usage_and_original_response():
    with pytest.raises(ProviderError) as caught:
        invoke(lambda request: httpx.Response(200, json=body("not JSON")))
    assert caught.value.details["usage"]["total_tokens"] == 30
    assert caught.value.receipt["response"]["content"][-1]["text"] == "not JSON"


def test_registry_and_model_profile_crud(migrated_client):
    response = migrated_client.post("/api/model-profiles", json={
        "name": "anthropic fixture", "protocol": "anthropic-messages",
        "base_url": "https://example.com/v1", "model": "fixture-model",
        "credential_mode": "session", "api_key": "private-test-key",
    })
    assert response.status_code == 201
    profile = response.json()["data"]
    assert profile["protocol"] == "anthropic-messages" and profile["has_key"]
    assert "private-test-key" not in response.text
    spec = AdapterSpec(protocol=profile["protocol"], base_url=profile["base_url"],
                       model=profile["model"], credential_mode=CredentialMode.SESSION,
                       credential_ref=CredentialService.reference_for(profile["id"]))
    adapter = build_adapter(spec, migrated_client.app.state.credentials)
    assert isinstance(adapter, AnthropicMessagesAdapter)
    assert adapter._headers()["x-api-key"] == "private-test-key"
    protocols = migrated_client.get("/api/model-profiles/protocols").json()["data"]
    capability = next(item for item in protocols if item["protocol"] == "anthropic-messages")
    assert not capability["supports_json_object"]
    assert not capability["supports_json_schema"]


def test_connection_api_uses_real_factory_with_mock_http(migrated_client, monkeypatch):
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=body())))
    monkeypatch.setattr("ndr.api.model_profiles.build_adapter",
                        lambda spec, credentials, **kw: build_adapter(spec, credentials,
                                                                      client=client, **kw))
    try:
        result = migrated_client.post("/api/model-profiles/test", json={"draft": {
            "name": "isolated", "protocol": "anthropic-messages",
            "base_url": "https://example.com/v1", "model": "fixture-model",
            "credential_mode": "session", "api_key": "private-test-key",
        }})
        assert result.status_code == 200, result.text
        data = result.json()["data"]
        assert data["ok"] and data["adapter"] == "anthropic-messages"
        assert data["usage"]["total_tokens"] == 30 and data["run_id"]
        assert "private-test-key" not in result.text
    finally:
        asyncio.run(client.aclose())


def test_roster_finalization_recovery_does_not_resend_anthropic(migrated_client, monkeypatch):
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from tests.integration.test_sourced_roster_jobs import response as roster_response

    from ndr.domain.enums import JobState
    from ndr.jobs.roster import run_character_roster_job
    from ndr.storage.models import InferenceRun, Job
    from ndr.storage.run_archive import decode_archive

    client = migrated_client
    book = client.post("/api/books/import", files={
        "file": ("authored.txt", '第一章\n林舟说：「早上好。」'.encode(), "text/plain"),
    }).json()["data"]
    chapter = client.get(f"/api/books/{book['book_id']}/chapters").json()["data"][0]
    profile = client.post("/api/model-profiles", json={
        "name": "isolated", "protocol": "anthropic-messages", "model": "fixture",
        "base_url": "https://example.com/v1", "credential_mode": "none",
    }).json()["data"]
    path = f"/api/books/{book['book_id']}/chapters/{chapter['id']}/character-roster/analyze"
    created = client.post(path, json={"profile_id": profile["id"],
                                     "idempotency_key": "anthropic-recovery", "run_now": False})
    assert created.status_code == 202, created.text
    job_id = created.json()["data"]["id"]
    commit, failed, calls = Session.commit, [], []

    def fail_once(session):
        if not failed and any(isinstance(row, Job) and row.state == JobState.COMPLETED
                              for row in session.dirty):
            failed.append(True)
            raise RuntimeError("isolated finalization failure")
        return commit(session)

    def handler(request):
        calls.append(request)
        proposal = roster_response()
        proposal.pop("_usage")
        return httpx.Response(200, json=body(json.dumps(proposal)))

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = AnthropicMessagesAdapter(base_url="https://example.com/v1", model="fixture",
                                       client=transport)
    monkeypatch.setattr(Session, "commit", fail_once)
    factory, settings = client.app.state.session_factory, client.app.state.settings
    try:
        outcome = run_character_roster_job(factory, settings, job_id=job_id,
                                          adapter_factory=lambda *_: adapter)
        assert failed and outcome.state == JobState.FAILED and len(calls) == 1
        with factory() as session:
            run = session.scalar(select(InferenceRun).where(InferenceRun.job_id == job_id))
            archive = decode_archive(run.call_archive)
            assert archive["provider_receipt"]["request"]["system"]
            assert archive["provider_receipt"]["response"]["type"] == "message"
            assert json.loads(run.usage_json)["total_tokens"] == 30

        def no_dispatch(*_):
            pytest.fail("returned Anthropic roster must recover locally")
        monkeypatch.setattr("ndr.jobs.roster._build_adapter", no_dispatch)
        assert client.post(f"/api/jobs/{job_id}/resume").status_code == 202
        assert client.get(f"/api/jobs/{job_id}").json()["data"]["state"] == "COMPLETED"
        assert len(calls) == 1
    finally:
        asyncio.run(transport.aclose())
