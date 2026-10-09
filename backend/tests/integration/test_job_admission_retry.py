import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError
from tests.integration.test_jobs import SAMPLE, _fake_profile, _import

from ndr.storage.models import InferenceRun, Job


def payload(client):
    book = _import(client)
    return {"book_id": book["book_id"], "profile_id": _fake_profile(client),
            "mode": "process", "range": {"start_cp": 0, "end_cp": len(SAMPLE)},
            "idempotency_key": "admission-retry-test", "run_now": True}


@pytest.mark.parametrize("failure", ["busy_once", "busy_always", "disk_error"])
def test_admission_retries_rollback_before_dispatch_and_preserve_idempotency(
    migrated_client, monkeypatch, failure,
):
    import ndr.api.jobs as routes

    client = migrated_client
    body = payload(client)
    create = routes.create_inference_job
    attempts, dispatched = [], []

    def create_with_failure(*args, **kwargs):
        value = create(*args, **kwargs)
        attempts.append(True)
        if failure != "busy_once" or len(attempts) == 1:
            error = sqlite3.OperationalError("private database detail")
            error.sqlite_errorcode = (sqlite3.SQLITE_IOERR if failure == "disk_error"
                                      else sqlite3.SQLITE_BUSY)
            error.sqlite_errorname = "SQLITE_IOERR" if failure == "disk_error" else "SQLITE_BUSY"
            raise OperationalError("private SQL", {"private": "secret"}, error)
        return value

    monkeypatch.setattr(routes, "create_inference_job", create_with_failure)
    monkeypatch.setattr(routes, "run_job", lambda *args, **kwargs: dispatched.append(kwargs["job_id"]))
    monkeypatch.setattr("ndr.storage.transactions.time.sleep", lambda *_: None)
    with TestClient(client.app, raise_server_exceptions=False) as safe_client:
        response = safe_client.post("/api/jobs", json=body)
    assert len(attempts) == {"busy_once": 2, "busy_always": 3, "disk_error": 1}[failure]
    with client.app.state.session_factory() as session:
        stored = list(session.scalars(select(Job).where(Job.idempotency_key == body["idempotency_key"])))
        assert not list(session.scalars(select(InferenceRun)))
    if failure == "busy_once":
        assert response.status_code == 202 and len(stored) == len(dispatched) == 1
        monkeypatch.setattr(routes, "create_inference_job", create)
        repeated = client.post("/api/jobs", json=body)
        assert repeated.json()["data"]["id"] == response.json()["data"]["id"]
        assert len(dispatched) == 1
    else:
        assert not stored and not dispatched
        error = response.json()["error"]
        assert response.json()["request_id"]
        assert "private" not in response.text
        if failure == "busy_always":
            assert response.status_code == 503
            assert error["details"]["task_created"] is False
            assert error["details"]["safe_to_retry"] is True
            assert "SQLITE_BUSY" in error["details"]["database_error"]
        else:
            assert response.status_code == 500
            assert "safe_to_retry" not in error["details"]


def test_real_sqlite_writer_lock_returns_explicit_uncommitted_submission(migrated_client, monkeypatch):
    client = migrated_client
    body = payload(client)
    body["run_now"] = False
    engine = client.app.state.session_factory.kw["bind"]

    def no_wait(connection, *_):
        connection.execute("PRAGMA busy_timeout=0")

    event.listen(engine, "checkout", no_wait)
    monkeypatch.setattr("ndr.storage.transactions.time.sleep", lambda *_: None)
    try:
        with engine.connect() as writer:
            writer.exec_driver_sql("BEGIN IMMEDIATE")
            response = client.post("/api/jobs", json=body)
            assert response.status_code == 503
            assert response.json()["error"]["details"]["task_created"] is False
            writer.rollback()
        assert client.post("/api/jobs", json=body).status_code == 202
    finally:
        event.remove(engine, "checkout", no_wait)
