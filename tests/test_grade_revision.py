"""教师评分修订：追加版本、保留原卷、学生看到最新分（03 §14）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.db import models
from study_companion.jobs.worker import drain

FIXTURES = Path(__file__).parent / "fixtures" / "six_tables"
TABLES = ("requirements", "materials", "students", "nodes", "records", "dictionary")


def _session(client: TestClient, **payload) -> str:
    return client.post("/api/v1/demo-sessions", json=payload).json()["data"]["session_token"]


@pytest.fixture
def ctx(client: TestClient) -> dict:
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
    dataset_id = receipt["dataset_id"]
    return {
        "dataset_id": dataset_id,
        "teacher": {"Authorization": f"Bearer {_session(client, role='teacher', dataset_id=dataset_id)}"},
        "student": {"Authorization": f"Bearer {_session(client, role='student', dataset_id=dataset_id, student_id='S01')}"},
    }


def _start_first_question(client: TestClient, ctx: dict) -> dict:
    plan = client.get(
        f"/api/v1/datasets/{ctx['dataset_id']}/students/S01/plan",
        headers=ctx["student"],
        params={"node_id": "N01"},
    ).json()["data"]
    activity = next(
        item
        for rec in plan["plan"]["recommendations"]
        for item in rec["activities"]
        if item["activity_kind"] == "question"
    )
    start = client.post(
        f"/api/v1/plans/{plan['plan_version']}/activities/{activity['activity_id']}/start",
        headers=ctx["student"],
    ).json()["data"]
    return start


def _submit(client: TestClient, ctx: dict, attempt_id: str) -> dict:
    attempt = client.get(f"/api/v1/attempts/{attempt_id}", headers=ctx["student"]).json()["data"]
    material_id = attempt["attempt"]["material_id"]
    material = client.get(
        f"/api/v1/materials/{material_id}", headers=ctx["student"]
    ).json()["data"]
    question_type = material.get("question_type")
    answer = "A" if question_type == "choice" else "3"
    return client.post(
        f"/api/v1/attempts/{attempt_id}/submit",
        headers=ctx["student"],
        json={"final_answer": answer, "unanswered": False},
    ).json()["data"]


def test_teacher_revision_keeps_history_and_student_sees_latest(
    client: TestClient, ctx: dict, session: Session
) -> None:
    start = _start_first_question(client, ctx)
    attempt_id = start["attempt"]["attempt_id"]
    first = _submit(client, ctx, attempt_id)
    original_grade_id = first["grade"]["grade_id"]

    revised = client.post(
        f"/api/v1/attempts/{attempt_id}/grade-revisions",
        headers=ctx["teacher"],
        json={
            "status": "graded",
            "score": 5,
            "max_score": 5,
            "rubric_scores": [],
            "reason": "人工复核：答案等价，补足满分",
        },
    )
    assert revised.status_code == 201, revised.text
    data = revised.json()["data"]
    assert data["grade"]["revision"] == first["grade"]["revision"] + 1
    assert data["grade"]["score"] == 5
    assert data["grade"]["method"] == "teacher"
    assert data["previous_grade_id"] == original_grade_id

    history = client.get(
        f"/api/v1/attempts/{attempt_id}/grades", headers=ctx["teacher"]
    ).json()["data"]
    revisions = [item["revision"] for item in history["items"]]
    assert revisions == sorted(revisions) and len(revisions) >= 2
    assert history["latest_grade_id"] == data["grade"]["grade_id"]

    student_view = client.get(f"/api/v1/attempts/{attempt_id}", headers=ctx["student"]).json()["data"]
    assert student_view["grade"]["score"] == 5
    assert student_view["grade"]["revision"] == data["grade"]["revision"]

    events = session.scalars(
        select(models.LearningEventRow).where(models.LearningEventRow.event_type == "grade_revised")
    ).all()
    assert events and events[-1].payload_json["previous_grade_id"] == original_grade_id


def test_revision_cannot_carry_score_when_reopened(client: TestClient, ctx: dict) -> None:
    start = _start_first_question(client, ctx)
    attempt_id = start["attempt"]["attempt_id"]
    _submit(client, ctx, attempt_id)
    bad = client.post(
        f"/api/v1/attempts/{attempt_id}/grade-revisions",
        headers=ctx["teacher"],
        json={"status": "needs_review", "score": 3, "max_score": 5, "reason": "重新审"},
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "VALIDATION_ERROR"


def test_student_cannot_revise_grades(client: TestClient, ctx: dict) -> None:
    start = _start_first_question(client, ctx)
    attempt_id = start["attempt"]["attempt_id"]
    _submit(client, ctx, attempt_id)
    response = client.post(
        f"/api/v1/attempts/{attempt_id}/grade-revisions",
        headers=ctx["student"],
        json={"status": "graded", "score": 5, "max_score": 5, "reason": "自己给自己满分"},
    )
    assert response.status_code == 403
