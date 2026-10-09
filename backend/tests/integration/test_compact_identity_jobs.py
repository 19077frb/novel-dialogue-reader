"""Frozen new/old prompt transport through real scheduler, with fake provider."""

import json

from integration.test_expression_pipeline import StageAdapter, prepare
from ndr.characters.prompt_catalog import IDENTITY_PROMPT_VERSION
from ndr.domain.enums import JobState
from ndr.jobs import scheduler
from ndr.storage.models import Job
from ndr.storage.transactions import transaction


def test_new_job_and_independent_review_share_compact_transport(fake_provider_client):
    client = fake_provider_client
    create, factory = prepare(client)
    job_id = create("compact-reviews")["id"]
    with factory() as session:
        assert json.loads(session.get(Job, job_id).range_json)["identity_prompt_version"] == IDENTITY_PROMPT_VERSION

    class Observing(StageAdapter):
        async def generate_labels(self, payload):
            data = json.loads(payload["messages"][1]["content"])
            assert len(data["effective_identity_profiles"]) == len(data["candidates"])
            assert all("canonical_name" not in p and "description" not in p
                       for p in data["effective_identity_profiles"])
            return await super().generate_labels(payload)

    adapter = Observing(["林舟"])
    outcome = scheduler.run_job(factory, client.app.state.settings, job_id=job_id,
                                 adapter_factory=lambda *_: adapter)
    assert outcome.state is JobState.COMPLETED, outcome.errors
    assert len(adapter.calls) == 2


def test_preexisting_full_profile_job_recovery_keeps_exact_old_request(fake_provider_client, monkeypatch):
    client = fake_provider_client
    create, factory = prepare(client)
    job_id = create("historical-transport")["id"]
    with transaction(factory) as session:
        job = session.get(Job, job_id)
        scope = json.loads(job.range_json)
        scope.pop("identity_prompt_version")
        job.range_json = json.dumps(scope)

    class Observing(StageAdapter):
        async def generate_labels(self, payload):
            data = json.loads(payload["messages"][1]["content"])
            assert all("canonical_name" in p for p in data["effective_identity_profiles"])
            return await super().generate_labels(payload)

    adapter = Observing(["林舟"])
    original = scheduler._apply_payload
    def fail(*args, **kwargs):
        raise RuntimeError("local finalization")
    monkeypatch.setattr(scheduler, "_apply_payload", fail)
    outcome = scheduler.run_job(factory, client.app.state.settings, job_id=job_id,
                                 adapter_factory=lambda *_: adapter)
    assert outcome.state is JobState.FAILED
    assert len(adapter.calls) == 2
    monkeypatch.setattr(scheduler, "_apply_payload", original)
    with transaction(factory) as session:
        session.get(Job, job_id).state = JobState.QUEUED
    outcome = scheduler.run_job(factory, client.app.state.settings, job_id=job_id,
                                 adapter_factory=lambda *_: adapter)
    assert outcome.state is JobState.COMPLETED, outcome.errors
    assert len(adapter.calls) == 2
