import json
from copy import deepcopy
from dataclasses import replace

import pytest
from sqlalchemy import select

from ndr.context.budget import CHAPTER_POLICY
from ndr.context.service import load_window_inputs, plan_range
from ndr.domain.enums import JobState, ReadingMode
from ndr.jobs.scheduler import run_job
from ndr.llm.adapters.fake import FakeProviderAdapter
from ndr.storage.models import BookVersion, InferenceRun, Job

TEXT = "第一章\n章首身份证据。\n林舟说：「你好。」\n陆欣答：「再见。」\n章末身份证据。\n第二章\n未来秘密。\n「下一章对白。」\n"


def prepare(client, text=TEXT):
    response = client.post(
        "/api/books/import",
        files={
            "file": ("whole.txt", text.encode(), "text/plain"),
        },
    )
    assert response.status_code == 202, response.text
    data = response.json()["data"]
    profile = client.post(
        "/api/model-profiles",
        json={
            "name": "whole",
            "protocol": "fake-provider",
            "base_url": "http://127.0.0.1:1",
            "model": "fake",
            "credential_mode": "none",
        },
    ).json()["data"]
    with client.app.state.session_factory() as session:
        version = session.get(BookVersion, data["book_version_id"])
        inputs = load_window_inputs(
            session, client.app.state.settings, version, policy=CHAPTER_POLICY
        )
    return data, profile, inputs


def create(
    client, data, profile, *, scope=None, selected=None, horizon=None, mode="initial", rounds=None
):
    scope = {
        "context_policy": "context-chapter-1",
        "output_protocol": "expression-production-1",
        **(scope or {}),
    }
    response = client.post(
        "/api/jobs",
        json={
            "book_id": data["book_id"],
            "profile_id": profile["id"],
            "range": scope,
            "selected_window_ids": selected,
            "reading_mode": mode,
            "visible_horizon_cp": horizon,
            "budget": {
                "max_format_retries": 0,
                **({"max_recheck_rounds": rounds} if rounds is not None else {}),
            },
            "run_now": False,
            "idempotency_key": "whole",
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["data"]


class WholeAdapter(FakeProviderAdapter):
    async def generate_labels(self, payload):
        self.calls.append({"payload": deepcopy(payload)})
        data = json.loads(payload["messages"][1]["content"])
        return {
            "labels": [
                {
                    "q": q,
                    "kind": "speech",
                    "character": None,
                    "basis": "insufficient",
                    "evidence": [],
                }
                for q in data["targets"]
            ],
            "breaks": [],
            "_usage": {"total_tokens": 11},
        }


def test_actual_job_request_sends_whole_chapter_not_next_chapter(migrated_client):
    client = migrated_client
    data, profile, inputs = prepare(client)
    end = inputs.source_ranges[0][1]
    job = create(client, data, profile, scope={"start_cp": 0, "end_cp": end})
    adapter = WholeAdapter()
    result = run_job(
        client.app.state.session_factory,
        client.app.state.settings,
        job_id=job["id"],
        adapter_factory=lambda *_: adapter,
    )
    assert result.state is JobState.COMPLETED and result.calls == 1, (
        result.errors,
        client.get(f"/api/jobs/{job['id']}").json()["data"]["last_error"],
    )
    sent = json.loads(adapter.calls[0]["payload"]["messages"][1]["content"])
    assert "".join(f["text"] for f in sent["context"]) == inputs.canonical_text[:end]
    assert "未来秘密" not in json.dumps(sent, ensure_ascii=False)
    assert len(sent["targets"]) == 2
    assert max(fragment["end_cp"] for fragment in sent["context"]) == end
    assert (
        run_job(
            client.app.state.session_factory,
            client.app.state.settings,
            job_id=job["id"],
            adapter_factory=lambda *_: adapter,
        ).calls
        == 0
    )


def test_estimation_respects_the_explicit_policy_and_execution_window_ids(migrated_client):
    client = migrated_client
    data, profile, inputs = prepare(client)
    scope = {"context_policy": "context-chapter-1", "end_cp": inputs.source_ranges[0][1]}
    response = client.post(f"/api/books/{data['book_id']}/estimates", json={"range": scope})
    assert response.status_code == 200, response.text
    estimate = response.json()["data"]
    assert estimate["policy"]["full_source"] and estimate["window_count"] == 1
    job = create(client, data, profile, scope=scope)
    result = run_job(
        client.app.state.session_factory,
        client.app.state.settings,
        job_id=job["id"],
        adapter_factory=lambda *_: WholeAdapter(),
    )
    assert result.state is JobState.COMPLETED
    actual = client.get(f"/api/jobs/{job['id']}").json()["data"]
    assert [w["window_id"] for w in actual["windows"]] == [
        w["window_id"] for w in estimate["windows"]
    ]


def test_selected_window_does_not_add_targets_from_other_chapters(migrated_client):
    client = migrated_client
    data, profile, inputs = prepare(client)
    with client.app.state.session_factory() as session:
        version = session.get(BookVersion, data["book_version_id"])
        plan = plan_range(session, client.app.state.settings, version, policy=CHAPTER_POLICY)
    assert len(plan.windows) == 2
    job = create(client, data, profile, selected=[plan.windows[1].window_id])
    adapter = WholeAdapter()
    result = run_job(
        client.app.state.session_factory,
        client.app.state.settings,
        job_id=job["id"],
        adapter_factory=lambda *_: adapter,
    )
    assert result.state is JobState.COMPLETED and result.calls == 1
    sent = json.loads(adapter.calls[0]["payload"]["messages"][1]["content"])
    assert len(sent["targets"]) == 1
    assert (
        "".join(f["text"] for f in sent["context"])
        == inputs.canonical_text[inputs.source_ranges[1][0] :]
    )


def test_partial_range_never_expands_back_to_whole_book_or_chapter(migrated_client):
    client = migrated_client
    data, profile, inputs = prepare(client)
    first = inputs.quotes[0]
    job = create(client, data, profile, scope={"start_cp": first.start_cp, "end_cp": first.end_cp})
    adapter = WholeAdapter()
    result = run_job(
        client.app.state.session_factory,
        client.app.state.settings,
        job_id=job["id"],
        adapter_factory=lambda *_: adapter,
    )
    assert result.state is JobState.COMPLETED and result.calls == 1
    sent = json.loads(adapter.calls[0]["payload"]["messages"][1]["content"])
    assert (
        "".join(f["text"] for f in sent["context"])
        == inputs.canonical_text[first.start_cp : first.end_cp]
    )


@pytest.mark.parametrize("evidence", [False, True])
def test_complete_review_never_expands_a_selected_partial_range(migrated_client, evidence):
    client = migrated_client
    data, profile, inputs = prepare(client)
    first = inputs.quotes[0]
    scope = {"start_cp": first.start_cp, "end_cp": first.end_cp}
    if evidence:
        scope["review_protocol"] = "expression-evidence-review-1"
    job = create(client, data, profile, scope=scope, rounds=1)
    adapter = WholeAdapter()
    result = run_job(
        client.app.state.session_factory,
        client.app.state.settings,
        job_id=job["id"],
        adapter_factory=lambda *_: adapter,
    )
    assert result.state is JobState.COMPLETED, result.errors
    assert len(adapter.calls) == 2
    for call in adapter.calls:
        sent = json.loads(call["payload"]["messages"][1]["content"])
        assert (
            "".join(f["text"] for f in sent["context"])
            == inputs.canonical_text[first.start_cp : first.end_cp]
        )


def test_oversized_unit_fails_zero_calls_instead_of_leaving_running(migrated_client):
    client = migrated_client
    data, profile, _ = prepare(client)
    job = create(client, data, profile)
    adapter = WholeAdapter()
    result = run_job(
        client.app.state.session_factory,
        client.app.state.settings,
        job_id=job["id"],
        policy=replace(CHAPTER_POLICY, context_tokens=5),
        adapter_factory=lambda *_: adapter,
    )
    assert result.state is JobState.FAILED and result.calls == 0 and not adapter.calls
    with client.app.state.session_factory() as session:
        row = session.get(Job, job["id"])
        assert row.state is JobState.FAILED and "未截断" in row.last_error
        assert not list(session.scalars(select(InferenceRun).where(InferenceRun.job_id == row.id)))


def test_initial_target_beyond_horizon_is_refused_before_call(migrated_client):
    client = migrated_client
    data, profile, inputs = prepare(client)
    job = create(client, data, profile, horizon=inputs.quotes[0].end_cp)
    adapter = WholeAdapter()
    result = run_job(
        client.app.state.session_factory,
        client.app.state.settings,
        job_id=job["id"],
        adapter_factory=lambda *_: adapter,
    )
    assert result.state is JobState.FAILED and not adapter.calls
    assert "invalid_full_context" in result.errors


def test_estimate_surfaces_oversized_complete_source_as_validation_error(migrated_client):
    client = migrated_client
    data, _, _ = prepare(client, "第一章\n" + "叙述" * 17000 + "「你好。」")
    response = client.post(
        f"/api/books/{data['book_id']}/estimates",
        json={"range": {"context_policy": "context-chapter-1"}},
    )
    assert response.status_code == 422
    assert "未截断" in response.json()["error"]["message"]


def test_full_policy_requires_explicit_short_owner_protocol(migrated_client):
    client = migrated_client
    data, _, _ = prepare(client)
    response = client.post(
        "/api/jobs",
        json={
            "book_id": data["book_id"],
            "range": {"context_policy": "context-chapter-1"},
            "run_now": False,
            "idempotency_key": "old",
        },
    )
    assert response.status_code == 422
    assert "短表达协议" in response.json()["error"]["message"]


def test_reread_keeps_same_source_but_may_ignore_initial_horizon(migrated_client):
    client = migrated_client
    data, profile, inputs = prepare(client)
    end = inputs.source_ranges[0][1]
    job = create(client, data, profile, scope={"end_cp": end}, horizon=0, mode="reread")
    adapter = WholeAdapter()
    result = run_job(
        client.app.state.session_factory,
        client.app.state.settings,
        job_id=job["id"],
        adapter_factory=lambda *_: adapter,
    )
    assert result.state is JobState.COMPLETED
    sent = json.loads(adapter.calls[0]["payload"]["messages"][1]["content"])
    with client.app.state.session_factory() as session:
        assert (
            json.loads(session.get(Job, job["id"]).range_json)["reading_mode"]
            == ReadingMode.REREAD.value
        )
    assert "".join(f["text"] for f in sent["context"]) == inputs.canonical_text[:end]


@pytest.mark.parametrize("rounds", [0, 1, 2])
def test_estimate_includes_three_possible_evidence_stages_per_round(migrated_client, rounds):
    client = migrated_client
    data, _, inputs = prepare(client)
    scope = {"context_policy": "context-chapter-1", "end_cp": inputs.source_ranges[0][1]}
    baseline = client.post(f"/api/books/{data['book_id']}/estimates", json={"range": scope}).json()[
        "data"
    ]
    response = client.post(
        f"/api/books/{data['book_id']}/estimates",
        json={
            "range": {**scope, "review_protocol": "expression-evidence-review-1"},
            "budget": {"max_recheck_rounds": rounds},
        },
    )
    assert response.status_code == 200, response.text
    estimate = response.json()["data"]
    assert estimate["total_tokens"] == baseline["total_tokens"] * (1 + 3 * rounds)
    assert estimate["windows"][0]["estimated_tokens"] == estimate["total_tokens"]
    assert estimate["policy"]["review_protocol"] == "expression-evidence-review-1"
    assert "未构成上限" in "".join(estimate["notes"])
    assert (
        client.post(
            f"/api/books/{data['book_id']}/estimates",
            json={
                "range": {**scope, "review_protocol": "unrecognized"},
            },
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "strategy",
    ["legacy", "complete", "complete-review", "complete-blocks", "complete-blocks-review"],
)
def test_recheck_strategy_keeps_server_scope_and_does_not_call_at_creation(
    migrated_client, strategy
):
    _recheck_strategy_creation(migrated_client, strategy)


@pytest.mark.parametrize(
    "strategy", ["complete", "complete-review", "complete-blocks", "complete-blocks-review"]
)
def test_actual_local_recheck_runs_selected_strategy_and_full_window_review(
    migrated_client, strategy
):
    client = migrated_client
    _, profile, inputs = prepare(client)
    response = client.post(
        f"/api/quotes/{inputs.quotes[0].quote_id}/recheck",
        json={
            "profile_id": profile["id"],
            "dialogue_strategy": strategy,
            "budget": {"max_recheck_rounds": 1, "max_format_retries": 0},
            "idempotency_key": "actual-local",
            "run_now": False,
        },
    )
    assert response.status_code == 202, response.text
    job = response.json()["data"]
    adapter = WholeAdapter()
    result = run_job(
        client.app.state.session_factory,
        client.app.state.settings,
        job_id=job["id"],
        adapter_factory=lambda *_: adapter,
    )
    assert result.state is JobState.COMPLETED, result.errors
    assert result.calls + result.recheck_calls == 2 and len(adapter.calls) == 2
    for call in adapter.calls:
        sent = json.loads(call["payload"]["messages"][1]["content"])
        assert (
            "".join(f["text"] for f in sent["context"])
            == inputs.canonical_text[: inputs.source_ranges[0][1]]
        )


def _recheck_strategy_creation(migrated_client, strategy):
    client = migrated_client
    data, profile, inputs = prepare(client)
    response = client.post(
        f"/api/quotes/{inputs.quotes[0].quote_id}/recheck",
        json={
            "profile_id": profile["id"],
            "dialogue_strategy": strategy,
            "idempotency_key": "local-strategy",
            "run_now": False,
        },
    )
    assert response.status_code == 202, response.text
    job = response.json()["data"]
    assert job["calls"] == 0 and job["kind"] == "RECHECK"
    scope = job["range"]
    assert [scope["start_cp"], scope["end_cp"]] == list(inputs.source_ranges[0])
    if strategy == "legacy":
        assert "output_protocol" not in scope and "context_policy" not in scope
    else:
        assert scope["context_policy"] == (
            "context-chapter-2" if strategy.startswith("complete-blocks") else "context-chapter-1"
        )
        assert scope["output_protocol"] == "expression-production-1"
        assert (scope.get("review_protocol") == "expression-evidence-review-1") == (
            strategy.endswith("-review")
        )
    assert (
        client.post(
            f"/api/quotes/{inputs.quotes[0].quote_id}/recheck",
            json={
                "profile_id": profile["id"],
                "dialogue_strategy": "unknown",
                "idempotency_key": "invalid-local",
                "run_now": False,
            },
        ).status_code
        == 422
    )
