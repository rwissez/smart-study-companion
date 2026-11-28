"""SEC-02 与实时状态：学生证据投影、方案活动状态、提示回显。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from study_companion.jobs.worker import drain

FIXTURES = Path(__file__).parent / "fixtures" / "six_tables"
TABLES = ("requirements", "materials", "students", "nodes", "records", "dictionary")
TEACHER_ONLY = ("reference_answer", "reference_explanation", "rubric_points", "reviewed_variant_tag")


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


def test_student_evidence_has_no_answer_fields(client: TestClient, imported: dict) -> None:
    dataset_id = imported["dataset_id"]
    teacher = {"Authorization": f"Bearer {_session(client, role='teacher', dataset_id=dataset_id)}"}
    student = {"Authorization": f"Bearer {_session(client, role='student', dataset_id=dataset_id, student_id='S01')}"}

    profile = client.get(f"/api/v1/datasets/{dataset_id}/students/S01/profile", headers=teacher).json()["data"]["result"]
    run_id = profile["run_id"]
    records = client.get(
        f"/api/v1/runs/{run_id}/students/S01/answer-records",
        headers=teacher,
        params={"node_id": "N01"},
    ).json()["data"]["items"]
    assert records, "S01 应有官方作答记录"
    record_id = records[0]["record_id"]

    ref = {"kind": "record", "id": record_id, "run_id": run_id}
    student_view = client.post("/api/v1/evidence/resolve", headers=student, json={"ref": ref})
    assert student_view.status_code == 200, student_view.text
    detail = student_view.json()["data"]["detail"]
    question = detail["question"]
    for field in TEACHER_ONLY:
        assert field not in question, f"学生证据不得包含 {field}"

    teacher_view = client.post("/api/v1/evidence/resolve", headers=teacher, json={"ref": ref})
    assert teacher_view.status_code == 200
    assert "reference_answer" in teacher_view.json()["data"]["detail"]["question"]


def test_plan_activity_state_follows_real_events(client: TestClient, imported: dict) -> None:
    dataset_id = imported["dataset_id"]
    student = {"Authorization": f"Bearer {_session(client, role='student', dataset_id=dataset_id, student_id='S01')}"}
    plan = client.get(f"/api/v1/datasets/{dataset_id}/students/S01/plan", headers=student).json()["data"]
    activity = None
    for recommendation in plan["plan"]["recommendations"]:
        for item in recommendation["activities"]:
            if item["activity_kind"] == "question":
                activity = item
                break
        if activity:
            break
    assert activity is not None
    assert activity["state"] == "not_started"

    start = client.post(
        f"/api/v1/plans/{plan['plan_version']}/activities/{activity['activity_id']}/start",
        headers=student,
    ).json()["data"]
    attempt_id = start["attempt"]["attempt_id"]
    client.post(
        f"/api/v1/attempts/{attempt_id}/hints", headers=student, json={"level": 1}
    )

    refreshed = client.get(f"/api/v1/datasets/{dataset_id}/students/S01/plan", headers=student).json()["data"]
    live = next(
        item
        for recommendation in refreshed["plan"]["recommendations"]
        for item in recommendation["activities"]
        if item["activity_id"] == activity["activity_id"]
    )
    assert live["state"] == "in_progress", "方案读取必须反映真实活动状态"

    attempt = client.get(f"/api/v1/attempts/{attempt_id}", headers=student).json()["data"]
    assert attempt["released_hints"], "已释放提示应可回显"
    assert attempt["released_hints"][0]["level"] == 1
    assert attempt["released_hints"][0]["text"]


def test_conversations_messages_are_paginated(client: TestClient, imported: dict) -> None:
    dataset_id = imported["dataset_id"]
    headers = {"Authorization": f"Bearer {_session(client, role='student', dataset_id=dataset_id, student_id='S01')}"}
    chat = client.post(
        "/api/v1/chat",
        headers=headers,
        json={"message": "我这次为什么没做对？", "target": {"type": "student", "id": "S01"}, "node_id": "N01"},
    )
    assert chat.status_code == 200, chat.text
    conversation_id = chat.json()["data"]["conversation_id"]
    messages = client.get(
        f"/api/v1/conversations/{conversation_id}/messages", headers=headers, params={"page_size": 10}
    ).json()["data"]
    assert {"items", "total", "page", "page_size"} <= set(messages)
    assert messages["total"] >= 2
    roles = [item["role"] for item in messages["items"]]
    assert roles[0] == "user" and "assistant" in roles
