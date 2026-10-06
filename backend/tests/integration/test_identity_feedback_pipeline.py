"""Identity feedback on actual Jobs with isolated authored fixtures, no remote API."""

import json
from copy import deepcopy

import pytest
from sqlalchemy import select
from test_expression_pipeline import StageAdapter, prepare

from ndr.domain.enums import AnnotationSource, AnnotationStatus, JobState, QuoteKind
from ndr.jobs.scheduler import run_job
from ndr.llm.errors import ProviderError, ProviderErrorKind
from ndr.storage.models import Annotation, InferenceRun, Job, ModelProfile, Quote, SpeakerGroup
from ndr.storage.transactions import transaction


class FeedbackAdapter(StageAdapter):
    def __init__(self, *, verdict="support_challenger", empty=False, timeout=False, pause=None):
        super().__init__(["许晴", "林舟", "许晴", "林舟"], pause=pause)
        self.verdict, self.empty, self.timeout = verdict, empty, timeout

    async def generate_labels(self, payload):
        stage = payload.get("review_stage", "")
        if stage.startswith("verification:"):
            self.calls.append({"payload": deepcopy(payload)})
            wanted = json.loads(payload["messages"][-1]["content"])["targets"]
            context = json.loads(payload["messages"][1]["content"])["context"]
            ref = next(r["ref"] for r in context if r["text"].strip() and r["ref"] not in wanted)
            return {
                "checks": [
                    {
                        "q": q,
                        "verdict": self.verdict,
                        "evidence": [ref],
                        "contradiction_evidence": [ref],
                        "reason": "原创外部归属依据",
                    }
                    for q in wanted
                ],
                "_usage": {
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "total_tokens": 30,
                    "unknown": False,
                },
            }
        raw = await super().generate_labels(payload)
        if stage == "identity_feedback:1":
            if self.timeout:
                raise ProviderError(ProviderErrorKind.TIMEOUT, "feedback timeout")
            original = json.loads(payload["messages"][-1]["content"])["primary_proposal"]
            clean = {k: v for k, v in raw.items() if not k.startswith("_")}
            issue = {
                "kind": "incorrect_association",
                "targets": [r["q"] for r in clean["labels"]],
                "character": clean["labels"][0]["character"],
                "evidence": clean["labels"][0]["evidence"],
                "reason": "原文人物关联冲突",
            }
            return {
                "schema_version": "identity-feedback-1",
                "issues": [] if self.empty else [issue],
                "proposal": original if self.empty else clean,
                "_usage": raw["_usage"],
            }
        if stage.startswith("review:"):
            assert "identity_feedback" not in json.dumps(payload["messages"])
        if stage.startswith("adjudication:"):
            critique = json.loads(payload["messages"][-1]["content"].split("\n", 1)[1])[
                "identity_feedback"
            ]
            assert critique["issues"] and critique["instruction"]
        return raw


@pytest.mark.parametrize(
    "verdict,expected",
    [
        ("support_challenger", AnnotationStatus.ACCEPTED),
        ("undecidable", AnnotationStatus.PROVISIONAL),
    ],
)
def test_feedback_does_not_bypass_independent_agreement_verification(
    fake_provider_client, verdict, expected
):
    client = fake_provider_client
    create, factory = prepare(client, identity_feedback=True)
    job = create("feedback-verdict")
    adapter = FeedbackAdapter(verdict=verdict)
    result = run_job(
        factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: adapter
    )
    assert result.state is JobState.COMPLETED, result.errors
    assert len(adapter.calls) == 5
    with factory() as session:
        annotation = session.scalar(select(Annotation))
        assert annotation.status is expected
        name = session.get(SpeakerGroup, annotation.speaker_id).canonical_name
        assert name == ("林舟" if verdict == "support_challenger" else "许晴")
        checkpoint = json.loads(session.get(Job, job["id"]).checkpoint_json)
        assert next(iter(checkpoint["expression_reviews"].values()))["identity_feedback"][
            "source_ref"
        ]
        runs = list(session.scalars(select(InferenceRun).where(InferenceRun.job_id == job["id"])))
        assert len(runs) == 5 and sum(json.loads(r.usage_json)["total_tokens"] for r in runs) == 150


def test_received_feedback_reused_after_pause_without_new_dispatch(fake_provider_client):
    client = fake_provider_client
    create, factory = prepare(client, identity_feedback=True)
    job = create("feedback-resume")

    def pause():
        with transaction(factory) as session:
            session.get(Job, job["id"]).state = JobState.PAUSING

    adapter = FeedbackAdapter(pause=(2, pause))
    assert (
        run_job(
            factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: adapter
        ).state
        is JobState.PAUSED
    )
    with transaction(factory) as session:
        session.get(Job, job["id"]).state = JobState.QUEUED
    result = run_job(
        factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: adapter
    )
    assert result.state is JobState.COMPLETED, result.errors
    assert len(adapter.calls) == 5


def test_feedback_timeout_stops_and_is_not_automatically_repeated(fake_provider_client):
    client = fake_provider_client
    create, factory = prepare(client, identity_feedback=True)
    job = create("feedback-timeout")
    adapter = FeedbackAdapter(timeout=True)
    for _ in range(2):
        result = run_job(
            factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: adapter
        )
        assert result.state is JobState.NEEDS_RECONCILIATION
    assert len(adapter.calls) == 2
    with factory() as session:
        assert session.scalar(select(Annotation)) is None


def test_empty_feedback_adds_only_one_stage_and_cached_result_does_not_repeat_it(
    fake_provider_client,
):
    client = fake_provider_client
    create, factory = prepare(client, identity_feedback=True)
    adapter = FeedbackAdapter(empty=True)
    for key in ("empty-feedback", "empty-feedback-cache"):
        job = create(key)
        result = run_job(
            factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: adapter
        )
        assert result.state is JobState.COMPLETED, result.errors
    assert len(adapter.calls) == 3


def test_valid_feedback_remains_pending_if_independent_review_fails(fake_provider_client):
    client = fake_provider_client
    create, factory = prepare(client, identity_feedback=True)
    job = create("feedback-review-failed")

    class FailedReview(FeedbackAdapter):
        async def generate_labels(self, payload):
            raw = await super().generate_labels(payload)
            if payload.get("review_stage", "").startswith("review:"):
                raise ProviderError(
                    ProviderErrorKind.INVALID_OUTPUT,
                    "authored failed review",
                    details={"usage": raw["_usage"]},
                )
            return raw

    adapter = FailedReview()
    result = run_job(
        factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: adapter
    )
    assert result.state is JobState.COMPLETED
    assert len(adapter.calls) == 3
    with factory() as session:
        annotation = session.scalar(select(Annotation))
        assert annotation.status is AnnotationStatus.PROVISIONAL
        assert session.get(SpeakerGroup, annotation.speaker_id).canonical_name == "许晴"


@pytest.mark.parametrize(
    "version,rounds,review",
    [
        ("bad-version", 1, "expression-evidence-review-1"),
        ("identity-feedback-1", 0, "expression-evidence-review-1"),
        ("identity-feedback-1", 1, None),
    ],
)
def test_feedback_creation_and_estimate_require_supported_independent_review(
    fake_provider_client, version, rounds, review
):
    client = fake_provider_client
    create, factory = prepare(client, identity_feedback=True)
    existing = create("feedback-invalid-baseline")
    with factory() as session:
        row = session.get(Job, existing["id"])
        scope = json.loads(row.range_json)
        scope.update(identity_feedback_protocol=version, review_protocol=review)
        request = {
            "book_id": row.book_id,
            "profile_id": session.scalar(select(ModelProfile.id)),
            "mode": "process",
            "range": scope,
            "budget": {"max_recheck_rounds": rounds},
            "reading_mode": "reread",
            "idempotency_key": "invalid-feedback",
            "run_now": False,
        }
    assert client.post("/api/jobs", json=request).status_code == 422
    estimate = client.post(
        f"/api/books/{request['book_id']}/estimates",
        json={"range": scope, "budget": request["budget"], "reading_mode": "reread"},
    )
    assert estimate.status_code == 422


def test_estimate_includes_one_feedback_stage_without_calling_provider(fake_provider_client):
    client = fake_provider_client
    create, factory = prepare(client, identity_feedback=True)
    job = create("feedback-estimate")
    with factory() as session:
        row = session.get(Job, job["id"])
        scope, book_id = json.loads(row.range_json), row.book_id
    request = {"range": scope, "budget": {"max_recheck_rounds": 1}, "reading_mode": "reread"}
    with_feedback = client.post(f"/api/books/{book_id}/estimates", json=request)
    assert with_feedback.status_code == 200, with_feedback.text
    del request["range"]["identity_feedback_protocol"]
    without = client.post(f"/api/books/{book_id}/estimates", json=request).json()["data"]
    extra = with_feedback.json()["data"]
    assert extra["total_tokens"] * 4 == without["total_tokens"] * 5
    assert extra["policy"]["identity_feedback_protocol"] == "identity-feedback-1"


def test_feedback_cannot_override_existing_manual_quote(fake_provider_client):
    client = fake_provider_client
    create, factory = prepare(client, identity_feedback=True)
    with transaction(factory) as session:
        quote = session.scalar(select(Quote).order_by(Quote.start_cp))
        annotation = Annotation(
            quote_id=quote.id,
            kind=QuoteKind.THOUGHT,
            status=AnnotationStatus.USER_CONFIRMED,
            source=AnnotationSource.USER,
            user_locked=True,
            evidence_refs_json="[]",
            version=7,
        )
        session.add(annotation)
    job = create("locked-feedback")
    adapter = FeedbackAdapter()
    result = run_job(
        factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: adapter
    )
    assert result.state is JobState.COMPLETED, result.errors
    with factory() as session:
        annotation = session.scalar(select(Annotation))
        assert annotation.user_locked and annotation.kind is QuoteKind.THOUGHT
        assert annotation.source is AnnotationSource.USER and annotation.version == 7
        assert "locked" in session.get(Job, job["id"]).checkpoint_json.lower()


@pytest.mark.parametrize("verdict", ["support_challenger", "undecidable"])
def test_new_anonymous_feedback_uses_adjudicated_identities_not_feedback_local_refs(
    fake_provider_client, verdict
):
    client = fake_provider_client
    create, factory = prepare(
        client,
        identity_feedback=True,
        all_quotes=True,
        auxiliary=True,
        context_policy="context-chapter-2",
        text="第一章\n门卫说：「早上好。」\n快递员答：「你好。」\n两人都没有透露姓名。",
    )

    class AnonymousAdapter(FeedbackAdapter):
        async def generate_labels(self, payload):
            raw = await super().generate_labels(payload)
            stage = payload.get("review_stage", "")
            if stage == "identity_feedback:1":
                proposal = raw["proposal"]
                proof = proposal["labels"][0]["evidence"]
                for label in proposal["labels"]:
                    label["character"] = "N9"
                proposal["new_characters"] = [
                    {
                        "ref": "N9",
                        "name": "门卫",
                        "description": "说话的门卫",
                        "evidence": proof,
                    }
                ]
                raw["issues"][0].update(kind="omitted_identity", character="N9")
            elif stage.startswith("adjudication:"):
                context = json.loads(payload["messages"][1]["content"])["context"]
                raw["new_characters"] = []
                for index, label in enumerate(raw["labels"]):
                    name = ("门卫", "快递员")[index]
                    proof = next(
                        row["ref"]
                        for row in context
                        if name in row["text"] and row["ref"] != label["q"]
                    )
                    ref = f"N{index + 1}"
                    label.update(character=ref, basis="direct", evidence=[proof])
                    raw["new_characters"].append(
                        {
                            "ref": ref,
                            "name": name,
                            "description": f"发言的{name}",
                            "evidence": [proof],
                        }
                    )
            return raw

    job = create("feedback-new-anonymous")
    adapter = AnonymousAdapter(verdict=verdict)
    result = run_job(
        factory, client.app.state.settings, job_id=job["id"], adapter_factory=lambda *_: adapter
    )
    assert result.state is JobState.COMPLETED, result.errors
    assert len(adapter.calls) == 5
    with factory() as session:
        annotations = list(session.scalars(select(Annotation).join(Quote).order_by(Quote.start_cp)))
        assert len(annotations) == 2
        names = [session.get(SpeakerGroup, row.speaker_id).canonical_name for row in annotations]
        if verdict == "support_challenger":
            assert names == ["门卫", "快递员"]
            assert len({row.speaker_id for row in annotations}) == 2
            assert all(row.status is AnnotationStatus.ACCEPTED for row in annotations)
        else:
            assert names == ["许晴", "许晴"]
            assert all(row.status is AnnotationStatus.PROVISIONAL for row in annotations)
            assert (
                session.scalar(select(SpeakerGroup).where(SpeakerGroup.canonical_name == "快递员"))
                is None
            )
        runs = list(session.scalars(select(InferenceRun).where(InferenceRun.job_id == job["id"])))
        assert (
            len(runs) == 5
            and sum(json.loads(row.usage_json)["total_tokens"] for row in runs) == 150
        )
        feedback = next(
            iter(
                json.loads(session.get(Job, job["id"]).checkpoint_json)[
                    "expression_reviews"
                ].values()
            )
        )["identity_feedback"]
        assert feedback["issues"][0]["character"] == "N9"
        assert feedback["source_ref"] in {row.id for row in runs}
