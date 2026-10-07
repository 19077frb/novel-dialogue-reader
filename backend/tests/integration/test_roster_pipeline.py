import json
from copy import deepcopy

import pytest
from sqlalchemy import select

from ndr.characters.facts import read_identity_records
from ndr.domain.enums import InferenceRunState, JobState
from ndr.jobs.roster import run_character_roster_job
from ndr.jobs.scheduler import reconcile_job, run_job
from ndr.llm.adapters.fake import FakeProviderAdapter
from ndr.llm.errors import ProviderError, ProviderErrorKind
from ndr.storage.models import BookCharacter, BookVersion, InferenceRun, Job
from ndr.storage.run_archive import decode_archive, encode_archive


def prepare(client, *, api_options=None, **budget):
    data = client.post(
        "/api/books/import",
        files={
            "file": (
                "roster.txt",
                "第一章\n林舟说：「你好。」\n陆欣说：「再见。」".encode(),
                "text/plain",
            ),
        },
    ).json()["data"]
    chapter = client.get(f"/api/books/{data['book_id']}/chapters").json()["data"][0]
    profile = client.post(
        "/api/model-profiles",
        json={
            "name": "isolated",
            "protocol": "fake-provider",
            "base_url": "http://127.0.0.1:1",
            "model": "fake",
            "credential_mode": "none",
        },
    ).json()["data"]
    job = client.post(
        f"/api/books/{data['book_id']}/chapters/{chapter['id']}/character-roster/analyze",
        json={
            "profile_id": profile["id"], "idempotency_key": "repair", "run_now": False,
            **(api_options or {}),
        },
    ).json()["data"]
    if api_options is None:
        update(client, job, budget=budget)
    return data, job


def update(client, job, *, budget=None, state=None, scope=None, snapshot=None):
    with client.app.state.session_factory() as session:
        stored = session.get(Job, job["id"])
        stored.range_json = json.dumps(
            {
                **json.loads(stored.range_json),
                "roster_repair_protocol": "roster-repair-1",
                **(scope or {}),
            }
        )
        if budget is not None:
            stored.budget_json = json.dumps({**json.loads(stored.budget_json), **budget})
        if state is not None:
            stored.state = state
        if snapshot is not None:
            stored.profile_snapshot_json = json.dumps(
                {
                    **json.loads(stored.profile_snapshot_json),
                    **snapshot,
                }
            )
        session.commit()


def person(ref, name, line):
    return {
        "temp_ref": ref,
        "name": name,
        "evidence_refs": [line],
        "facts": [{"kind": "name", "value": name, "evidence_refs": [line]}],
    }


def primary(*, invalid=True, all_bad=False, usage=5):
    rows = [
        person("c1", "林舟", "L999" if all_bad else "L2"),
        person("c2", "陆欣", "L999" if invalid else "L3"),
    ]
    return {
        "schema_version": "1.1",
        "characters": rows,
        **({} if usage is None else {"_usage": {"total_tokens": usage}}),
    }


def fixed(*, indices=None):
    return {
        "schema_version": "roster-repair-1",
        "repairs": [
            {
                "indices": [2] if indices is None else indices,
                "characters": [
                    person("c2", "陆欣", "L3"),
                ],
            },
        ],
        "_usage": {"total_tokens": 7},
    }


def run(client, job, adapter):
    return run_character_roster_job(
        client.app.state.session_factory,
        client.app.state.settings,
        job_id=job["id"],
        adapter_factory=lambda *_: adapter,
    )


def saved(client, job):
    with client.app.state.session_factory() as session:
        row = session.get(Job, job["id"])
        runs = list(
            session.scalars(
                select(InferenceRun)
                .where(InferenceRun.job_id == row.id)
                .order_by(InferenceRun.created_at)
            )
        )
        return (
            row.state,
            json.loads(row.progress_json),
            [
                {
                    "id": r.id,
                    "state": r.state,
                    "usage": json.loads(r.usage_json or "{}"),
                    "archive": decode_archive(r.call_archive),
                }
                for r in runs
            ],
        )


class CallbackAdapter(FakeProviderAdapter):
    def __init__(self, script, callback, at=1):
        super().__init__(script=deepcopy(script))
        self.callback, self.at = callback, at

    async def generate_labels(self, payload):
        result = await super().generate_labels(payload)
        if len(self.calls) == self.at:
            self.callback()
        return result


def test_public_api_freezes_repair_limits_and_runs_real_pipeline(migrated_client):
    client = migrated_client
    data, job = prepare(client, api_options={
        "roster_repair_enabled": True, "max_roster_repairs": 1, "max_format_retries": 0,
    })
    assert job["range"]["roster_repair_protocol"] == "roster-repair-1"
    assert job["range"]["roster_repair_policy"] == "identity-blocks-2"
    with client.app.state.session_factory() as session:
        budget = json.loads(session.get(Job, job["id"]).budget_json)
        assert budget["max_roster_repairs"] == 1
        assert budget["max_format_retries"] == 0
    adapter = FakeProviderAdapter(script=[primary(), fixed()])
    outcome = run(client, job, adapter)
    assert outcome.state is JobState.COMPLETED and outcome.calls == 2
    with client.app.state.session_factory() as session:
        people = list(session.scalars(select(BookCharacter).where(
            BookCharacter.book_version_id == data["book_version_id"]
        )))
        assert {p.canonical_name for p in people} == {"林舟", "陆欣"}
        assert all(not p.user_confirmed for p in people)


def test_public_api_disabled_repair_preserves_legacy_digest_and_budget(migrated_client):
    client = migrated_client
    data, job = prepare(client, api_options={})
    assert "roster_repair_protocol" not in job["range"]
    with client.app.state.session_factory() as session:
        stored = session.get(Job, job["id"])
        assert json.loads(stored.budget_json) == {"max_input_tokens": None}
        profile_id = json.loads(stored.profile_snapshot_json)["profile_id"]
    endpoint = f"/api/books/{data['book_id']}/chapters/{job['range']['chapter_id']}/character-roster/analyze"
    base = {"profile_id": profile_id, "idempotency_key": "repair", "run_now": False}
    same = client.post(endpoint, json={**base, "roster_repair_enabled": False,
        "max_roster_repairs": 5, "max_format_retries": 5})
    assert same.status_code == 202 and same.json()["data"]["id"] == job["id"]
    assert client.post(endpoint, json={**base, "roster_repair_enabled": True}).status_code == 409
    for value in [-1, 6, True, "1", 1.5]:
        for key in ["max_roster_repairs", "max_format_retries"]:
            assert client.post(endpoint, json={**base, key: value}).status_code == 422


def test_public_api_zero_repairs_keeps_valid_people_without_second_call(migrated_client):
    client = migrated_client
    _, job = prepare(client, api_options={"roster_repair_enabled": True, "max_roster_repairs": 0})
    adapter = FakeProviderAdapter(script=[primary()])
    result = run(client, job, adapter)
    assert result.state is JobState.COMPLETED and result.calls == 1
    assert len(adapter.calls) == 1
    assert saved(client, job)[1]["proposal_diagnostics"]["unresolved_groups"]


def test_isolated_policy_keeps_valid_repair_and_only_retries_remaining_group(migrated_client):
    client = migrated_client
    data, job = prepare(client, api_options={
        "roster_repair_enabled": True, "max_roster_repairs": 2, "max_format_retries": 0,
    })
    partial = fixed()
    partial["repairs"].insert(0, {"indices": [1], "characters": [person("c1", "林舟", "L999")]})
    partial["repairs"][1]["characters"][0]["description"] = "没有对应逐事实依据的显示说明"
    last = {"schema_version": "roster-repair-1", "repairs": [
        {"indices": [1], "characters": [person("c1", "林舟", "L2")]},
    ], "_usage": {"total_tokens": 11}}
    adapter = FakeProviderAdapter(script=[primary(all_bad=True), partial, last])
    result = run(client, job, adapter)
    assert result.state is JobState.COMPLETED and result.calls == 3
    _, progress, runs = saved(client, job)
    diagnostics = progress["proposal_diagnostics"]
    assert diagnostics["repair_policy"] == "identity-blocks-2"
    assert diagnostics["repair_succeeded"] and diagnostics["unresolved_groups"] == []
    assert diagnostics["repair_steps"][0]["successful_groups"] == [[2]]
    assert diagnostics["repair_steps"][0]["unresolved_groups"] == [[1]]
    assert diagnostics["repair_steps"][0]["discarded_descriptions"] == 1
    request = runs[2]["archive"]["request"]
    task = json.loads(request["messages"][1]["content"].split("\n", 1)[1].split("\n\n", 1)[0])
    assert [g["indices"] for g in task["groups"]] == [[1]]
    assert [p["name"] for p in task["retained_characters"]] == ["陆欣"]
    assert request["roster_repair_policy"] == "identity-blocks-2"
    assert request["roster_repair_binding"] != runs[1]["archive"]["request"]["roster_repair_binding"]
    assert [r["usage"]["total_tokens"] for r in runs] == [5, 7, 11]
    with client.app.state.session_factory() as session:
        version = session.get(BookVersion, data["book_version_id"])
        people = list(session.scalars(select(BookCharacter).where(BookCharacter.book_version_id == version.id)))
        sources = {p.canonical_name: read_identity_records(p, version)[0].source_ref for p in people}
        assert sources == {"陆欣": runs[1]["id"], "林舟": runs[2]["id"]}
    assert run(client, job, adapter).calls == 0 and len(adapter.calls) == 3


def test_isolated_policy_exhausted_partial_repair_is_not_reported_as_whole_success(migrated_client):
    client = migrated_client
    data, job = prepare(client, api_options={"roster_repair_enabled": True, "max_roster_repairs": 1})
    partial = fixed()
    partial["repairs"].insert(0, {"indices": [1], "characters": [person("c1", "林舟", "L999")]})
    result = run(client, job, FakeProviderAdapter(script=[primary(all_bad=True), partial]))
    assert result.state is JobState.COMPLETED and result.calls == 2
    diagnostics = saved(client, job)[1]["proposal_diagnostics"]
    assert not diagnostics["repair_succeeded"] and diagnostics["unresolved_groups"] == [[1]]
    with client.app.state.session_factory() as session:
        people = list(session.scalars(select(BookCharacter).where(
            BookCharacter.book_version_id == data["book_version_id"])))
        assert [p.canonical_name for p in people] == ["陆欣"]


def test_isolated_policy_pause_restores_partial_receipt_without_resending_valid_group(migrated_client):
    client = migrated_client
    data, job = prepare(client, api_options={"roster_repair_enabled": True, "max_roster_repairs": 2})
    partial = fixed()
    partial["repairs"].insert(0, {"indices": [1], "characters": [person("c1", "林舟", "L999")]})
    adapter = CallbackAdapter([primary(all_bad=True), partial],
                              lambda: update(client, job, state=JobState.PAUSING), at=2)
    assert run(client, job, adapter).state is JobState.PAUSED
    with client.app.state.session_factory() as session:
        assert not list(session.scalars(select(BookCharacter).where(
            BookCharacter.book_version_id == data["book_version_id"])))
    original_runs = saved(client, job)[2]
    update(client, job, state=JobState.QUEUED)
    last = fixed(indices=[1])
    last["repairs"][0]["characters"] = [person("c1", "林舟", "L2")]
    resumed_adapter = FakeProviderAdapter(script=[last])
    result = run(client, job, resumed_adapter)
    assert result.state is JobState.COMPLETED and result.calls == 1
    runs = saved(client, job)[2]
    assert [r["id"] for r in runs[:2]] == [r["id"] for r in original_runs]
    assert [r["archive"] for r in runs[:2]] == [r["archive"] for r in original_runs]
    with client.app.state.session_factory() as session:
        version = session.get(BookVersion, data["book_version_id"])
        people = list(session.scalars(select(BookCharacter).where(BookCharacter.book_version_id == version.id)))
        sources = {p.canonical_name: read_identity_records(p, version)[0].source_ref for p in people}
        assert sources == {"陆欣": runs[1]["id"], "林舟": runs[2]["id"]}


def test_enabled_idempotency_key_returns_frozen_legacy_policy_job(migrated_client):
    client = migrated_client
    data, job = prepare(client, api_options={"roster_repair_enabled": True})
    with client.app.state.session_factory() as session:
        stored = session.get(Job, job["id"])
        scope = json.loads(stored.range_json)
        del scope["roster_repair_policy"]
        stored.range_json = json.dumps(scope)
        profile_id = json.loads(stored.profile_snapshot_json)["profile_id"]
        session.commit()
    endpoint = f"/api/books/{data['book_id']}/chapters/{scope['chapter_id']}/character-roster/analyze"
    result = client.post(endpoint, json={"profile_id": profile_id,
                                       "idempotency_key": "repair", "run_now": False,
                                       "roster_repair_enabled": True})
    assert result.status_code == 202 and result.json()["data"]["id"] == job["id"]
    assert "roster_repair_policy" not in result.json()["data"]["range"]


def test_actual_partial_repair_keeps_separate_receipts_and_identity_sources(migrated_client):
    client = migrated_client
    data, job = prepare(client)
    adapter = FakeProviderAdapter(script=[primary(), fixed()])
    result = run(client, job, adapter)
    assert result.state is JobState.COMPLETED and result.calls == 2
    state, progress, runs = saved(client, job)
    assert state is JobState.COMPLETED and progress["calls"] == 2
    assert [r["usage"]["total_tokens"] for r in runs] == [5, 7]
    assert progress["proposal_diagnostics"]["unresolved_groups"] == []
    assert runs[1]["archive"]["request"]["roster_repair_protocol"] == "roster-repair-1"
    repair_messages = runs[1]["archive"]["request"]["messages"]
    assert "schema_version必须为roster-repair-1" in repair_messages[0]["content"]
    assert "schema_version=1.1" not in repair_messages[0]["content"]
    assert "林舟说" in repair_messages[1]["content"] and "陆欣说" in repair_messages[1]["content"]
    with client.app.state.session_factory() as session:
        version = session.get(BookVersion, data["book_version_id"])
        people = list(
            session.scalars(
                select(BookCharacter).where(BookCharacter.book_version_id == version.id)
            )
        )
        sources = {
            p.canonical_name: read_identity_records(p, version)[0].source_ref for p in people
        }
        assert sources == {"林舟": runs[0]["id"], "陆欣": runs[1]["id"]}
        assert all(not p.user_confirmed for p in people)
    assert run(client, job, adapter).calls == 0 and len(adapter.calls) == 2


def test_all_invalid_proposal_can_be_repaired_without_rerunning_primary(migrated_client):
    client = migrated_client
    _, job = prepare(client)
    repair = fixed(indices=[2])
    repair["repairs"].insert(0, {"indices": [1], "characters": [person("c1", "林舟", "L2")]})
    result = run(client, job, FakeProviderAdapter(script=[primary(all_bad=True), repair]))
    assert result.state is JobState.COMPLETED and result.calls == 2


@pytest.mark.parametrize("all_bad", [False, True])
def test_known_failed_repairs_are_charged_and_preserve_first_valid_people(migrated_client, all_bad):
    client = migrated_client
    data, job = prepare(client, max_roster_repairs=2)
    bad = {"schema_version": "wrong", "_usage": {"total_tokens": 7}}
    result = run(client, job, FakeProviderAdapter(script=[primary(all_bad=all_bad), bad, bad]))
    assert result.state is (JobState.FAILED if all_bad else JobState.COMPLETED)
    assert result.calls == 3
    _, progress, runs = saved(client, job)
    assert [r["usage"]["total_tokens"] for r in runs] == [5, 7, 7]
    assert [r["state"] for r in runs][1:] == [InferenceRunState.FAILED] * 2
    with client.app.state.session_factory() as session:
        people = list(
            session.scalars(
                select(BookCharacter).where(
                    BookCharacter.book_version_id == data["book_version_id"]
                )
            )
        )
        assert [p.canonical_name for p in people] == ([] if all_bad else ["林舟"])
    if not all_bad:
        assert progress["proposal_diagnostics"]["unresolved_groups"] == [[2]]


def test_primary_format_failure_uses_separate_bounded_calls(migrated_client):
    client = migrated_client
    _, job = prepare(client, max_format_retries=1)
    adapter = FakeProviderAdapter(
        script=[
            {"schema_version": "wrong", "characters": [], "_usage": {"total_tokens": 3}},
            primary(invalid=False),
        ]
    )
    result = run(client, job, adapter)
    assert result.state is JobState.COMPLETED and result.calls == 2
    assert "校验" in adapter.calls[1]["payload"]["messages"][-1]["content"]


@pytest.mark.parametrize("at", [1, 2])
def test_pause_after_received_result_restores_without_duplicate_calls(migrated_client, at):
    client = migrated_client
    _, job = prepare(client)
    adapter = CallbackAdapter(
        [primary(), fixed()], lambda: update(client, job, state=JobState.PAUSING), at=at
    )
    result = run(client, job, adapter)
    assert result.state is JobState.PAUSED and result.calls == at
    update(client, job, state=JobState.QUEUED)
    resumed = FakeProviderAdapter(script=[fixed()] if at == 1 else [])
    result = run(client, job, resumed)
    assert result.state is JobState.COMPLETED and result.calls == 2 - at
    assert len(saved(client, job)[2]) == 2


def test_timeout_requires_explicit_retry_of_only_the_failed_stage(migrated_client):
    client = migrated_client
    _, job = prepare(client)
    adapter = FakeProviderAdapter(
        script=[primary(), ProviderError(ProviderErrorKind.TIMEOUT, "timeout")]
    )
    assert run(client, job, adapter).state is JobState.NEEDS_RECONCILIATION
    assert run(client, job, adapter).calls == 0 and len(adapter.calls) == 2
    with client.app.state.session_factory() as session:
        reconcile_job(session, session.get(Job, job["id"]), action="retry")
        session.commit()
    resumed = FakeProviderAdapter(script=[fixed()])
    result = run(client, job, resumed)
    assert result.state is JobState.COMPLETED and result.calls == 1
    runs = saved(client, job)[2]
    assert len(runs) == 3 and runs[1]["usage"] == {}
    assert runs[1]["state"] is InferenceRunState.UNKNOWN_OUTCOME
    assert resumed.calls[0]["payload"]["roster_stage"] == "repair:0"
    assert resumed.calls[0]["payload"]["roster_generation"] == 1


@pytest.mark.parametrize("invalid", [False, True])
def test_unknown_usage_never_authorizes_an_additional_call(migrated_client, invalid):
    client = migrated_client
    _, job = prepare(client)
    adapter = FakeProviderAdapter(script=[primary(invalid=invalid, usage=None), fixed()])
    result = run(client, job, adapter)
    assert result.state is (JobState.NEEDS_RECONCILIATION if invalid else JobState.COMPLETED)
    assert result.calls == 1


def test_profile_output_ceiling_is_reserved_not_the_fallback_2000(migrated_client):
    client = migrated_client
    _, job = prepare(client, max_input_tokens=10000)
    update(client, job, snapshot={"params": {"max_tokens": 128000}})
    adapter = FakeProviderAdapter(script=[primary(invalid=False)])
    result = run(client, job, adapter)
    assert result.state is JobState.BUDGET_EXHAUSTED and not adapter.calls


def test_budget_can_be_raised_without_repeating_the_primary(migrated_client):
    client = migrated_client
    _, job = prepare(client)
    adapter = CallbackAdapter(
        [primary()], lambda: update(client, job, budget={"max_input_tokens": 2005})
    )
    assert run(client, job, adapter).state is JobState.BUDGET_EXHAUSTED
    update(client, job, budget={"max_input_tokens": None}, state=JobState.QUEUED)
    resumed = FakeProviderAdapter(script=[fixed()])
    result = run(client, job, resumed)
    assert result.state is JobState.COMPLETED and result.calls == 1


def test_changed_model_refuses_receipt_reuse_without_a_new_call(migrated_client):
    client = migrated_client
    _, job = prepare(client)
    adapter = CallbackAdapter([primary()], lambda: update(client, job, state=JobState.PAUSING))
    assert run(client, job, adapter).state is JobState.PAUSED
    update(client, job, state=JobState.QUEUED, snapshot={"params": {"max_tokens": 4000}})
    resumed = FakeProviderAdapter(script=[fixed()])
    result = run(client, job, resumed)
    assert result.state is JobState.FAILED and not resumed.calls


def test_same_process_duplicate_run_during_call_is_not_dispatched(migrated_client):
    client = migrated_client
    _, job = prepare(client)
    second = FakeProviderAdapter(script=[primary(invalid=False)])
    results = []
    adapter = CallbackAdapter(
        [primary(invalid=False)], lambda: results.append(run(client, job, second))
    )
    assert run(client, job, adapter).state is JobState.COMPLETED
    assert results[0].state is JobState.RUNNING and not second.calls


def test_dispatched_receipt_without_response_is_not_repeated_on_recovery(migrated_client):
    client = migrated_client
    _, job = prepare(client)
    adapter = CallbackAdapter([primary()], lambda: update(client, job, state=JobState.PAUSING))
    assert run(client, job, adapter).state is JobState.PAUSED
    with client.app.state.session_factory() as session:
        run_row = session.scalar(select(InferenceRun).where(InferenceRun.job_id == job["id"]))
        record = decode_archive(run_row.call_archive)
        run_row.call_archive = encode_archive(
            {**record, "phase": "prepared", "adapter_result": None}
        )
        run_row.state, run_row.usage_json = InferenceRunState.DISPATCHED, None
        session.commit()
    update(client, job, state=JobState.QUEUED)
    resumed = FakeProviderAdapter(script=[fixed()])
    assert run(client, job, resumed).state is JobState.NEEDS_RECONCILIATION
    assert not resumed.calls


def test_real_dispatcher_uses_same_pipeline_and_adapter_factory(migrated_client):
    client = migrated_client
    _, job = prepare(client)
    adapter = FakeProviderAdapter(script=[primary(), fixed()])
    result = run_job(
        client.app.state.session_factory,
        client.app.state.settings,
        job_id=job["id"],
        adapter_factory=lambda *_: adapter,
    )
    assert result.state is JobState.COMPLETED and result.calls == 2


def test_storage_failure_rolls_back_candidates_without_losing_call_charges(
    migrated_client, monkeypatch,
):
    from ndr.jobs import roster_pipeline as module

    client = migrated_client
    data, job = prepare(client)
    store = module.store_roster_candidates

    def fail_after_write(*args, **kwargs):
        store(*args, **kwargs)
        raise ValueError("isolated store failure")

    monkeypatch.setattr(module, "store_roster_candidates", fail_after_write)
    result = run(client, job, FakeProviderAdapter(script=[primary(), fixed()]))
    assert result.state is JobState.FAILED and result.calls == 2
    assert [r["usage"]["total_tokens"] for r in saved(client, job)[2]] == [5, 7]
    with client.app.state.session_factory() as session:
        assert not list(session.scalars(select(BookCharacter).where(
            BookCharacter.book_version_id == data["book_version_id"])))


def test_manual_profile_is_not_overwritten_by_repaired_roster(migrated_client):
    client = migrated_client
    data, job = prepare(client)
    with client.app.state.session_factory() as session:
        manual = BookCharacter(book_version_id=data["book_version_id"], canonical_name="林舟",
                               description="人工说明", aliases_json='["人工别名"]',
                               source="USER", user_confirmed=True, name_locked=True)
        session.add(manual)
        session.flush()
        person_id = manual.id
        session.commit()
    first = primary()
    first["characters"][0]["character_id"] = person_id
    result = run(client, job, FakeProviderAdapter(script=[first, fixed()]))
    assert result.state is JobState.COMPLETED
    with client.app.state.session_factory() as session:
        person_row = session.get(BookCharacter, person_id)
        assert person_row.canonical_name == "林舟" and person_row.description == "人工说明"
        assert json.loads(person_row.aliases_json) == ["人工别名"]
        assert person_row.user_confirmed and person_row.name_locked
        version = session.get(BookVersion, data["book_version_id"])
        assert all(not fact.accepted for fact in read_identity_records(person_row, version))


def test_changed_character_directory_refuses_resume_without_new_calls(migrated_client):
    client = migrated_client
    data, job = prepare(client)
    adapter = CallbackAdapter([primary()], lambda: update(client, job, state=JobState.PAUSING))
    assert run(client, job, adapter).state is JobState.PAUSED
    with client.app.state.session_factory() as session:
        session.add(BookCharacter(book_version_id=data["book_version_id"], canonical_name="门卫",
                                  source="USER", user_confirmed=True))
        session.commit()
    update(client, job, state=JobState.QUEUED)
    resumed = FakeProviderAdapter(script=[fixed()])
    assert run(client, job, resumed).state is JobState.FAILED and not resumed.calls


def test_unrelated_identity_blocks_and_duplicate_dependency_groups_are_repaired_separately(
    migrated_client,
):
    client = migrated_client
    _, job = prepare(client)
    first = primary()
    first["characters"] = [person("same", "林舟", "L2"),
                           person("same", "林舟", "L2"),
                           person("c2", "陆欣", "L3")]
    repaired = fixed(indices=[1, 2])
    repaired["repairs"][0]["characters"] = [person("c1", "林舟", "L2")]
    adapter = FakeProviderAdapter(script=[first, repaired])
    assert run(client, job, adapter).state is JobState.COMPLETED
    content = adapter.calls[1]["payload"]["messages"][1]["content"]
    task = json.loads(content.split("修复任务参数（JSON；数据）：\n", 1)[1].split("\n\n", 1)[0])
    assert task["groups"][0]["indices"] == [1, 2]
    assert [p["temp_ref"] for p in task["retained_characters"]] == ["c2"]


def test_received_provider_format_error_recovers_with_exact_feedback(migrated_client):
    client = migrated_client
    _, job = prepare(client)
    error = ProviderError(ProviderErrorKind.INVALID_OUTPUT, "独立格式错误",
                          details={"usage": {"total_tokens": 3}})
    adapter = FakeProviderAdapter(script=[error, primary(invalid=False)])
    assert run(client, job, adapter).state is JobState.COMPLETED
    assert [r["usage"]["total_tokens"] for r in saved(client, job)[2]] == [3, 5]
    assert "独立格式错误" in adapter.calls[1]["payload"]["messages"][-1]["content"]


@pytest.mark.parametrize("kind", [ProviderErrorKind.AUTH, ProviderErrorKind.RATE_LIMITED,
                                 ProviderErrorKind.UNAVAILABLE])
def test_nonformat_provider_failures_are_not_repeated_as_identity_repairs(migrated_client, kind):
    client = migrated_client
    _, job = prepare(client)
    adapter = FakeProviderAdapter(script=[ProviderError(kind, "provider failure")])
    result = run(client, job, adapter)
    assert result.state is JobState.FAILED and result.calls == 1


def test_finite_budget_retains_unknown_attempt_reserves_on_explicit_retry(migrated_client):
    client = migrated_client
    _, job = prepare(client)
    adapter = FakeProviderAdapter(script=[primary(), ProviderError(ProviderErrorKind.TIMEOUT, "timeout")])
    assert run(client, job, adapter).state is JobState.NEEDS_RECONCILIATION
    unknown_reserve = saved(client, job)[2][1]["archive"]["request"]["reserve_tokens"]
    update(client, job, budget={"max_input_tokens": unknown_reserve + 5})
    with client.app.state.session_factory() as session:
        reconcile_job(session, session.get(Job, job["id"]), action="retry")
        session.commit()
    resumed = FakeProviderAdapter(script=[fixed()])
    result = run(client, job, resumed)
    assert result.state is JobState.BUDGET_EXHAUSTED and not resumed.calls
    assert saved(client, job)[2][1]["usage"] == {}


@pytest.mark.parametrize("budget", [{"max_input_tokens": -1}, {"max_input_tokens": True},
                                  {"max_roster_repairs": 6}, {"max_format_retries": "1"}])
def test_invalid_frozen_limits_fail_before_a_paid_call(migrated_client, budget):
    client = migrated_client
    _, job = prepare(client, **budget)
    adapter = FakeProviderAdapter(script=[primary()])
    assert run(client, job, adapter).state is JobState.FAILED and not adapter.calls
