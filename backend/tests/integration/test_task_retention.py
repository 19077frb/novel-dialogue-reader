"""Completed execution retention preserves results, usage, and recovery evidence."""

import json

import pytest
from sqlalchemy import select
from tests.integration.test_character_directory import _auto_job, _merge_group
from tests.integration.test_character_directory import populated as populated

from ndr.characters.auto_merge import run_auto_merge_job
from ndr.domain.enums import InferenceRunState, JobKind, JobState
from ndr.llm.adapters.fake import FakeProviderAdapter
from ndr.storage.models import InferenceRun, Job, JobWindow
from ndr.storage.retention_policy import (
    apply_completed_job_retention,
    plan_completed_job_retention,
)
from ndr.storage.run_archive import encode_archive
from ndr.storage.transactions import transaction


@pytest.mark.parametrize("keep", [False, True])
def test_completed_merge_preserves_proposal_and_prunes_only_success_details(
    migrated_client, populated, keep,
):
    client, ids = migrated_client, populated
    client.app.state.settings.retain_completed_call_details = keep
    job, _ = _auto_job(client, ids)
    adapter = FakeProviderAdapter(script=[{"groups": [_merge_group(ids)]}], usage={"total_tokens": 40})
    state, calls = run_auto_merge_job(
        client.app.state.session_factory, client.app.state.settings,
        job_id=job["id"], adapter_factory=lambda *_: adapter,
    )
    assert state is JobState.COMPLETED and calls == 1
    with client.app.state.session_factory() as session:
        current = session.get(Job, job["id"])
        assert json.loads(current.checkpoint_json)["merge_proposal"]["groups"]
        attempt = session.scalar(select(InferenceRun).where(InferenceRun.job_id == current.id))
        assert (attempt.call_archive is not None) is keep
        assert json.loads(attempt.usage_json)["total_tokens"] == 40
        assert attempt.request_fingerprint and attempt.profile_snapshot_json
    base = f"/api/books/{ids['book']}/character-directory/auto-merge/{job['id']}"
    preview = client.get(base)
    assert preview.status_code == 200, preview.text
    result = preview.json()["data"]
    assert result["phase"] == "awaiting_confirmation"
    assert result["usage"]["total_tokens"] == 40
    assert result["proposals"][0]["target"]["character_id"] == ids["target"]
    confirmed = client.post(f"{base}/confirm", json={"selected_target_ids": [ids["target"]]})
    assert confirmed.status_code == 200, confirmed.text
    result = confirmed.json()["data"]
    assert result["phase"] == "applied" and result["merged_count"] == 1
    assert result["usage"]["total_tokens"] == 40
    assert len(adapter.calls) == 1
    with client.app.state.session_factory() as session:
        attempts = list(session.scalars(select(InferenceRun).where(InferenceRun.job_id == job["id"])))
        assert len(attempts) == 1
        assert (attempts[0].call_archive is not None) is keep


@pytest.mark.parametrize("state", list(JobState))
def test_retention_only_changes_completed_execution_fields(migrated_client, state):
    factory = migrated_client.app.state.session_factory
    checkpoint = {"scene_state": {"large": "synthetic" * 100},
                  "expression_reviews": {"why": "preserve"}, "auxiliary_warnings": ["preserve"],
                  "merge_proposal": {"groups": []}, "dialogue_receipts": {"w": ["known"]}}
    with transaction(factory) as session:
        job = Job(kind=JobKind.INFERENCE, state=state, checkpoint_json=json.dumps(checkpoint))
        session.add(job)
        session.flush()
        session.add(JobWindow(job_id=job.id, window_id="w", state=JobState.COMPLETED))
        runs = []
        for run_state, usage in [(InferenceRunState.SUCCEEDED, '{"total_tokens":40}'),
                                 (InferenceRunState.SUCCEEDED, None),
                                 (InferenceRunState.FAILED, '{"total_tokens":40}'),
                                 (InferenceRunState.UNKNOWN_OUTCOME, None)]:
            attempt = InferenceRun(
                job_id=job.id, window_id="w", state=run_state, usage_json=usage,
                profile_snapshot_json="{}", request_fingerprint="frozen",
                call_archive=encode_archive({"phase": "returned", "adapter_result": {}}),
            )
            session.add(attempt)
            runs.append(attempt)
        session.flush()
        before_job = job.updated_at
        before_runs = [attempt.updated_at for attempt in runs]
        plan = apply_completed_job_retention(session, job)
        session.flush()
        session.expire_all()
        assert job.updated_at == before_job
        assert [attempt.updated_at for attempt in runs] == before_runs
        assert [attempt.usage_json for attempt in runs] == [
            '{"total_tokens":40}', None, '{"total_tokens":40}', None,
        ]
        if state is JobState.COMPLETED:
            assert plan["archive_run_ids"] == [runs[0].id]
            assert plan["checkpoint_bytes"] > 0 and plan["archive_bytes"] > 0
            assert json.loads(job.checkpoint_json) == {
                key: value for key, value in checkpoint.items() if key != "scene_state"
            }
            assert runs[0].call_archive is None
            assert all(attempt.call_archive for attempt in runs[1:])
        else:
            assert not plan["drop_scene_state"] and not plan["archive_run_ids"]
            assert json.loads(job.checkpoint_json) == checkpoint
            assert all(attempt.call_archive for attempt in runs)


@pytest.mark.parametrize("pending", ["window", "dispatch", "receipt"])
def test_completed_flag_cannot_prune_pending_local_work(migrated_client, pending):
    with transaction(migrated_client.app.state.session_factory) as session:
        job = Job(kind=JobKind.INFERENCE, state=JobState.COMPLETED,
                  checkpoint_json=json.dumps({"scene_state": {}, "dialogue_receipts":
                                               {"missing": []} if pending == "receipt" else {}}))
        session.add(job)
        session.flush()
        session.add(JobWindow(job_id=job.id, window_id="w", state=
                              JobState.RUNNING if pending == "window" else JobState.COMPLETED))
        if pending == "dispatch":
            session.add(InferenceRun(job_id=job.id, state=InferenceRunState.DISPATCHED,
                                     profile_snapshot_json="{}", request_fingerprint="frozen"))
        session.flush()
        assert not plan_completed_job_retention(session, job)["drop_scene_state"]

@pytest.mark.parametrize("mode", ["initial", "reread"])
def test_projected_checkpoint_pause_resume_rebuilds_same_visible_identity_facts(
    fake_provider_client, monkeypatch, mode,
):
    from dataclasses import replace

    from integration.test_expression_pipeline import StageAdapter, prepare
    from ndr.characters.facts import CharacterIdentityFact
    from ndr.context.budget import BOUNDED_BLOCK_POLICY
    from ndr.jobs import scheduler
    from ndr.storage.models import BookCharacter, BookVersion, Chapter, Quote

    client = fake_provider_client
    create, factory = prepare(
        client, all_quotes=True, context_policy="context-chapter-3",
        text="第一章\n林舟说：「早上好。」\n他走到窗边。\n许晴答：「你好。」\n顾宁点头。",
    )
    job_id = create(f"checkpoint-resume-{mode}")["id"]
    with factory() as session:
        job = session.get(Job, job_id)
        book_id, version_id = job.book_id, job.book_version_id
        chapter = session.scalar(select(Chapter).where(Chapter.book_version_id == version_id))
        chapter_id = chapter.id
    response = client.put(f"/api/books/{book_id}/chapters/{chapter_id}/character-roster", json={
        "candidates": [{"temp_ref": "p1", "canonical_name": "林舟", "description": "已知资料"}],
        "pov_temp_ref": "p1", "expected_version": 1,
    })
    assert response.status_code == 200, response.text
    person_id = response.json()["data"]["pov_character_id"]
    with transaction(factory) as session:
        version = session.get(BookVersion, version_id)
        person = session.get(BookCharacter, person_id)
        facts = [CharacterIdentityFact(
            kind=kind, value=value, visible_from_cp=cp,
            canonical_sha256=version.canonical_sha256, source="user", source_ref="fixture",
            accepted=True,
        ).model_dump(mode="json") for kind, value, cp in [
            ("name", "林舟", 0), ("description", "已知资料", 0),
            ("description", "后文秘密资料", version.canonical_length_cp),
        ]]
        person.identity_facts_json = json.dumps(facts, ensure_ascii=False)
        job = session.get(Job, job_id)
        scope = json.loads(job.range_json)
        scope.update(chapter_id=chapter_id, reading_mode=mode)
        if mode == "initial":
            scope["visible_horizon_cp"] = session.scalar(select(Quote).where(
                Quote.book_version_id == version_id,
            ).order_by(Quote.start_cp.desc())).end_cp
        job.range_json = json.dumps(scope)
        job.budget_json = json.dumps({"max_recheck_rounds": 0, "max_format_retries": 0})
    apply = scheduler._apply_payload
    observed = []

    def pause_after_first_apply(session, **kwargs):
        state = kwargs["state"]
        assert state.projected_identity_input
        profile = next(person for person in state.confirmed_characters if person.character_id == person_id)
        observed.append(profile.as_dict())
        result = apply(session, **kwargs)
        if len(observed) == 1:
            session.get(Job, job_id).state = JobState.PAUSING
        return result

    monkeypatch.setattr(scheduler, "_apply_payload", pause_after_first_apply)
    policy = replace(BOUNDED_BLOCK_POLICY, dialogue_target_limit=1)
    adapter = StageAdapter(["林舟"])
    outcome = scheduler.run_job(factory, client.app.state.settings, job_id=job_id,
                                policy=policy, adapter_factory=lambda *_: adapter)
    assert outcome.state is JobState.PAUSED, outcome.errors
    with transaction(factory) as session:
        job = session.get(Job, job_id)
        checkpoint = json.loads(job.checkpoint_json)
        assert checkpoint["windows_done"] == 1
        saved = checkpoint["scene_state"]["confirmed_characters"][0]
        assert saved["character_id"] == person_id
        assert "identity_records" not in saved and "relations" not in saved
        job.state = JobState.QUEUED
    outcome = scheduler.run_job(factory, client.app.state.settings, job_id=job_id,
                                policy=policy, adapter_factory=lambda *_: adapter)
    assert outcome.state is JobState.COMPLETED, outcome.errors
    assert len(observed) == len(adapter.calls) == 2
    assert observed[0] == observed[1]
    assert bool("后文秘密资料" in json.dumps(observed[1], ensure_ascii=False)) is (mode == "reread")
    assert observed[1]["identity_records"]
    with factory() as session:
        checkpoint = json.loads(session.get(Job, job_id).checkpoint_json)
        assert "scene_state" not in checkpoint
        assert checkpoint["windows_done"] == 2

@pytest.mark.parametrize("pipeline", [False, True])
def test_completed_roster_prunes_success_archive_and_keeps_proposal_and_usage(
    migrated_client, pipeline,
):
    from tests.integration.test_roster_pipeline import update
    from tests.integration.test_sourced_roster_jobs import prepare, response

    from ndr.jobs.roster import run_character_roster_job
    from ndr.storage.models import BookCharacter, ChapterCharacterRoster

    client = migrated_client
    client.app.state.settings.retain_completed_call_details = False
    data, job = prepare(client)
    if pipeline:
        update(client, job)
    adapter = FakeProviderAdapter(script=[response()])
    factory, settings = client.app.state.session_factory, client.app.state.settings
    outcome = run_character_roster_job(factory, settings, job_id=job["id"],
                                      adapter_factory=lambda *_: adapter)
    assert outcome.state is JobState.COMPLETED and outcome.calls == 1
    with factory() as session:
        current = session.get(Job, job["id"])
        checkpoint = json.loads(current.checkpoint_json)
        assert checkpoint["candidate_count"] == 1
        attempts = list(session.scalars(select(InferenceRun).where(InferenceRun.job_id == job["id"])))
        assert len(attempts) == 1
        assert attempts[0].state is InferenceRunState.SUCCEEDED
        assert attempts[0].call_archive is None
        assert json.loads(attempts[0].usage_json)["total_tokens"] == 19
        assert attempts[0].request_fingerprint and attempts[0].profile_snapshot_json
        roster = session.scalar(select(ChapterCharacterRoster).where(
            ChapterCharacterRoster.analysis_job_id == job["id"],
        ))
        assert len(json.loads(roster.candidates_json)) == 1
        assert len(list(session.scalars(select(BookCharacter).where(
            BookCharacter.book_version_id == data["book_version_id"],
        )))) == 1
    repeated = run_character_roster_job(factory, settings, job_id=job["id"],
                                       adapter_factory=lambda *_: pytest.fail("Completed roster re-dispatched"))
    assert repeated.state is JobState.COMPLETED and repeated.calls == 0
    assert len(adapter.calls) == 1
