"""The student side must never receive the answer key, from any route."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

FORBIDDEN_KEYS = {"reference_answer", "reference_explanation", "rubric_points", "reviewed_variant_tag"}


def _token(client: TestClient, **payload) -> str:
    response = client.post("/api/v1/demo-sessions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["data"]["session_token"]


@pytest.fixture
def teacher_headers(client: TestClient, seeded: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(client, role='teacher', dataset_id=seeded['dataset_id'])}"}


@pytest.fixture
def student_headers(client: TestClient, seeded: dict) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_token(client, role='student', dataset_id=seeded['dataset_id'], student_id='S01')}"
    }


def test_teacher_can_see_the_answer_key(client: TestClient, teacher_headers: dict) -> None:
    response = client.get("/api/v1/materials/Q01", headers=teacher_headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["material_id"] == "Q01"
    assert "max_score" in data


def test_student_material_has_no_teacher_only_keys(
    client: TestClient, student_headers: dict
) -> None:
    response = client.get("/api/v1/materials/Q01", headers=student_headers)
    assert response.status_code == 200

    data = response.json()["data"]
    assert data["material_id"] == "Q01"
    assert data["content"]
    assert not (FORBIDDEN_KEYS & set(data))


def test_student_catalog_does_not_embed_answers(
    client: TestClient, student_headers: dict
) -> None:
    response = client.get("/api/v1/catalog", headers=student_headers)
    assert response.status_code == 200

    data = response.json()["data"]
    assert data["catalog_version"]
    assert data["indicators"]
    assert not data.get("materials")


def test_missing_material_is_404(client: TestClient, student_headers: dict) -> None:
    response = client.get("/api/v1/materials/Q-missing", headers=student_headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
