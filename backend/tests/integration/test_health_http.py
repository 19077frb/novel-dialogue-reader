"""连通性集成测试：真实 ASGI 请求走通 /api/health。"""

from __future__ import annotations

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
