"""Rejected character proposals retain known charges without changing characters."""

import json
from copy import deepcopy

import pytest
from sqlalchemy import select

from ndr.domain.enums import JobState
from ndr.jobs.roster import run_character_roster_job
from ndr.jobs.service import spent_tokens, usage_summary
from ndr.llm.errors import ProviderError, ProviderErrorKind
from ndr.storage.models import BookCharacter, InferenceRun, Job


def prepare(client):
    imported = client.post(
        "/api/books/import",
        files={"file": ("usage.txt", "第一章\n林舟说：「早上好。」".encode(), "text/plain")},
    ).json()["data"]
    chapter = client.get(f"/api/books/{imported['book_id']}/chapters").json()["data"][0]
    profile = client.post(
        "/api/model-profiles",
        json={
            "name": "isolated usage test",
            "protocol": "fake-provider",
            "base_url": "http://127.0.0.1:1",
            "model": "fake",
            "credential_mode": "none",
        },
    ).json()["data"]
    job = client.post(
        f"/api/books/{imported['book_id']}/chapters/{chapter['id']}/character-roster/analyze",
        json={"profile_id": profile["id"], "idempotency_key": "roster-usage", "run_now": False},
    ).json()["data"]
    return imported, job


@pytest.mark.parametrize(
    ("case", "usage", "expected_tokens", "unknown"),
    [
        ("semantic", {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}, 18, 0),
        ("schema", {"total_tokens": 18}, 18, 0),
        ("provider", {"total_tokens": 18}, 18, 0),
        ("semantic", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, 0, 0),
        ("semantic", {"total_tokens": None, "unknown": True}, 0, 1),
        ("semantic", None, 0, 1),
        ("semantic", {"total_tokens": True}, 0, 1),
        ("semantic", {"total_tokens": -1}, 0, 1),
        ("semantic", {"total_tokens": "18"}, 0, 1),
        ("provider", None, 0, 1),
        ("success", {"total_tokens": 18}, 18, 0),
        ("success", {"unknown": True, "total_tokens": None}, 0, 1),
    ],
)
def test_roster_attempt_usage_survives_validation_or_provider_failure(
    migrated_client,
    case,
    usage,
    expected_tokens,
    unknown,
):
    client = migrated_client
    imported, job = prepare(client)
    adapter_calls = []

    class Adapter:
        async def generate_labels(self, request):
            adapter_calls.append(request)
            if case == "provider":
                raise ProviderError(
                    ProviderErrorKind.INVALID_OUTPUT,
                    "invalid provider response",
                    details={"usage": deepcopy(usage)} if usage is not None else {},
                )
            payload = {
                "schema_version": "1.0",
                "characters": [
                    {
                        "temp_ref": "c1",
                        "name": "林舟",
                        "evidence_refs": ["L1"],
                        "pov_candidate": True,
                    }
                ],
            }
            if case == "semantic":
                payload["characters"][0]["character_id"] = "not-sent-in-request"
            elif case == "schema":
                payload["characters"][0]["name"] = None
            if usage is not None:
                payload["_usage"] = deepcopy(usage)
            return payload

    outcome = run_character_roster_job(
        client.app.state.session_factory,
        client.app.state.settings,
        job_id=job["id"],
        adapter_factory=lambda *_: Adapter(),
    )
    assert len(adapter_calls) == outcome.calls == 1
    assert outcome.state is (JobState.COMPLETED if case == "success" else JobState.FAILED)
    with client.app.state.session_factory() as session:
        attempt = session.scalar(select(InferenceRun).where(InferenceRun.job_id == job["id"]))
        assert attempt is not None
        assert (attempt.usage_json is None) == bool(unknown)
        if not unknown:
            assert json.loads(attempt.usage_json) == usage
        spent = spent_tokens(session, job["id"])
        assert spent["total_tokens"] == expected_tokens and spent["unknown_runs"] == unknown
        summary = usage_summary(session, imported["book_id"])
        assert (
            summary["total_tokens"] == expected_tokens and summary["unknown_usage_runs"] == unknown
        )
        assert summary["runs"] == 1
        stored = session.get(Job, job["id"])
        assert json.loads(stored.progress_json)["calls"] == 1
        people = list(
            session.scalars(
                select(BookCharacter).where(
                    BookCharacter.book_version_id == imported["book_version_id"],
                )
            )
        )
        assert bool(people) == (case == "success")
    detail = client.get(f"/api/jobs/{job['id']}").json()["data"]
    assert detail["calls"] == 1 and detail["unknown_usage_runs"] == unknown
    assert detail["usage"]["total_tokens"] == expected_tokens
