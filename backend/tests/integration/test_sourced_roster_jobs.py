import json
from copy import deepcopy

import pytest
from sqlalchemy import select

from ndr.characters.facts import read_identity_records, visible_identity_profile
from ndr.domain.enums import InferenceRunState, JobState
from ndr.jobs.roster import run_character_roster_job
from ndr.llm.adapters.fake import FakeProviderAdapter
from ndr.storage.models import BookCharacter, BookVersion, InferenceRun, Job
from ndr.storage.transactions import transaction


def prepare(client, text="第一章\n林舟说：「早上好。」"):
    data = client.post("/api/books/import", files={
        "file": ("sourced.txt", text.encode(), "text/plain"),
    }).json()["data"]
    chapter = client.get(f"/api/books/{data['book_id']}/chapters").json()["data"][0]
    profile = client.post("/api/model-profiles", json={
        "name": "isolated", "protocol": "fake-provider", "base_url": "http://127.0.0.1:1",
        "model": "fake", "credential_mode": "none",
    }).json()["data"]
    path = f"/api/books/{data['book_id']}/chapters/{chapter['id']}/character-roster/analyze"
    result = client.post(path, json={"profile_id": profile["id"], "idempotency_key": "sourced",
                                    "run_now": False})
    assert result.status_code == 202, result.text
    return data, result.json()["data"]


def response(version="1.1"):
    person = {"temp_ref": "c1", "name": "林舟", "evidence_refs": ["L2"],
              "pov_candidate": True}
    if version == "1.1":
        person.update(facts=[{"kind": "name", "value": "林舟", "evidence_refs": ["L2"]}],
                      pov_evidence_refs=["L2"])
    return {"schema_version": version, "characters": [person], "_usage": {"total_tokens": 19}}


def run(client, job, payload):
    adapter = FakeProviderAdapter(script=[deepcopy(payload)])
    result = run_character_roster_job(client.app.state.session_factory, client.app.state.settings,
                                    job_id=job["id"], adapter_factory=lambda *_: adapter)
    return result, adapter


def test_new_job_writes_original_bound_model_facts_and_old_job_stays_legacy(migrated_client):
    client = migrated_client
    data, job = prepare(client)
    with client.app.state.session_factory() as session:
        assert json.loads(session.get(Job, job["id"]).range_json)["roster_protocol"] == "sourced-roster-2"
    result, adapter = run(client, job, response())
    assert result.state == JobState.COMPLETED
    assert adapter.calls[0]["payload"]["roster_protocol"] == "sourced-roster-2"
    with client.app.state.session_factory() as session:
        from ndr.storage.run_archive import decode_archive
        saved_run = session.scalar(select(InferenceRun).where(InferenceRun.job_id == job["id"]))
        archive = decode_archive(saved_run.call_archive)
        assert archive["request"] == adapter.calls[0]["payload"]
        assert archive["adapter_result"]["characters"] == response()["characters"]
        person = session.scalar(select(BookCharacter).where(
            BookCharacter.book_version_id == data["book_version_id"],
        ))
        version = session.get(BookVersion, data["book_version_id"])
        records = read_identity_records(person, version)
        assert len(records) == 1 and records[0].source == "model" and records[0].accepted
        assert records[0].source_ref and not person.user_confirmed
        assert visible_identity_profile(person, version, horizon=0)["name"] is None
        assert visible_identity_profile(person, version, horizon=version.canonical_length_cp)["name"] == "林舟"


def test_legacy_queued_job_accepts_original_protocol_without_inventing_facts(migrated_client):
    client = migrated_client
    data, job = prepare(client)
    with transaction(client.app.state.session_factory) as session:
        stored = session.get(Job, job["id"])
        value = json.loads(stored.range_json)
        value.pop("roster_protocol")
        stored.range_json = json.dumps(value)
    result, adapter = run(client, job, response("1.0"))
    assert result.state == JobState.COMPLETED
    assert adapter.calls[0]["payload"]["roster_protocol"] == "legacy-roster-1"
    with client.app.state.session_factory() as session:
        person = session.scalar(select(BookCharacter).where(
            BookCharacter.book_version_id == data["book_version_id"],
        ))
        assert person.identity_facts_json == "[]"


def test_equal_anonymous_designations_are_not_implicitly_one_identity(migrated_client):
    client = migrated_client
    data, job = prepare(client, "第一章\n第一位女同学说：「你好。」\n第二位女同学说：「再见。」")
    payload = {"schema_version": "1.1", "characters": [
        {"temp_ref": f"c{i}", "name": "女同学", "evidence_refs": [f"L{i+1}"],
         "facts": [{"kind": "designation", "value": "女同学", "evidence_refs": [f"L{i+1}"]}]}
        for i in (1, 2)
    ]}
    result, _ = run(client, job, payload)
    assert result.state == JobState.COMPLETED
    with client.app.state.session_factory() as session:
        people = list(session.scalars(select(BookCharacter).where(
            BookCharacter.book_version_id == data["book_version_id"],
        )))
        assert len(people) == 2 and len({p.id for p in people}) == 2
        assert all(p.canonical_name == "女同学" for p in people)


@pytest.mark.parametrize("protected", [False, True])
@pytest.mark.parametrize("named", [False, True])
def test_missing_role_id_reuses_original_request_context_without_duplicate_or_manual_overwrite(
    migrated_client, protected, named,
):
    client = migrated_client
    data, first = prepare(client, "第一章\n林舟打工的书店店长刘禾说：「你好。」")
    manager = {"temp_ref": "manager", "name": "店长", "evidence_refs": ["L2"],
               "description": "林舟打工的书店店长", "facts": [
                   {"kind": "designation", "value": "店长", "evidence_refs": ["L2"]},
                   {"kind": "description", "value": "林舟打工的书店店长",
                    "evidence_refs": ["L2"]},
               ]}
    initial = response()
    initial_manager = deepcopy(manager)
    if named:
        initial_manager.update(name="刘禾", real_name="刘禾")
        initial_manager["facts"].append({"kind": "name", "value": "刘禾",
                                         "evidence_refs": ["L2"]})
    initial["characters"].append(initial_manager)
    assert run(client, first, initial)[0].state == JobState.COMPLETED
    with transaction(client.app.state.session_factory) as session:
        profile_id = json.loads(session.get(Job, first["id"]).profile_snapshot_json)["profile_id"]
        people = list(session.scalars(select(BookCharacter).where(
            BookCharacter.book_version_id == data["book_version_id"],
        )))
        expected_name = "刘禾" if named else "店长"
        person = next(p for p in people if p.canonical_name == expected_name)
        manager_id = person.id
        if protected:
            person.user_confirmed = True
            person.name_locked = True
            person.description = "人工说明，不能被分析覆盖"
    chapter = client.get(f"/api/books/{data['book_id']}/chapters").json()["data"][0]
    second = client.post(
        f"/api/books/{data['book_id']}/chapters/{chapter['id']}/character-roster/analyze",
        json={"profile_id": profile_id,
              "idempotency_key": "second-role", "run_now": False,
              "allow_overwrite_manual": named},
    )
    assert second.status_code == 202, second.text
    result, adapter = run(client, second.json()["data"], {
        "schema_version": "1.1", "characters": [deepcopy(manager)], "_usage": {"total_tokens": 7},
    })
    assert result.state == JobState.COMPLETED and len(adapter.calls) == 1
    with client.app.state.session_factory() as session:
        people = list(session.scalars(select(BookCharacter).where(
            BookCharacter.book_version_id == data["book_version_id"],
        )))
        assert len(people) == 2
        person = next(p for p in people if p.id == manager_id)
        assert person.canonical_name == expected_name and person.user_confirmed is protected
        if protected:
            assert person.description == "人工说明，不能被分析覆盖"
        roster = client.get(
            f"/api/books/{data['book_id']}/chapters/{chapter['id']}/character-roster",
        ).json()["data"]
        assert roster["candidates"][0]["character_id"] == manager_id
    if not protected:
        # Replaying the original receipt must not substitute today's changed directory.
        with transaction(client.app.state.session_factory) as session:
            session.get(Job, second.json()["data"]["id"]).state = JobState.FAILED
            person = session.get(BookCharacter, manager_id)
            person.identity_facts_json = "[]"
            person.presentation_history_json = json.dumps([
                {"cp": 0, "name": "店长", "description": "林舟打工的餐厅店长"},
            ])

        def forbid_provider(*args):
            pytest.fail("Saved-result revalidation must not issue another model request")

        restored = run_character_roster_job(
            client.app.state.session_factory, client.app.state.settings,
            job_id=second.json()["data"]["id"], adapter_factory=forbid_provider,
        )
        assert restored.state == JobState.COMPLETED and restored.calls == 0
        with client.app.state.session_factory() as session:
            assert len(list(session.scalars(select(BookCharacter).where(
                BookCharacter.book_version_id == data["book_version_id"],
            )))) == 2


def test_invalid_original_preparation_does_not_dispatch_or_leave_running(migrated_client):
    client = migrated_client
    data, job = prepare(client)
    with transaction(client.app.state.session_factory) as session:
        session.get(BookVersion, data["book_version_id"]).canonical_sha256 = "0" * 64
    result, adapter = run(client, job, response())
    assert result.state == JobState.FAILED and not adapter.calls
    with client.app.state.session_factory() as session:
        assert session.get(Job, job["id"]).state == JobState.FAILED
        assert not list(session.scalars(select(InferenceRun).where(InferenceRun.job_id == job["id"])))


def test_charged_storage_failure_rolls_back_all_candidates_and_terminates_job(
    migrated_client, monkeypatch,
):
    from ndr.jobs import roster as module

    client = migrated_client
    data, job = prepare(client)
    original_store = module.store_roster_candidates

    def fail_after_write(*args, **kwargs):
        original_store(*args, **kwargs)
        raise ValueError("isolated storage fault")

    monkeypatch.setattr(module, "store_roster_candidates", fail_after_write)
    result, _ = run(client, job, response())
    assert result.state == JobState.FAILED
    with client.app.state.session_factory() as session:
        assert not list(session.scalars(select(BookCharacter).where(
            BookCharacter.book_version_id == data["book_version_id"],
        )))
        stored = session.get(Job, job["id"])
        attempt = session.scalar(select(InferenceRun).where(InferenceRun.job_id == job["id"]))
        assert stored.state == JobState.FAILED and attempt.state == InferenceRunState.FAILED
        assert json.loads(attempt.usage_json)["total_tokens"] == 19
        assert attempt.error_code == "ROSTER_STORAGE_FAILED"


@pytest.mark.parametrize("change", ["old_output", "bad_line"])
def test_new_protocol_failure_keeps_charges_and_no_partial_person(migrated_client, change):
    client = migrated_client
    data, job = prepare(client)
    payload = response("1.0" if change == "old_output" else "1.1")
    if change == "bad_line":
        payload["characters"][0]["facts"][0]["evidence_refs"] = ["L99"]
    result, adapter = run(client, job, payload)
    assert result.state == JobState.FAILED
    with client.app.state.session_factory() as session:
        assert not list(session.scalars(select(BookCharacter).where(
            BookCharacter.book_version_id == data["book_version_id"],
        )))
        attempt = session.scalar(select(InferenceRun).where(InferenceRun.job_id == job["id"]))
        assert json.loads(attempt.usage_json)["total_tokens"] == 19
        from ndr.storage.run_archive import decode_archive
        archive = decode_archive(attempt.call_archive)
        assert archive["request"] == adapter.calls[0]["payload"]
        assert archive["adapter_result"] == payload
        assert archive["phase"] == "returned"


@pytest.mark.parametrize("protocol", ["sourced-roster-1", "sourced-roster-2"])
def test_actual_job_freezes_strict_or_isolated_semantics_and_preserves_diagnostics(
    migrated_client, protocol,
):
    client = migrated_client
    data, job = prepare(client)
    with transaction(client.app.state.session_factory) as session:
        stored = session.get(Job, job["id"])
        value = json.loads(stored.range_json)
        value["roster_protocol"] = protocol
        stored.range_json = json.dumps(value)
    payload = response()
    person = payload["characters"][0]
    person["description"] = "无原文说明"
    person["facts"].append({"kind": "description", "value": "无原文说明",
                            "evidence_refs": ["L999"]})
    bad = deepcopy(person)
    bad.update(temp_ref="c2", name="错误姓名")
    payload["characters"].append(bad)
    result, adapter = run(client, job, payload)
    assert adapter.calls[0]["payload"]["roster_protocol"] == protocol
    assert result.state == (JobState.COMPLETED if protocol.endswith("2") else JobState.FAILED)
    with client.app.state.session_factory() as session:
        people = list(session.scalars(select(BookCharacter).where(
            BookCharacter.book_version_id == data["book_version_id"],
        )))
        attempt = session.scalar(select(InferenceRun).where(InferenceRun.job_id == job["id"]))
        assert json.loads(attempt.usage_json)["total_tokens"] == 19
        if protocol.endswith("2"):
            assert len(people) == 1 and people[0].canonical_name == "林舟"
            assert people[0].description == ""
            diagnostic = json.loads(session.get(Job, job["id"]).progress_json)["proposal_diagnostics"]
            assert diagnostic["isolated_characters"] == 1
            assert diagnostic["discarded_auxiliary_facts"] == 2
        else:
            assert not people


@pytest.mark.parametrize("legacy", [False, True])
def test_roster_request_uses_chapter_visible_identity_not_future_book_fields(migrated_client, legacy):
    client = migrated_client
    data, job = prepare(client, "第一章\n林舟说：「早上好。」\n第二章\n少女自称未来姓名。")
    with transaction(client.app.state.session_factory) as session:
        version = session.get(BookVersion, data["book_version_id"])
        records = [{"kind": kind, "value": value, "visible_from_cp": cp,
                    "canonical_sha256": version.canonical_sha256, "source": "user",
                    "source_ref": "manual", "accepted": True}
                   for kind, value, cp in [("designation", "少女", 0),
                                           ("name", "未来姓名", version.canonical_length_cp),
                                           ("alias", "未来别名", version.canonical_length_cp),
                                           ("description", "未来说明", version.canonical_length_cp)]]
        person = BookCharacter(book_version_id=version.id, canonical_name="未来姓名",
                               description="未来说明", aliases_json='["未来别名"]',
                               source="MODEL", user_confirmed=False,
                               identity_facts_json=json.dumps(records, ensure_ascii=False))
        session.add(person)
        session.flush()
        person_id = person.id
        if legacy:
            stored = session.get(Job, job["id"])
            value = json.loads(stored.range_json)
            value.pop("identity_input_version")
            stored.range_json = json.dumps(value)
    result, adapter = run(client, job, response())
    assert result.state is JobState.COMPLETED, result.errors
    sent = json.dumps(adapter.calls[0]["payload"]["messages"], ensure_ascii=False)
    assert person_id in sent
    if legacy:
        assert all(text in sent for text in ("未来姓名", "未来别名", "未来说明"))
    else:
        assert all(text not in sent for text in ("未来姓名", "未来别名", "未来说明"))
        assert "少女" in sent
