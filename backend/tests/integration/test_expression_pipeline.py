"""Actual opt-in Jobs, authored text, isolated database; no remote calls."""

import json
from copy import deepcopy

import pytest
from sqlalchemy import select

from ndr.domain.enums import (
    AnnotationSource,
    AnnotationStatus,
    InferenceRunState,
    JobState,
    QuoteKind,
)
from ndr.jobs.scheduler import reconcile_job, run_job
from ndr.llm.adapters.fake import FakeProviderAdapter
from ndr.llm.errors import ProviderError, ProviderErrorKind
from ndr.llm.expression_review import REVIEW_VERSION
from ndr.storage.models import (
    Annotation,
    BookCharacter,
    BookVersion,
    InferenceRun,
    Job,
    Quote,
    ResultCache,
    SpeakerGroup,
)
from ndr.storage.transactions import transaction


def prepare(client, *, all_quotes=False, auxiliary=False, identity_feedback=False):
    text = "第一章\n林舟说：「早上好。」\n许晴答：「你好。」\n顾宁点头。"
    data = client.post(
        "/api/books/import", files={"file": ("stages.txt", text.encode(), "text/plain")}
    ).json()["data"]
    profile = client.post(
        "/api/model-profiles",
        json={
            "name": "stages",
            "protocol": "fake-provider",
            "base_url": "http://127.0.0.1:1",
            "model": "fake",
            "credential_mode": "none",
        },
    ).json()["data"]
    factory = client.app.state.session_factory
    with transaction(factory) as session:
        version = session.get(BookVersion, data["book_version_id"])
        for name in ("林舟", "许晴", "顾宁"):
            session.add(
                BookCharacter(
                    book_version_id=version.id,
                    canonical_name=name,
                    source="USER",
                    user_confirmed=True,
                )
            )
        cutoff = session.scalar(select(Quote).order_by(Quote.start_cp)).end_cp
        if all_quotes:
            cutoff = session.scalar(select(Quote).order_by(Quote.start_cp.desc())).end_cp

    def create(key):
        response = client.post(
            "/api/jobs",
            json={
                "book_id": data["book_id"],
                "profile_id": profile["id"],
                "mode": "process",
                "range": {
                    "start_cp": 0,
                    "end_cp": cutoff,
                    "output_protocol": "expression-production-1",
                    "review_protocol": REVIEW_VERSION,
                    **(
                        {"identity_feedback_protocol": "identity-feedback-1"}
                        if identity_feedback
                        else {}
                    ),
                    **(
                        {"auxiliary_protocol": "expression-auxiliary-isolation-1"}
                        if auxiliary
                        else {}
                    ),
                },
                "budget": {"max_recheck_rounds": 1, "max_format_retries": 0},
                "reading_mode": "reread",
                "idempotency_key": key,
                "run_now": False,
            },
        )
        assert response.status_code == 202, response.text
        return response.json()["data"]

    return create, factory


class StageAdapter(FakeProviderAdapter):
    def __init__(self, owners, *, basis="direct", pause=None, kind="speech"):
        super().__init__()
        self.owners, self.basis, self.pause = owners, basis, pause
        self.kind = kind

    async def generate_labels(self, payload):
        self.calls.append({"payload": deepcopy(payload)})
        data = json.loads(payload["messages"][1]["content"])
        if payload.get("review_stage", "").startswith("review:"):
            assert "first" not in data and "旧候选" not in json.dumps(
                payload["messages"], ensure_ascii=False
            )
        owner = self.owners[min(len(self.calls) - 1, len(self.owners) - 1)]
        ref = next(p["id"] for p in data["candidates"] if p["name"] == owner)
        evidence = next(
            r["ref"]
            for r in data["context"]
            if r["text"].strip()
            and r["ref"] not in data["targets"]
            and r["ref"] not in data["gap_next_quote"]
        )
        result = {
            "labels": [
                {
                    "q": q,
                    "kind": self.kind,
                    "character": ref,
                    "basis": self.basis,
                    "evidence": [evidence],
                }
                for q in data["targets"]
            ],
            "breaks": [],
            "_usage": {
                "input_tokens": 10,
                "output_tokens": 20,
                "total_tokens": 30,
                "unknown": False,
            },
        }
        if self.pause and len(self.calls) == self.pause[0]:
            self.pause[1]()
        return result


@pytest.mark.parametrize(
    "owners,expected,calls",
    [
        (["林舟", "林舟"], "林舟", 2),
        (["林舟", "许晴", "顾宁"], "顾宁", 3),
    ],
)
def test_actual_job_independent_review_and_evidence_based_third_identity(
    fake_provider_client, owners, expected, calls
):
    client = fake_provider_client
    create, factory = prepare(client)
    job = create("formal-chain")
    adapter = StageAdapter(owners)
    result = run_job(
        factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: adapter
    )
    assert result.state is JobState.COMPLETED, result.errors
    assert len(adapter.calls) == calls
    with factory() as session:
        annotation = session.scalar(select(Annotation))
        assert session.get(SpeakerGroup, annotation.speaker_id).canonical_name == expected
        runs = list(session.scalars(select(InferenceRun).where(InferenceRun.job_id == job["id"])))
        assert len(runs) == calls
        assert all(r.state is InferenceRunState.SUCCEEDED for r in runs)
        assert sum(json.loads(r.usage_json)["total_tokens"] for r in runs) == 30 * calls


def test_review_and_cached_replay_keep_independent_owner_and_auxiliary_notice(fake_provider_client):
    client = fake_provider_client
    create, factory = prepare(client, auxiliary=True)

    class AuxiliaryStageAdapter(StageAdapter):
        async def generate_labels(self, payload):
            raw = await super().generate_labels(payload)
            raw["labels"][0].update(addressee="C999", addressee_evidence=["not-sent"])
            return raw

    adapter = AuxiliaryStageAdapter(["林舟", "林舟"])
    for key in ("auxiliary-first", "auxiliary-cache"):
        job = create(key)
        outcome = run_job(
            factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: adapter
        )
        assert outcome.state is JobState.COMPLETED, client.get(f"/api/jobs/{job['id']}").json()[
            "data"
        ]["last_error"]
        with factory() as session:
            annotation = session.scalar(select(Annotation))
            assert session.get(SpeakerGroup, annotation.speaker_id).canonical_name == "林舟"
            assert "已隔离" in session.get(Job, job["id"]).checkpoint_json
    assert len(adapter.calls) == 2


def test_cache_keeps_pending_owner_ceiling_without_new_review_calls(fake_provider_client):
    client = fake_provider_client
    create, factory = prepare(client)
    adapter = StageAdapter(["林舟", "林舟"], basis="style_only")
    for key in ("pending-first", "pending-replay"):
        job = create(key)
        outcome = run_job(
            factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: adapter
        )
        assert outcome.state is JobState.COMPLETED, outcome.errors
        with factory() as session:
            assert session.scalar(select(Annotation)).status is AnnotationStatus.PROVISIONAL
    assert len(adapter.calls) == 2


def test_pause_after_received_review_resumes_without_repeating_either_call(fake_provider_client):
    client = fake_provider_client
    create, factory = prepare(client)
    job = create("pause-chain")

    def pause():
        with transaction(factory) as session:
            session.get(Job, job["id"]).state = JobState.PAUSING

    adapter = StageAdapter(["林舟", "林舟"], pause=(2, pause))
    first = run_job(
        factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: adapter
    )
    assert first.state is JobState.PAUSED
    with transaction(factory) as session:
        assert session.scalar(select(Annotation)) is None
        session.get(Job, job["id"]).state = JobState.QUEUED
    second = run_job(
        factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: adapter
    )
    assert second.state is JobState.COMPLETED, second.errors
    assert len(adapter.calls) == 2


@pytest.mark.parametrize("kind", ["speech", "thought", "quotation"])
def test_runtime_review_keeps_expression_kind_and_ownership(fake_provider_client, kind):
    client = fake_provider_client
    create, factory = prepare(client)
    job = create("owner-kind")
    adapter = StageAdapter(["林舟", "林舟"], kind=kind)
    outcome = run_job(
        factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: adapter
    )
    assert outcome.state is JobState.COMPLETED
    with factory() as session:
        annotation = session.scalar(select(Annotation))
        assert annotation.kind is QuoteKind(kind)
        assert session.get(SpeakerGroup, annotation.speaker_id).canonical_name == "林舟"


@pytest.mark.parametrize("failure_stage", [2, 3])
def test_failed_extra_stage_is_charged_and_preserves_first_candidate(
    fake_provider_client, failure_stage
):
    client = fake_provider_client
    create, factory = prepare(client)
    job = create("failed-extra")

    class BadStage(StageAdapter):
        async def generate_labels(self, payload):
            response = await super().generate_labels(payload)
            if len(self.calls) == failure_stage:
                response["labels"] = []
            return response

    adapter = BadStage(["林舟", "许晴", "顾宁"])
    result = run_job(
        factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: adapter
    )
    assert result.state is JobState.COMPLETED, result.errors
    assert len(adapter.calls) == failure_stage
    with factory() as session:
        annotation = session.scalar(select(Annotation))
        assert session.get(SpeakerGroup, annotation.speaker_id).canonical_name == "林舟"
        if failure_stage == 3:
            assert annotation.status is AnnotationStatus.PROVISIONAL
        runs = list(session.scalars(select(InferenceRun).where(InferenceRun.job_id == job["id"])))
        assert sum(json.loads(r.usage_json)["total_tokens"] for r in runs) == failure_stage * 30
        assert any(r.state is InferenceRunState.FAILED for r in runs)


def test_manual_owner_locked_during_review_is_not_overwritten(fake_provider_client):
    client = fake_provider_client
    create, factory = prepare(client)
    job = create("manual-inflight")
    # Establish a prior candidate using the same range, without the staged protocol.
    with transaction(factory) as session:
        row = session.get(Job, job["id"])
        data = json.loads(row.range_json)
        data.pop("review_protocol")
        row.range_json = json.dumps(data)
        row.budget_json = json.dumps({"max_recheck_rounds": 0})
    first = StageAdapter(["林舟"])
    run_job(factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: first)
    second_job = create("manual-review")

    def lock():
        with transaction(factory) as session:
            annotation = session.scalar(select(Annotation))
            annotation.user_locked = True
            annotation.source = AnnotationSource.USER

    adapter = StageAdapter(["许晴", "许晴"], pause=(2, lock))
    result = run_job(
        factory,
        client.app.state.settings,
        job_id=second_job["id"],
        adapter_factory=lambda *_: adapter,
    )
    assert result.state is JobState.COMPLETED
    with factory() as session:
        annotation = session.scalar(select(Annotation))
        assert annotation.source is AnnotationSource.USER and annotation.user_locked
        assert session.get(SpeakerGroup, annotation.speaker_id).canonical_name == "林舟"


@pytest.mark.parametrize("kind", ["speech", "thought", "quotation"])
@pytest.mark.parametrize("unknown", [False, True])
def test_actual_manual_expression_correction_during_review_preserves_entire_answer(
    fake_provider_client,
    kind,
    unknown,
):
    from ndr.corrections.history import annotation_snapshot

    client = fake_provider_client
    create, factory = prepare(client)
    prior_job = create("manual-expression-prior")
    with transaction(factory) as session:
        row = session.get(Job, prior_job["id"])
        data = json.loads(row.range_json)
        data.pop("review_protocol")
        row.range_json = json.dumps(data)
        row.budget_json = json.dumps({"max_recheck_rounds": 0})
    run_job(
        factory,
        client.app.state.settings,
        job_id=prior_job["id"],
        adapter_factory=lambda *_: StageAdapter(["林舟"]),
    )
    job = create("manual-expression-review")
    saved = {}

    def correct():
        with factory() as session:
            target = session.scalar(select(Annotation))
            quote_id, version = target.quote_id, target.version
        response = client.post(
            f"/api/quotes/{quote_id}/corrections",
            json={
                "action": "set_kind",
                "kind": kind,
                "expected_version": version,
            },
        )
        assert response.status_code == 201, response.text
        if unknown:
            response = client.post(
                f"/api/quotes/{quote_id}/corrections", json={"action": "mark_unknown"}
            )
            assert response.status_code == 201, response.text
        with factory() as session:
            saved.update(annotation_snapshot(session.scalar(select(Annotation))))

    adapter = StageAdapter(["许晴", "许晴"], pause=(2, correct), kind="quotation")
    result = run_job(
        factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: adapter
    )
    assert result.state is JobState.COMPLETED
    with factory() as session:
        annotation = session.scalar(select(Annotation))
        assert annotation_snapshot(annotation) == saved
        assert annotation.kind.value == kind and annotation.user_locked
        assert annotation.source is AnnotationSource.USER
        if unknown:
            assert annotation.speaker_id is None and annotation.status is AnnotationStatus.UNKNOWN
        else:
            assert session.get(SpeakerGroup, annotation.speaker_id).canonical_name == "林舟"


def test_review_timeout_stops_without_applying_or_automatically_repeating(fake_provider_client):
    client = fake_provider_client
    create, factory = prepare(client)
    job = create("stage-timeout")

    class TimeoutStage(StageAdapter):
        async def generate_labels(self, payload):
            result = await super().generate_labels(payload)
            if payload.get("review_stage"):
                raise ProviderError(ProviderErrorKind.TIMEOUT, "isolated timeout")
            return result

    adapter = TimeoutStage(["林舟", "林舟"])
    for _ in range(2):
        result = run_job(
            factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: adapter
        )
        assert result.state is JobState.NEEDS_RECONCILIATION
    assert len(adapter.calls) == 2
    with factory() as session:
        assert session.scalar(select(Annotation)) is None
        runs = list(session.scalars(select(InferenceRun).where(InferenceRun.job_id == job["id"])))
        assert len(runs) == 2
        assert sum(r.state is InferenceRunState.UNKNOWN_OUTCOME for r in runs) == 1
        assert sum(r.usage_json is None for r in runs) == 1


def test_changed_identity_after_pause_refuses_restore_without_new_call(fake_provider_client):
    client = fake_provider_client
    create, factory = prepare(client)
    job = create("stale-stage")

    def pause():
        with transaction(factory) as session:
            session.get(Job, job["id"]).state = JobState.PAUSING

    adapter = StageAdapter(["林舟", "林舟"], pause=(2, pause))
    run_job(
        factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: adapter
    )
    with transaction(factory) as session:
        person = session.scalar(select(BookCharacter).where(BookCharacter.canonical_name == "林舟"))
        person.canonical_name = "更名后"
        session.get(Job, job["id"]).state = JobState.QUEUED
    result = run_job(
        factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: adapter
    )
    assert result.state is JobState.FAILED
    assert len(adapter.calls) == 2
    with factory() as session:
        assert session.scalar(select(Annotation)) is None


def test_format_failure_and_corrected_response_are_both_reused_after_pause(fake_provider_client):
    client = fake_provider_client
    create, factory = prepare(client)
    job = create("format-stage-resume")
    with transaction(factory) as session:
        session.get(Job, job["id"]).budget_json = json.dumps(
            {"max_recheck_rounds": 1, "max_format_retries": 1}
        )

    def pause():
        with transaction(factory) as session:
            session.get(Job, job["id"]).state = JobState.PAUSING

    class RetryStage(StageAdapter):
        async def generate_labels(self, payload):
            result = await super().generate_labels(payload)
            if len(self.calls) == 2:
                raise ProviderError(
                    ProviderErrorKind.INVALID_OUTPUT,
                    "specific original schema fault",
                    details={"usage": result["_usage"]},
                )
            return result

    adapter = RetryStage(["林舟", "林舟", "林舟"], pause=(3, pause))
    result = run_job(
        factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: adapter
    )
    assert result.state is JobState.PAUSED
    with transaction(factory) as session:
        session.get(Job, job["id"]).state = JobState.QUEUED
    result = run_job(
        factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: adapter
    )
    assert result.state is JobState.COMPLETED, result.errors
    assert len(adapter.calls) == 3
    assert (
        "specific original schema fault" in adapter.calls[2]["payload"]["messages"][-1]["content"]
    )
    with factory() as session:
        attempts = list(
            session.scalars(select(InferenceRun).where(InferenceRun.job_id == job["id"]))
        )
        assert len(attempts) == 3
        assert sum(json.loads(a.usage_json)["total_tokens"] for a in attempts) == 90


@pytest.mark.parametrize("verdict", ["support_challenger", "undecidable"])
def test_agreement_challenge_requires_separate_actual_verification(fake_provider_client, verdict):
    client = fake_provider_client
    create, factory = prepare(client, all_quotes=True)
    job = create("agreement-verifier")

    class ChallengeAdapter(StageAdapter):
        async def generate_labels(self, payload):
            if payload.get("review_stage", "").startswith("verification:"):
                assert payload["json_object"] is True
                assert "json" in payload["messages"][0]["content"].lower()
                self.calls.append({"payload": deepcopy(payload)})
                data = json.loads(payload["messages"][1]["content"])
                request = json.loads(payload["messages"][-1]["content"])
                evidence = next(
                    row["ref"]
                    for row in data["context"]
                    if row["text"].strip()
                    and row.get("kind") not in {"inner_gap", "outer_gap"}
                    and row["ref"] != request["targets"][0]
                )
                return {
                    "checks": [
                        {
                            "q": q,
                            "verdict": verdict,
                            "evidence": [evidence],
                            "contradiction_evidence": [evidence],
                            "reason": "独立原创核验",
                        }
                        for q in request["targets"]
                    ],
                    "_usage": {
                        "input_tokens": 10,
                        "output_tokens": 20,
                        "total_tokens": 30,
                        "unknown": False,
                    },
                }
            result = await super().generate_labels(payload)
            if payload.get("review_stage", "").startswith("review:"):
                data = json.loads(payload["messages"][1]["content"])
                second = next(c["id"] for c in data["candidates"] if c["name"] == "许晴")
                result["labels"][1]["character"] = second
            return result

    adapter = ChallengeAdapter(["林舟", "林舟", "顾宁"])
    outcome = run_job(
        factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: adapter
    )
    assert outcome.state is JobState.COMPLETED, outcome.errors
    assert len(adapter.calls) == 4
    with factory() as session:
        rows = session.execute(
            select(Annotation, Quote)
            .join(Quote, Quote.id == Annotation.quote_id)
            .order_by(Quote.start_cp)
        ).all()
        assert len(rows) == 2
        if verdict == "support_challenger":
            assert session.get(SpeakerGroup, rows[0][0].speaker_id).canonical_name == "顾宁"
        else:
            assert rows[0][0].status is AnnotationStatus.PROVISIONAL
        assert session.get(SpeakerGroup, rows[1][0].speaker_id).canonical_name == "顾宁"


def test_corrupt_review_cache_cannot_upgrade_pending_or_trigger_new_calls(fake_provider_client):
    client = fake_provider_client
    create, factory = prepare(client)
    adapter = StageAdapter(["林舟", "林舟"], basis="style_only")
    job = create("corrupt-cache-first")
    run_job(
        factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: adapter
    )
    with transaction(factory) as session:
        cache = session.scalar(
            select(ResultCache).where(ResultCache.schema_version == "expr-review-1")
        )
        payload = json.loads(cache.result_json)
        payload["owner_approvals"] = {}
        cache.result_json = json.dumps(payload)
    replay = create("corrupt-cache-replay")
    result = run_job(
        factory, client.app.state.settings, job_id=replay["id"], adapter_factory=lambda *_: adapter
    )
    assert result.state is JobState.FAILED
    assert len(adapter.calls) == 2
    with factory() as session:
        assert session.scalar(select(Annotation)).status is AnnotationStatus.PROVISIONAL


def test_explicit_retry_repeats_only_unknown_stage_not_received_primary(fake_provider_client):
    client = fake_provider_client
    create, factory = prepare(client)
    job = create("explicit-stage-retry")

    class OnceTimeout(StageAdapter):
        async def generate_labels(self, payload):
            result = await super().generate_labels(payload)
            if len(self.calls) == 2:
                raise ProviderError(ProviderErrorKind.TIMEOUT, "once timeout")
            return result

    adapter = OnceTimeout(["林舟", "林舟", "林舟"])
    result = run_job(
        factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: adapter
    )
    assert result.state is JobState.NEEDS_RECONCILIATION
    with transaction(factory) as session:
        action = reconcile_job(session, session.get(Job, job["id"]), action="retry")
        assert len(action["affected_windows"]) == 1
    result = run_job(
        factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: adapter
    )
    assert result.state is JobState.COMPLETED, result.errors
    assert len(adapter.calls) == 3
    assert adapter.calls[2]["payload"]["review_generation"] == 1
    with factory() as session:
        attempts = list(
            session.scalars(select(InferenceRun).where(InferenceRun.job_id == job["id"]))
        )
        assert len(attempts) == 3 and sum(a.usage_json is None for a in attempts) == 1
