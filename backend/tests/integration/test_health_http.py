"""连通性集成测试：真实 ASGI 请求走通 /api/health。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ndr.app import create_app


def test_app_starts_and_serves_health(tmp_settings) -> None:
    app = create_app(tmp_settings)
    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["status"] == "ok"


def test_lifespan_creates_data_dir(tmp_settings) -> None:
    assert not tmp_settings.data_dir.exists()
    with TestClient(create_app(tmp_settings)):
        assert tmp_settings.data_dir.is_dir()


@pytest.mark.parametrize("origin", [
    "http://127.0.0.1:8765", "http://localhost:8800", "http://[::1]:9000",
    "http://127.0.0.1",
])
def test_restart_health_allows_loopback_origin_only_on_health(tmp_settings, origin) -> None:
    tmp_settings.cors_origins = []
    with TestClient(create_app(tmp_settings)) as client:
        response = client.get("/api/health", headers={"Origin": origin})
        configuration = client.get("/api/settings/application", headers={"Origin": origin})

    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["cache-control"] == "no-store"
    assert "Origin" in response.headers["vary"]
    assert "access-control-allow-credentials" not in response.headers
    assert "access-control-allow-origin" not in configuration.headers
    assert response.json()["app"] == "novel-dialogue-reader"


@pytest.mark.parametrize("origin", [
    "https://example.com", "http://127.0.0.1.example.com", "null",
    "http://user@localhost:8765", "http://localhost:8765/path",
    "http://localhost:8765?query=1", "http://localhost:8765#fragment",
    "http://localhost:65536", "http://localhost:invalid", "http://[invalid]:8765",
    "https://localhost:8765", "http://192.168.0.1:8765",
])
def test_restart_health_does_not_authorize_other_origins(tmp_settings, origin) -> None:
    tmp_settings.cors_origins = []
    with TestClient(create_app(tmp_settings)) as client:
        response = client.get("/api/health", headers={"Origin": origin})
    assert "access-control-allow-origin" not in response.headers
