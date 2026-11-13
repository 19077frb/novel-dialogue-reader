"""Recent task reads keep complete recovery details without per-task queries."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Request
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from ndr.api.jobs import recent_jobs_route
from ndr.domain.enums import InferenceRunState, JobKind, JobState
from ndr.ingest.service import import_txt
from ndr.jobs import local_finalization
from ndr.jobs.service import job_detail, job_details
from ndr.storage.models import InferenceRun, Job, JobWindow


@contextmanager
def counted_queries(engine):
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def seed_jobs(session, settings, count):
    outcome = import_txt(session, settings, filename="recent.txt", raw="第一章\n「你好。」".encode())
    book_id = outcome.book.id
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(count):
        job = Job(
            id=f"job-{index:03}",
            kind=JobKind.INFERENCE,
            book_id=book_id,
            book_version_id=outcome.version.id,
            # Equal timestamps deliberately exercise the ID tie breaker.
            created_at=base + timedelta(seconds=index // 2),
            state=JobState.RUNNING if index == 0 else JobState.COMPLETED,
            range_json=json.dumps({"chapter_id": "selected", "selected_window_ids": ["a"]}),
            checkpoint_json='{"window_id":"a"}',
            progress_json='{"cached_windows":2,"stage":"dispatch"}',
        )
        session.add(job)
        session.flush()
        session.add(JobWindow(job_id=job.id, window_id="a", target_ids_json='["q"]'))
        session.add(InferenceRun(
            job_id=job.id, window_id="a", state=InferenceRunState.SUCCEEDED,
            usage_json='{"input_tokens":2,"output_tokens":3}',
            profile_snapshot_json="{}", request_fingerprint="x" * 64,
            call_archive=b"archive must not be read",
        ))
    session.commit()
    return book_id


@pytest.mark.parametrize("limit", [1, 100, 200])
def test_recent_route_has_constant_query_budget_and_complete_details(
    migrated_settings, migrated_engine, limit,
):
    with Session(migrated_engine) as session:
        book_id = seed_jobs(session, migrated_settings, 205)
    with Session(migrated_engine) as session, counted_queries(migrated_engine) as queries:
        result = recent_jobs_route(Request({"type": "http"}), book_id=book_id,
                                   kind=JobKind.INFERENCE, limit=limit, session=session)
        assert len(queries) == 5
        assert [row.id for row in result.data] == [f"job-{i:03}" for i in range(204, 204 - limit, -1)]
        assert all(row.calls == 1 and row.windows_total == 1 for row in result.data)
        assert all(row.windows[0].attempts == 1 for row in result.data)
        assert all(row.usage["total_tokens"] == 5 for row in result.data)
        assert not any("call_archive" in sql for sql in queries)
        assert not any(isinstance(row, InferenceRun) for row in session.identity_map.values())
    with Session(migrated_engine) as session, counted_queries(migrated_engine) as queries:
        empty = recent_jobs_route(Request({"type": "http"}), book_id=book_id,
                                  chapter_id="absent", limit=limit, session=session)
        assert empty.data == []
        assert len(queries) == 3


def test_batched_details_preserve_recovery_usage_window_order_and_task_isolation(
    migrated_settings, migrated_engine,
):
    with Session(migrated_engine) as session:
        book_id = seed_jobs(session, migrated_settings, 3)
        empty = Job(id="empty", kind=JobKind.CHARACTER_ROSTER, book_id=book_id,
                    state=JobState.NEEDS_RECONCILIATION, last_error="需要核实")
        session.add(empty)
        session.add_all([
            JobWindow(job_id="job-000", window_id="z", state=JobState.NEEDS_RECONCILIATION,
                      target_ids_json="not-json"),
            JobWindow(job_id="job-000", window_id="b", state=JobState.COMPLETED,
                      target_ids_json='{"invalid":"object"}'),
        ])
        for usage, state, error in [
            (None, InferenceRunState.DISPATCHED, None),
            (None, InferenceRunState.FAILED, "INVALID_MODEL_OUTPUT"),
            ("not-json", InferenceRunState.FAILED, None),
            ('{"unknown":true}', InferenceRunState.UNKNOWN_OUTCOME, None),
            (None, InferenceRunState.PREPARED, None),
        ]:
            session.add(InferenceRun(job_id="job-000", window_id="a", state=state,
                                     usage_json=usage, error_code=error,
                                     profile_snapshot_json="{}", request_fingerprint="x" * 64))
        session.commit()
    factory = object()
    local_finalization.defer_settlement(factory, "job-000", lambda: None)
    try:
        with Session(migrated_engine) as session:
            jobs = [session.get(Job, key) for key in ["job-002", "empty", "job-000", "job-001"]]
            expected = [job_detail(session, job).model_dump() for job in jobs]
            with counted_queries(migrated_engine) as queries:
                actual = job_details(session, jobs)
                assert len(queries) == 2
            assert [row.model_dump() for row in actual] == expected
            running = actual[2]
            assert running.state is JobState.RUNNING
            assert running.progress["stage"] == "local_finalization_pending"
            assert running.checkpoint == {"window_id": "a"}
            assert running.range["selected_window_ids"] == ["a"]
            assert [row.window_id for row in running.windows] == ["a", "b", "z"]
            assert [row.target_count for row in running.windows] == [1, 0, 0]
            assert running.windows[0].attempts == 6
            assert actual[0].windows[0].attempts == actual[3].windows[0].attempts == 1
            assert running.remaining_windows == 2
            assert running.calls == 6 and running.unknown_usage_runs == 3
            assert running.usage == {"input_tokens": 2, "output_tokens": 3,
                                     "total_tokens": 5, "unknown_runs": 4}
            assert actual[1].windows == [] and actual[1].calls == 0
            with counted_queries(migrated_engine) as queries:
                assert job_details(session, []) == []
                assert not queries
            assert session.scalar(select(Job.state).where(Job.id == "job-000")) is JobState.RUNNING
    finally:
        local_finalization.forget_settlements(factory)


def test_service_errors_report_only_latest_unresolved_window_calls_without_extra_queries(
    migrated_settings, migrated_engine,
):
    with Session(migrated_engine) as session:
        seed_jobs(session, migrated_settings, 4)
        base = datetime(2030, 1, 1, tzinfo=UTC)
        attempts = [
            ("job-000", "a", "old-limit", "RATE_LIMITED", InferenceRunState.FAILED, 0),
            ("job-000", "a", "retry-ok", None, InferenceRunState.SUCCEEDED, 1),
            # A later success in another window leaves this window unresolved.
            ("job-001", "a", "quota", "PROVIDER_QUOTA_EXHAUSTED", InferenceRunState.FAILED, 0),
            ("job-001", "b", "other-ok", None, InferenceRunState.SUCCEEDED, 1),
            # Historical completed jobs can retain a valid primary result.
            ("job-002", "a", "primary-ok", None, InferenceRunState.SUCCEEDED, 0),
            ("job-002", "a", "review-limited", "RATE_LIMITED", InferenceRunState.FAILED, 1),
            # An active newer retry supersedes its previous failed attempt.
            ("job-003", "a", "earlier", "RATE_LIMITED", InferenceRunState.FAILED, 0),
            ("job-003", "a", "retry-running", None, InferenceRunState.DISPATCHED, 1),
        ]
        for job_id, window_id, run_id, error, state, offset in attempts:
            session.add(InferenceRun(id=run_id, job_id=job_id, window_id=window_id,
                state=state, error_code=error, created_at=base + timedelta(seconds=offset),
                profile_snapshot_json="{}", request_fingerprint="x" * 64))
        completed = session.get(Job, "job-002")
        completed.checkpoint_json = '{"review_stopped":{"a":"历史复核提前结束"}}'
        session.commit()
    with Session(migrated_engine) as session:
        jobs = [session.get(Job, f"job-{index:03}") for index in range(4)]
        expected = [job_detail(session, job).model_dump() for job in jobs]
        with counted_queries(migrated_engine) as queries:
            details = job_details(session, jobs)
        assert len(queries) == 2
        assert not any("call_archive" in query for query in queries)
        assert [detail.model_dump() for detail in details] == expected
        assert [detail.service_error_code for detail in details] == [
            None, "PROVIDER_QUOTA_EXHAUSTED", "RATE_LIMITED", None,
        ]
        assert [detail.service_error_run_id for detail in details] == [
            None, "quota", "review-limited", None,
        ]
        assert details[2].state is JobState.COMPLETED
