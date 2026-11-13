"""Network fault safety with authored fixtures and in-process MockTransport only."""

import httpx
import pytest
from sqlalchemy import select

from integration.test_expression_pipeline import prepare
from ndr.domain.enums import InferenceRunState, JobState
from ndr.jobs import scheduler
from ndr.jobs.service import _spent_tokens
from ndr.llm.adapters.anthropic_messages import AnthropicMessagesAdapter
from ndr.llm.adapters.chat_completions import ChatCompletionsAdapter
from ndr.llm.adapters.fake import FakeProviderAdapter
from ndr.llm.errors import ProviderError, ProviderErrorKind
from ndr.storage.models import Annotation, InferenceRun, Job, JobWindow
from ndr.storage.transactions import transaction


def _run(client, job, adapter):
    return scheduler.run_job(
        client.app.state.session_factory, client.app.state.settings,
        job_id=job["id"], adapter_factory=lambda *_: adapter,
    )


@pytest.mark.parametrize("adapter_class", [ChatCompletionsAdapter, AnthropicMessagesAdapter])
@pytest.mark.parametrize("fault", [httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError])
def test_network_interruption_is_one_unknown_attempt_and_never_automatically_resent(
    fake_provider_client, adapter_class, fault,
):
    client = fake_provider_client
    create, factory = prepare(client)
    job = create("interrupted-request")
    calls = []

    def handler(request):
        calls.append(request)
        raise fault("private network detail", request=request)

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = adapter_class(base_url="https://fixture.invalid", model="fixture", client=transport)
    for _ in range(2):
        assert _run(client, job, adapter).state is JobState.NEEDS_RECONCILIATION
    assert len(calls) == 1
    with factory() as session:
        runs = list(session.scalars(select(InferenceRun).where(InferenceRun.job_id == job["id"])))
        assert len(runs) == 1 and runs[0].state is InferenceRunState.UNKNOWN_OUTCOME
        assert runs[0].usage_json is None
        assert _spent_tokens(runs)["unknown_runs"] == 1
        assert session.scalar(select(Annotation)) is None
        assert session.scalar(select(JobWindow).where(
            JobWindow.job_id == job["id"],
        )).state is JobState.NEEDS_RECONCILIATION
        assert "中断" in session.get(Job, job["id"]).last_error


@pytest.mark.parametrize("retry_limit", [0, 2])
def test_definite_connection_failure_retains_bounded_retries_without_fabricating_usage(
    fake_provider_client, retry_limit,
):
    client = fake_provider_client
    client.app.state.settings.rate_limit_max_retries = retry_limit
    client.app.state.settings.rate_limit_backoff_base_seconds = 0
    create, factory = prepare(client)
    job = create("unsent-request")
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("isolated unsent request", request=request)

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = ChatCompletionsAdapter(
        base_url="https://fixture.invalid", model="fixture", client=transport,
    )
    assert _run(client, job, adapter).state is JobState.FAILED
    assert len(calls) == retry_limit + 1
    with factory() as session:
        runs = list(session.scalars(select(InferenceRun).where(InferenceRun.job_id == job["id"])))
        assert len(runs) == retry_limit + 1
        assert all(run.state is InferenceRunState.FAILED for run in runs)
        assert all(run.error_code == "PROVIDER_CONNECTION_FAILED" for run in runs)
        assert all(run.usage_json is None for run in runs)
        assert _spent_tokens(runs)["unknown_runs"] == 0


def test_server_failure_is_not_retried_and_error_receipt_can_restore_without_network(
    fake_provider_client, monkeypatch,
):
    client = fake_provider_client
    client.app.state.settings.rate_limit_max_retries = 3
    client.app.state.settings.rate_limit_backoff_base_seconds = 0
    create, factory = prepare(client)
    job = create("server-error-receipt")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, json={"error": "fixture unavailable"})

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = ChatCompletionsAdapter(
        base_url="https://fixture.invalid", model="fixture", client=transport,
    )
    settle = scheduler._usage_json_from_error

    def interrupted_settle(*args):
        raise RuntimeError("isolated local finalization failure")

    monkeypatch.setattr(scheduler, "_usage_json_from_error", interrupted_settle)
    assert _run(client, job, adapter).state is JobState.FAILED
    assert len(calls) == 1
    monkeypatch.setattr(scheduler, "_usage_json_from_error", settle)
    with transaction(factory) as session:
        session.get(Job, job["id"]).state = JobState.QUEUED
    assert _run(client, job, adapter).state is JobState.FAILED
    assert len(calls) == 1
    with factory() as session:
        runs = list(session.scalars(select(InferenceRun).where(InferenceRun.job_id == job["id"])))
        assert len(runs) == 1 and runs[0].error_code == "PROVIDER_UNAVAILABLE"
        assert runs[0].usage_json is None
        assert _spent_tokens(runs)["unknown_runs"] == 1


@pytest.mark.parametrize("kind", [ProviderErrorKind.TIMEOUT, ProviderErrorKind.UNKNOWN_OUTCOME])
def test_legacy_optional_review_unknown_keeps_primary_and_stops_without_resending(
    fake_provider_client, kind,
):
    from integration.test_recheck_routing_jobs import _create_job, _import, _profile

    client = fake_provider_client
    imported = _import(client)
    job = _create_job(
        client, book_id=imported["book_id"], profile_id=_profile(client),
        key="legacy-unknown-review", max_recheck_rounds=1,
    )

    class InterruptedReview(FakeProviderAdapter):
        async def generate_labels(self, payload):
            result = await super().generate_labels(payload)
            if len(self.calls) > 1:
                raise ProviderError(kind, "复核请求或响应中断，结果与用量未知")
            return result

    adapter = InterruptedReview(usage={"total_tokens": 30})
    assert _run(client, job, adapter).state is JobState.NEEDS_RECONCILIATION
    assert _run(client, job, adapter).state is JobState.NEEDS_RECONCILIATION
    assert len(adapter.calls) == 2
    with client.app.state.session_factory() as session:
        assert len(list(session.scalars(select(Annotation)))) == 2
        runs = list(session.scalars(select(InferenceRun).where(InferenceRun.job_id == job["id"])))
        assert len(runs) == 2
        assert sum(run.state is InferenceRunState.UNKNOWN_OUTCOME for run in runs) == 1
        assert _spent_tokens(runs)["unknown_runs"] == 1
        assert "请求结果未知" in session.get(Job, job["id"]).last_error
