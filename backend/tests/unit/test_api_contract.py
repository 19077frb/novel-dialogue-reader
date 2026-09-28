"""HTTP 错误契约与请求 ID 的集成测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import BaseModel

from ndr.app import create_app
from ndr.domain.enums import ErrorCode
from ndr.storage.transactions import VersionConflict


class _Echo(BaseModel):
    value: int


def _app_with_test_routes(settings):  # noqa: ANN001, ANN202
    """在测试中注册临时路由，用于验证错误处理契约（不属于产品路由）。"""

    app = create_app(settings)

    @app.get("/api/_test/echo")
    def echo(value: int) -> dict[str, int]:
        return {"value": value}

    @app.post("/api/_test/body")
    def body(payload: _Echo) -> dict[str, int]:
        return {"value": payload.value}

    @app.get("/api/_test/conflict")
    def conflict() -> None:
        raise VersionConflict(entity="Annotation", target_id="a1", expected=3, current=4)

    return app


def test_unknown_route_uses_error_envelope(migrated_client: TestClient) -> None:
    response = migrated_client.get("/api/does-not-exist")
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["code"] == ErrorCode.NOT_FOUND.value
    assert payload["error"]["details"] == {}
    assert payload["request_id"] == response.headers["x-request-id"]


def test_invalid_query_parameter_returns_validation_envelope(tmp_settings, migrated_settings) -> None:  # noqa: ANN001
    app = _app_with_test_routes(migrated_settings)
    with TestClient(app) as client:
        response = client.get("/api/_test/echo", params={"value": "not-an-int"})
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == ErrorCode.VALIDATION_ERROR.value
    assert payload["error"]["details"]["errors"][0]["loc"] == ["query", "value"]


def test_invalid_body_returns_validation_envelope(migrated_settings) -> None:  # noqa: ANN001
    app = _app_with_test_routes(migrated_settings)
    with TestClient(app) as client:
        response = client.post("/api/_test/body", json={"value": "x", "extra": 1})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR.value


def test_version_conflict_maps_to_409(migrated_settings) -> None:  # noqa: ANN001
    app = _app_with_test_routes(migrated_settings)
    with TestClient(app) as client:
        response = client.get("/api/_test/conflict")
    assert response.status_code == 409
    payload = response.json()
    assert payload["error"]["code"] == ErrorCode.VERSION_CONFLICT.value
    assert payload["error"]["details"] == {
        "entity": "Annotation",
        "target_id": "a1",
        "expected_version": 3,
        "current_version": 4,
    }


def test_health_reports_ready_after_migration(migrated_client: TestClient) -> None:
    payload = migrated_client.get("/api/health").json()
    assert payload["status"] == "ok"
    assert payload["database"]["state"] == "READY"
    assert payload["database"]["revision"] == payload["database"]["head_revision"]


def test_health_reports_not_initialized_without_migration(client: TestClient) -> None:
    payload = client.get("/api/health").json()
    assert payload["database"]["state"] == "NOT_INITIALIZED"


def test_openapi_contains_contract_components(migrated_client: TestClient) -> None:
    schema = migrated_client.get("/openapi.json").json()
    components = schema["components"]["schemas"]
    for name in ("DataEnvelope", "ErrorEnvelope", "ErrorBody", "CursorPage"):
        assert name in components
    assert schema["info"]["version"]
    assert "/api/health" in schema["paths"]
