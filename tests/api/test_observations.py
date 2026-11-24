from __future__ import annotations

from fastapi.testclient import TestClient


def _student_token(client: TestClient, seeded: dict, student_id: str = "S01") -> str:
    response = client.post(
        "/api/v1/demo-sessions",
        json={"role": "student", "dataset_id": seeded["dataset_id"], "student_id": student_id},
    )
    return response.json()["data"]["session_token"]


def test_student_can_submit_and_list_observation(client: TestClient, seeded: dict) -> None:
    token = _student_token(client, seeded)
    body = {
        "run_id": seeded["run_id"],
        "student_id": "S01",
        "node_id": "N01",
        "activity_id": "activity-1",
        "material_id": "Q01",
        "event_type": "hint_requested",
        "sequence": 1,
        "hint_level": 1,
        "payload": {"independent": False},
    }
    response = client.post(
        f"/api/v1/runs/{seeded['run_id']}/observations",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    )
    assert response.status_code == 201, response.text
    assert response.json()["data"]["source"] == "student"

    listed = client.get(
        f"/api/v1/runs/{seeded['run_id']}/observations",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 1
    assert listed.json()["data"]["items"][0]["event_type"] == "hint_requested"


def test_student_cannot_submit_for_another_student(client: TestClient, seeded: dict) -> None:
    token = _student_token(client, seeded)
    response = client.post(
        f"/api/v1/runs/{seeded['run_id']}/observations",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "run_id": seeded["run_id"], "student_id": "S02", "node_id": "N01",
            "activity_id": "activity-1", "event_type": "answer_submitted", "sequence": 1,
        },
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_duplicate_activity_sequence_is_conflict(client: TestClient, seeded: dict) -> None:
    token = _student_token(client, seeded)
    body = {
        "run_id": seeded["run_id"], "student_id": "S01", "node_id": "N01",
        "activity_id": "activity-1", "event_type": "answer_submitted", "sequence": 1,
    }
    path = f"/api/v1/runs/{seeded['run_id']}/observations"
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post(path, headers=headers, json=body).status_code == 201
    duplicate = client.post(path, headers=headers, json=body)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "CONFLICT"


def test_teacher_can_filter_observations_by_student(client: TestClient, seeded: dict) -> None:
    created = client.post(
        "/api/v1/demo-sessions",
        json={"role": "teacher", "dataset_id": seeded["dataset_id"]},
    )
    token = created.json()["data"]["session_token"]
    body = {
        "run_id": seeded["run_id"], "student_id": "S01", "node_id": "N01",
        "activity_id": "activity-2", "event_type": "activity_completed", "sequence": 1,
    }
    path = f"/api/v1/runs/{seeded['run_id']}/observations"
    assert client.post(path, headers={"Authorization": f"Bearer {token}"}, json=body).status_code == 201
    listed = client.get(
        path, headers={"Authorization": f"Bearer {token}"}, params={"student_id": "S01"}
    )
    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 1
