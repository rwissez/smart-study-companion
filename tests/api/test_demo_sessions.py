from __future__ import annotations

from fastapi.testclient import TestClient


def _create(client: TestClient, **payload) -> dict:
    response = client.post("/api/v1/demo-sessions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_bootstrap_teacher_gets_only_selection_capabilities(client: TestClient) -> None:
    data = _create(client, role="teacher")

    assert data["session_token"]
    scope = data["scope"]
    assert scope["role"] == "teacher"
    assert scope["dataset_id"] is None
    assert scope["bootstrap"] is True
    assert set(scope["capabilities"]) == {"list_datasets", "import_dataset", "select_dataset"}


def test_bound_teacher_gets_full_teacher_capabilities(client: TestClient, seeded: dict) -> None:
    data = _create(client, role="teacher", dataset_id=seeded["dataset_id"])

    scope = data["scope"]
    assert scope["bootstrap"] is False
    assert "view_class_analysis" in scope["capabilities"]
    # Teachers chat under their own capability; the student-scoped one must not leak.
    assert "chat_teacher" in scope["capabilities"]
    assert "chat_own" not in scope["capabilities"]


def test_student_session_requires_dataset_and_self(client: TestClient, seeded: dict) -> None:
    missing = client.post("/api/v1/demo-sessions", json={"role": "student"})
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "VALIDATION_ERROR"

    data = _create(client, role="student", dataset_id=seeded["dataset_id"], student_id="S01")
    scope = data["scope"]
    assert scope["role"] == "student"
    assert scope["student_id"] == "S01"
    assert scope["capabilities"] == ["view_own_analysis", "view_own_plan", "chat_own"]


def test_unknown_student_is_indistinguishable_from_unknown_dataset(
    client: TestClient, seeded: dict
) -> None:
    unknown_student = client.post(
        "/api/v1/demo-sessions",
        json={"role": "student", "dataset_id": seeded["dataset_id"], "student_id": "S99"},
    )
    unknown_dataset = client.post(
        "/api/v1/demo-sessions",
        json={"role": "student", "dataset_id": "ds-missing", "student_id": "S01"},
    )

    assert unknown_student.status_code == 404
    assert unknown_dataset.status_code == 404
    assert "S99" not in unknown_student.text


def test_tampered_token_is_rejected(client: TestClient, seeded: dict) -> None:
    data = _create(client, role="student", dataset_id=seeded["dataset_id"], student_id="S01")
    token = data["session_token"]

    ok = client.get("/api/v1/datasets", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 403  # students cannot list datasets
    assert ok.json()["error"]["code"] == "FORBIDDEN"

    bad = client.get("/api/v1/catalog", headers={"Authorization": f"Bearer {token}x"})
    assert bad.status_code == 403

    none = client.get("/api/v1/catalog")
    assert none.status_code == 403


def test_runs_are_listed_for_a_dataset(client: TestClient, seeded: dict) -> None:
    data = _create(client, role="teacher", dataset_id=seeded["dataset_id"])
    headers = {"Authorization": f"Bearer {data['session_token']}"}

    response = client.get(f"/api/v1/datasets/{seeded['dataset_id']}/runs", headers=headers)
    assert response.status_code == 200
    assert [item["run_id"] for item in response.json()["data"]["items"]] == [seeded["run_id"]]


def test_demo_endpoints_vanish_when_demo_mode_is_off(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setenv("DEMO_MODE", "false")
    from study_companion.config import reset_settings_cache

    reset_settings_cache()

    response = client.post("/api/v1/demo-sessions", json={"role": "teacher"})
    assert response.status_code == 404
