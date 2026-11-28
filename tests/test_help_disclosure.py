"""帮助披露状态机：独立模式先确认、拒绝不释放、接受写事件（01 §7.1、05 §8）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from study_companion.jobs.worker import drain

FIXTURES = Path(__file__).parent / "fixtures" / "six_tables"
TABLES = ("requirements", "materials", "students", "nodes", "records", "dictionary")


def _session(client: TestClient, **payload) -> str:
    return client.post("/api/v1/demo-sessions", json=payload).json()["data"]["session_token"]


@pytest.fixture
def imported(client: TestClient) -> dict:
    teacher = {"Authorization": f"Bearer {_session(client, role='teacher')}"}
    files = [
        ("files", (f"{name}.csv", (FIXTURES / f"{name}.csv").read_bytes(), "text/csv"))
        for name in TABLES
    ]
    receipt = client.post(
        "/api/v1/imports",
        headers=teacher,
        files=files,
        data={"metadata": '{"intent":"new_family"}'},
    ).json()["data"]
    drain()
    return receipt


def _independent_activity(client: TestClient, dataset_id: str, token: str) -> tuple[dict, dict]:
    student = {"Authorization": f"Bearer {token}"}
    plan = client.get(f"/api/v1/datasets/{dataset_id}/students/S01/plan", headers=student).json()["data"]
    for recommendation in plan["plan"]["recommendations"]:
        for activity in recommendation["activities"]:
            if activity.get("exposure_mode") == "independent":
                return plan, activity
    pytest.skip("本轮方案没有独立模式活动")


def test_independent_activity_requires_confirmation_before_hint(
    client: TestClient, imported: dict
) -> None:
    dataset_id = imported["dataset_id"]
    token = _session(client, role="student", dataset_id=dataset_id, student_id="S01")
    student = {"Authorization": f"Bearer {token}"}
    plan, activity = _independent_activity(client, dataset_id, token)

    start = client.post(
        f"/api/v1/plans/{plan['plan_version']}/activities/{activity['activity_id']}/start",
        headers=student,
    ).json()["data"]
    attempt_id = start["attempt"]["attempt_id"]

    pending = client.post(
        f"/api/v1/attempts/{attempt_id}/hints", headers=student, json={"level": 1}
    )
    assert pending.status_code == 200, pending.text
    data = pending.json()["data"]
    assert data["requires_confirmation"] is True
    assert data["hint"] is None
    disclosure_id = data["disclosure_id"]

    released = client.post(
        f"/api/v1/attempts/{attempt_id}/assisted-confirm",
        headers=student,
        json={"disclosure_id": disclosure_id, "allow": True},
    )
    assert released.status_code == 200, released.text
    assert released.json()["data"]["released"] is True
    assert released.json()["data"]["hint"]

    attempt = client.get(f"/api/v1/attempts/{attempt_id}", headers=student).json()["data"]
    assert attempt["attempt"]["assistance_status"] == "assisted"
    assert attempt["released_hints"], "确认后应能回显已释放提示"

    teacher = {"Authorization": f"Bearer {_session(client, role='teacher', dataset_id=dataset_id)}"}
    events = client.get(
        f"/api/v1/datasets/{dataset_id}/students/S01/learning-events", headers=teacher
    ).json()["data"]
    types = [item["event_type"] for item in events["items"]]
    assert "assisted_mode_confirmed" in types
    assert "hint_requested" in types


def test_rejecting_help_keeps_attempt_independent(client: TestClient, imported: dict) -> None:
    dataset_id = imported["dataset_id"]
    token = _session(client, role="student", dataset_id=dataset_id, student_id="S02")
    student = {"Authorization": f"Bearer {token}"}
    plan_response = client.get(f"/api/v1/datasets/{dataset_id}/students/S02/plan", headers=student)
    plan = plan_response.json()["data"]
    independent = None
    for recommendation in plan["plan"]["recommendations"]:
        for activity in recommendation["activities"]:
            if activity.get("exposure_mode") == "independent":
                independent = activity
                break
        if independent:
            break
    if independent is None:
        pytest.skip("S02 本轮方案没有独立模式活动")

    start = client.post(
        f"/api/v1/plans/{plan['plan_version']}/activities/{independent['activity_id']}/start",
        headers=student,
    ).json()["data"]
    attempt_id = start["attempt"]["attempt_id"]
    pending = client.post(
        f"/api/v1/attempts/{attempt_id}/hints", headers=student, json={"level": 1}
    ).json()["data"]
    rejected = client.post(
        f"/api/v1/attempts/{attempt_id}/assisted-confirm",
        headers=student,
        json={"disclosure_id": pending["disclosure_id"], "allow": False},
    )
    assert rejected.status_code == 200
    assert rejected.json()["data"]["released"] is False

    attempt = client.get(f"/api/v1/attempts/{attempt_id}", headers=student).json()["data"]
    assert attempt["attempt"]["assistance_status"] != "assisted"
    assert not attempt["released_hints"]
