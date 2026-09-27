from __future__ import annotations

from fastapi.testclient import TestClient

from ndr import __version__
from ndr.app import create_app


def test_health_reports_process_and_version(tmp_settings) -> None:
    with TestClient(create_app(tmp_settings)) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "novel-dialogue-reader"
    assert body["version"] == __version__
    assert body["api_version"] == "1"
    assert body["uptime_seconds"] >= 0
    assert body["started_at"].endswith("+00:00")


def test_health_does_not_claim_database_ready_before_t01(tmp_settings) -> None:
    with TestClient(create_app(tmp_settings)) as client:
        body = client.get("/api/health").json()

    assert body["database"]["state"] == "NOT_INITIALIZED"


def test_unknown_api_route_returns_404(tmp_settings) -> None:
    with TestClient(create_app(tmp_settings)) as client:
        assert client.get("/api/does-not-exist").status_code == 404
