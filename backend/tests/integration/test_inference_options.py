"""Per-task thinking settings are immutable and do not mutate saved profiles."""

import json

from fastapi.testclient import TestClient

from ndr.config import Settings
from ndr.domain.enums import CredentialMode
from ndr.jobs.service import profile_snapshot
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.models import Job, ModelProfile


def test_inherited_defaults_and_explicit_overrides() -> None:
    profile = ModelProfile(
        id="profile",
        name="default",
        protocol="fake-provider",
        model="fake",
        base_url="http://127.0.0.1:1",
        credential_mode=CredentialMode.NONE,
        params_json=json.dumps({"thinking": {"type": "adaptive"}, "reasoning_effort": "high"}),
    )
    assert profile_snapshot(profile)["params"] == {
        "thinking": {"type": "adaptive"},
        "reasoning_effort": "high",
    }
    overridden = profile_snapshot(profile, {"thinking_mode": "enabled", "reasoning_effort": "low"})
    assert overridden["params"] == {"thinking": {"type": "enabled"}, "reasoning_effort": "low"}
    profile.params_json = json.dumps({"thinking": {"type": "disabled"}, "reasoning_effort": "high"})
    inherited = profile_snapshot(profile, {"thinking_mode": "default", "reasoning_effort": "low"})
    assert inherited["params"] == {"thinking": {"type": "disabled"}}
    assert json.loads(profile.params_json)["reasoning_effort"] == "high"


def test_roster_and_dialogue_freeze_options_without_changing_profile(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    client = fake_provider_client
    imported = client.post(
        "/api/books/import",
        files={"file": ("example.txt", "第一章\n「你好。」".encode(), "text/plain")},
    ).json()["data"]
    book_id = imported["book_id"]
    chapter = client.get(f"/api/books/{book_id}/chapters").json()["data"][0]
    original_params = {
        "temperature": 0.2,
        "max_tokens": 1234,
        "thinking": {"type": "adaptive"},
        "reasoning_effort": "medium",
    }
    profile = client.post(
        "/api/model-profiles",
        json={
            "name": "test",
            "protocol": "fake-provider",
            "model": "fake",
            "base_url": "http://127.0.0.1:1",
            "credential_mode": "none",
            "params": original_params,
        },
    ).json()["data"]
    options = {"thinking_mode": "enabled", "reasoning_effort": "high"}
    payload = {
        "book_id": book_id,
        "profile_id": profile["id"],
        "idempotency_key": "thinking-dialogue",
        "run_now": False,
        "inference_options": options,
    }
    response = client.post("/api/jobs", json=payload)
    assert response.status_code == 202, response.text
    dialogue_id = response.json()["data"]["id"]
    assert client.post("/api/jobs", json=payload).json()["data"]["id"] == dialogue_id
    assert (
        client.post(
            "/api/jobs",
            json={
                **payload,
                "inference_options": {"thinking_mode": "disabled"},
            },
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/jobs",
            json={
                **payload,
                "inference_options": {"thinking_mode": "high"},
            },
        ).status_code
        == 422
    )
    roster = client.post(
        f"/api/books/{book_id}/chapters/{chapter['id']}/character-roster/analyze",
        json={
            "profile_id": profile["id"],
            "idempotency_key": "thinking-roster",
            "run_now": False,
            "inference_options": {"thinking_mode": "disabled"},
        },
    )
    assert roster.status_code == 202, roster.text
    engine = create_db_engine(migrated_settings)
    try:
        with create_session_factory(engine)() as session:
            dialogue = session.get(Job, dialogue_id)
            params = json.loads(dialogue.profile_snapshot_json)["params"]
            assert params == {
                **original_params,
                "thinking": {"type": "enabled"},
                "reasoning_effort": "high",
            }
            roster_job = session.get(Job, roster.json()["data"]["id"])
            params = json.loads(roster_job.profile_snapshot_json)["params"]
            assert params["thinking"] == {"type": "disabled"}
            assert "reasoning_effort" not in params
            assert params["max_tokens"] == 1234
        saved = client.get("/api/model-profiles").json()["data"]
        assert saved[0]["params"] == original_params
        client.patch(f"/api/model-profiles/{profile['id']}", json={"params": {}})
        with create_session_factory(engine)() as session:
            frozen = session.get(Job, dialogue_id)
            assert (
                json.loads(frozen.profile_snapshot_json)["params"]["thinking"]["type"] == "enabled"
            )
    finally:
        engine.dispose()
