import json
from dataclasses import replace

import pytest
from test_full_source_jobs import WholeAdapter, create, prepare

from ndr.context.budget import DIALOGUE_BLOCK_POLICY, POLICY_BY_VERSION
from ndr.context.service import plan_range
from ndr.domain.enums import JobState, ReadingMode
from ndr.jobs.scheduler import run_job
from ndr.storage.models import BookVersion

TEXT = "开头。\n「甲。」\n「乙。」\n旁白。\n「丙。」\n「丁。」\n结尾。\n"


@pytest.mark.parametrize("mode", ["initial", "reread"])
@pytest.mark.parametrize("selected_only", [False, True])
def test_estimate_matches_actual_block_windows_with_readonly_boundary_source(
    migrated_client, monkeypatch, mode, selected_only
):
    client = migrated_client
    monkeypatch.setitem(
        POLICY_BY_VERSION, "context-chapter-2", replace(DIALOGUE_BLOCK_POLICY, context_tokens=22)
    )
    data, profile, _ = prepare(client, TEXT)
    scope = {"context_policy": "context-chapter-2", "output_protocol": "expression-production-1"}
    response = client.post(
        f"/api/books/{data['book_id']}/estimates", json={"range": scope, "reading_mode": mode}
    )
    assert response.status_code == 200, response.text
    estimate = response.json()["data"]
    assert estimate["window_count"] > 1 and estimate["policy"]["dialogue_blocks"] is True
    with client.app.state.session_factory() as session:
        expected_plan = plan_range(
            session,
            client.app.state.settings,
            session.get(BookVersion, data["book_version_id"]),
            policy=POLICY_BY_VERSION["context-chapter-2"],
            reading_mode=ReadingMode(mode),
        )
    selected = [estimate["windows"][0]["window_id"]] if selected_only else None
    job = create(client, data, profile, scope=scope, mode=mode, rounds=0, selected=selected)
    adapter = WholeAdapter()
    result = run_job(
        client.app.state.session_factory,
        client.app.state.settings,
        job_id=job["id"],
        adapter_factory=lambda *_: adapter,
    )
    assert result.state is JobState.COMPLETED, result.errors
    expected_windows = expected_plan.windows[:1] if selected_only else expected_plan.windows
    assert result.calls == len(expected_windows) == len(adapter.calls)
    detail = client.get(f"/api/jobs/{job['id']}").json()["data"]
    assert sorted(w["window_id"] for w in detail["windows"]) == sorted(
        w.window_id for w in expected_windows
    )
    targets = []
    for call, window in zip(adapter.calls, expected_windows, strict=True):
        sent = json.loads(call["payload"]["messages"][1]["content"])
        targets.extend(sent["targets"])
        assert all(f["text"] == TEXT[f["start_cp"] : f["end_cp"]] for f in sent["context"])
        assert [(f["start_cp"], f["end_cp"], f["text"]) for f in sent["context"]] == [
            (f.start_cp, f.end_cp, f.text) for f in window.fragments
        ]
        if mode == "initial":
            assert all(f["end_cp"] <= window.visible_horizon_cp for f in sent["context"])
    assert len(targets) == sum(len(w.target_quote_ids) for w in expected_windows)
    # Q references are local to each call; the server maps them to distinct targets.
    assert estimate["target_count"] == 4
    paid_calls = len(adapter.calls)
    resumed = run_job(
        client.app.state.session_factory,
        client.app.state.settings,
        job_id=job["id"],
        adapter_factory=lambda *_: adapter,
    )
    assert resumed.state is JobState.COMPLETED
    assert len(adapter.calls) == paid_calls
