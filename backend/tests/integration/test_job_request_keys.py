"""Coalesced creation requests retain recoverable, globally unique receipts."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from alembic import command
from sqlalchemy import delete, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tests.integration.test_jobs import SAMPLE, _create_job, _fake_profile, _import
from tests.integration.test_roster_replacement import restart
from tests.integration.test_sourced_roster_jobs import prepare

from ndr.api.errors import ApiError
from ndr.domain.enums import JobKind, JobPurpose, JobState, ReadingMode
from ndr.jobs.request_keys import bind_request_key, find_request_job
from ndr.jobs.scheduler import run_job
from ndr.jobs.service import create_inference_job
from ndr.llm.adapters.fake import FakeProviderAdapter
from ndr.storage.base import Base
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.migrate import build_alembic_config, run_migrations
from ndr.storage.models import Book, BookVersion, Job, JobRequestKey, ModelProfile
from ndr.storage.transactions import admission_transaction


def test_forced_request_alias_recovers_lost_receipt_after_completion_without_new_call(
    fake_provider_client,
):
    client = fake_provider_client
    data = _import(client)
    profile = _fake_profile(client)
    original = _create_job(client, data["book_id"], profile, key="original", force_reprocess=True)
    # The caller loses this response. Only its own fresh key survives a refresh.
    _create_job(client, data["book_id"], profile, key="lost-response", force_reprocess=True)
    recent = client.get("/api/jobs/recent", params={
        "book_id": data["book_id"], "idempotency_key": "lost-response",
    })
    assert recent.status_code == 200, recent.text
    assert [job["id"] for job in recent.json()["data"]] == [original["id"]]
    factory, settings = client.app.state.session_factory, client.app.state.settings
    adapter = FakeProviderAdapter()
    outcome = run_job(factory, settings, job_id=original["id"], adapter_factory=lambda *_: adapter)
    assert outcome.state is JobState.COMPLETED and len(adapter.calls) == 1
    repeated = _create_job(client, data["book_id"], profile, key="lost-response",
                           force_reprocess=True, run_now=True)
    assert repeated["id"] == original["id"] and repeated["state"] == "COMPLETED"
    again = run_job(factory, settings, job_id=repeated["id"], adapter_factory=lambda *_: adapter)
    assert again.calls == 0 and len(adapter.calls) == 1
    with factory() as session:
        job = session.get(Job, original["id"])
        assert job.idempotency_key == "original"
        assert session.scalar(select(JobRequestKey.job_id).where(
            JobRequestKey.idempotency_key == "lost-response")) == job.id
        assert len(list(session.scalars(select(Job).where(Job.kind == JobKind.INFERENCE)))) == 1


def test_alias_conflicts_and_cross_kind_namespace_are_preserved(fake_provider_client):
    client = fake_provider_client
    data = _import(client)
    profile = _fake_profile(client)
    _create_job(client, data["book_id"], profile, key="original")
    _create_job(client, data["book_id"], profile, key="alias")
    payload = {
        "book_id": data["book_id"], "profile_id": profile, "run_now": False,
        "range": {"start_cp": 0, "end_cp": 10}, "idempotency_key": "alias",
    }
    conflict = client.post("/api/jobs", json=payload)
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    chapter = client.get(f"/api/books/{data['book_id']}/chapters").json()["data"][0]
    roster = client.post(
        f"/api/books/{data['book_id']}/chapters/{chapter['id']}/character-roster/analyze",
        json={"profile_id": profile, "run_now": False, "idempotency_key": "alias"},
    )
    assert roster.status_code == 409, roster.text
    merge = client.post(f"/api/books/{data['book_id']}/character-directory/auto-merge",
                        json={"profile_id": profile, "run_now": False,
                              "book_version_id": data["book_version_id"],
                              "idempotency_key": "alias"})
    assert merge.status_code == 409, merge.text


def test_roster_alias_keeps_requested_digest_and_terminal_receipt(migrated_client):
    client = migrated_client
    data, original = prepare(client)
    adopted = restart(client, data, original)
    assert adopted.status_code == 202 and adopted.json()["data"]["id"] == original["id"]
    factory = client.app.state.session_factory
    with factory() as session:
        stored = session.get(Job, original["id"])
        alias = session.get(JobRequestKey, "new-start")
        assert alias.request_digest != stored.request_digest
        stored.state = JobState.COMPLETED
        session.commit()
    repeated = restart(client, data, original)
    assert repeated.status_code == 202 and repeated.json()["data"]["id"] == original["id"]
    assert restart(client, data, original, max_input_tokens=45678).status_code == 409
    recent = client.get("/api/jobs/recent", params={
        "book_id": data["book_id"], "kind": "CHARACTER_ROSTER", "idempotency_key": "new-start",
    })
    assert recent.status_code == 200 and [job["id"] for job in recent.json()["data"]] == [original["id"]]


def test_concurrent_admissions_register_every_alias_on_one_task(fake_provider_client):
    client = fake_provider_client
    data, profile = _import(client), _fake_profile(client)
    factory = client.app.state.session_factory

    def admit(key):
        with admission_transaction(factory) as session:
            job, _ = create_inference_job(
                session, book=session.get(Book, data["book_id"]),
                version=session.get(BookVersion, data["book_version_id"]),
                profile=session.get(ModelProfile, profile), purpose=JobPurpose.PROCESS,
                range_payload={"start_cp": 0, "end_cp": len(SAMPLE)}, budget={},
                idempotency_key=key, reading_mode=ReadingMode.INITIAL, visible_horizon_cp=None,
            )
            return job.id

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(admit, ["parallel-1", "parallel-2", "parallel-3", "parallel-2"]))
    assert len(set(ids)) == 1
    with factory() as session:
        receipts = list(session.scalars(select(JobRequestKey).where(JobRequestKey.job_id == ids[0])))
        assert {row.idempotency_key for row in receipts} == {"parallel-1", "parallel-2", "parallel-3"}


def test_alias_admission_rolls_back_with_outer_transaction(fake_provider_client):
    client = fake_provider_client
    data, profile = _import(client), _fake_profile(client)
    original = _create_job(client, data["book_id"], profile, key="original")
    factory = client.app.state.session_factory
    with (pytest.raises(RuntimeError, match="rollback admission"),
          admission_transaction(factory) as session):
        job = session.get(Job, original["id"])
        bind_request_key(session, job, "uncommitted", job.request_digest)
        raise RuntimeError("rollback admission")
    with factory() as session:
        assert session.get(JobRequestKey, "uncommitted") is None
        assert session.get(JobRequestKey, "original").job_id == original["id"]


@pytest.mark.parametrize("deletion", ["job", "book"])
def test_request_keys_cascade_and_use_fk_index(fake_provider_client, deletion):
    client = fake_provider_client
    data, profile = _import(client), _fake_profile(client)
    original = _create_job(client, data["book_id"], profile, key="original")
    _create_job(client, data["book_id"], profile, key="alias")
    factory = client.app.state.session_factory
    with admission_transaction(factory) as session:
        plan = str(session.execute(text(
            "EXPLAIN QUERY PLAN SELECT idempotency_key FROM job_request_keys WHERE job_id=:job"
        ), {"job": original["id"]}).all())
        assert "ix_job_request_keys_job_id" in plan
        key_plan = str(session.execute(text(
            "EXPLAIN QUERY PLAN SELECT job_id FROM job_request_keys WHERE idempotency_key='alias'"
        )).all())
        assert "SEARCH job_request_keys" in key_plan and "SCAN job_request_keys" not in key_plan
        if deletion == "job":
            session.execute(delete(Job).where(Job.id == original["id"]))
        else:
            session.get(Job, original["id"]).state = JobState.COMPLETED
    if deletion == "book":
        deleted = client.delete(f"/api/books/{data['book_id']}")
        assert deleted.status_code == 204, deleted.text
    with factory() as session:
        assert not list(session.scalars(select(JobRequestKey)))


@pytest.mark.parametrize("steal", ["insert", "update", "same_job_alias"])
def test_legacy_original_key_writes_cannot_take_registered_alias(migrated_engine, steal):
    with Session(migrated_engine) as session:
        original = Job(kind=JobKind.INFERENCE, state=JobState.QUEUED,
                       idempotency_key="original", request_digest="a" * 64)
        other = Job(kind=JobKind.CHARACTER_ROSTER, state=JobState.QUEUED,
                    idempotency_key="other", request_digest="b" * 64)
        session.add_all([original, other])
        session.flush()
        bind_request_key(session, original, "alias", "c" * 64)
        session.commit()
        with pytest.raises(IntegrityError):
            if steal == "insert":
                session.add(Job(kind=JobKind.CHARACTER_MERGE, state=JobState.QUEUED,
                                idempotency_key="alias", request_digest="c" * 64))
            elif steal == "same_job_alias":
                original.idempotency_key, original.request_digest = "alias", "d" * 64
            else:
                other.idempotency_key = "alias"
            session.commit()
        session.rollback()
        receipt = session.get(JobRequestKey, "alias")
        assert receipt.job_id == original.id and receipt.request_digest == "c" * 64
        assert original.idempotency_key == "original"


def test_original_key_change_keeps_old_receipt_and_digest_updates_do_not_change_aliases(migrated_engine):
    with Session(migrated_engine) as session:
        job = Job(kind=JobKind.INFERENCE, state=JobState.QUEUED,
                  idempotency_key="original", request_digest="a" * 64)
        session.add(job)
        session.flush()
        bind_request_key(session, job, "alias", "c" * 64)
        job.request_digest = "b" * 64
        session.commit()
        assert session.get(JobRequestKey, "original").request_digest == "b" * 64
        assert session.get(JobRequestKey, "alias").request_digest == "c" * 64
        job.idempotency_key, job.request_digest = "changed-original", "d" * 64
        session.commit()
        assert session.get(JobRequestKey, "original").request_digest == "b" * 64
        assert session.get(JobRequestKey, "changed-original").request_digest == "d" * 64
        assert find_request_job(session, "original", "b" * 64).id == job.id


def test_metadata_created_database_uses_same_request_key_helpers(engine):
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        job = Job(kind=JobKind.INFERENCE, state=JobState.QUEUED,
                  idempotency_key="original", request_digest="a" * 64)
        session.add(job)
        session.flush()
        bind_request_key(session, job, "original", "a" * 64)
        bind_request_key(session, job, "alias", "a" * 64)
        job.state = JobState.COMPLETED
        session.commit()
        assert find_request_job(session, "original", "a" * 64).id == job.id
        assert find_request_job(session, "alias", "a" * 64).id == job.id
        with pytest.raises(ApiError):
            find_request_job(session, "alias", "b" * 64)
        other = Job(kind=JobKind.CHARACTER_ROSTER, state=JobState.QUEUED,
                    idempotency_key="other", request_digest="a" * 64)
        session.add(other)
        session.flush()
        with pytest.raises(ApiError):
            bind_request_key(session, other, "alias", "a" * 64)


def test_request_key_migration_backfills_and_is_reversible_repeatable(tmp_settings):
    run_migrations(tmp_settings, revision="0022")
    engine = create_db_engine(tmp_settings)
    factory = create_session_factory(engine)
    try:
        with admission_transaction(factory) as session:
            job = Job(kind=JobKind.INFERENCE, state=JobState.COMPLETED,
                      idempotency_key="legacy-original", request_digest="a" * 64,
                      range_json='{"reading_mode":"reread"}', budget_json='{"max_input_tokens":99}')
            null_key = Job(kind=JobKind.IMPORT, state=JobState.COMPLETED)
            session.add_all([job, null_key])
            session.flush()
            job_id = job.id
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT * FROM jobs ORDER BY id")).all()
        for _ in range(2):
            run_migrations(tmp_settings)
            run_migrations(tmp_settings)
            with engine.connect() as connection:
                assert connection.execute(text("SELECT * FROM jobs ORDER BY id")).all() == rows
            with factory() as session:
                receipts = list(session.scalars(select(JobRequestKey)))
                assert [(r.idempotency_key, r.job_id, r.request_digest) for r in receipts] == [
                    ("legacy-original", job_id, "a" * 64),
                ]
                bind_request_key(session, session.get(Job, job_id), "temporary-alias", "a" * 64)
                session.commit()
            command.downgrade(build_alembic_config(tmp_settings), "0022")
            assert "job_request_keys" not in inspect(engine).get_table_names()
            with engine.connect() as connection:
                assert connection.execute(text("SELECT * FROM jobs ORDER BY id")).all() == rows
                assert not connection.execute(text(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'jobs_register_%'"
                )).all()
                assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        run_migrations(tmp_settings)
    finally:
        engine.dispose()
