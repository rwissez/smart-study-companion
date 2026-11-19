from __future__ import annotations

from fastapi.testclient import TestClient

from study_companion.api.envelope import SCHEMA_VERSION


def test_health_reports_envelope_and_flags(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200

    body = response.json()
    assert body["schema_version"] == SCHEMA_VERSION
    assert body["warnings"] == []
    assert body["request_id"]
    assert response.headers["X-Request-ID"] == body["request_id"]

    data = body["data"]
    assert data["status"] == "ok"
    assert data["database_ok"] is True
    assert data["demo_mode"] is True
    assert data["model_configured"] is False


def test_health_echoes_supplied_request_id(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"X-Request-ID": "req-from-caller"})
    assert response.json()["request_id"] == "req-from-caller"
    assert response.headers["X-Request-ID"] == "req-from-caller"


def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/nope")
    assert response.status_code == 404

    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert "data" not in body
    assert body["request_id"]
