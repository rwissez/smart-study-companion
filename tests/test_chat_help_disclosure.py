"""学生对话求助记入帮助披露账本（05 §8）。"""

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
        "student": {"Authorization": f"Bearer {_session(client, role='student', dataset_id=dataset_id, student_id='S01')}"},
        "teacher": {"Authorization": f"Bearer {_session(client, role='teacher', dataset_id=dataset_id)}"},
    }


def _independent_activity(client: TestClient, ctx: dict) -> tuple[dict, dict]:
    plan = client.get(
        f"/api/v1/datasets/{ctx['dataset_id']}/students/S01/plan",
        headers=ctx["student"],
        params={"node_id": "N01"},
    ).json()["data"]
    for recommendation in plan["plan"]["recommendations"]:
        for activity in recommendation["activities"]:
            if activity.get("exposure_mode") == "independent" and activity["activity_kind"] == "question":
                return plan, activity
    pytest.skip("没有独立模式题目活动")


def test_student_chat_help_marks_attempt_assisted(
    client: TestClient, ctx: dict, session: Session
) -> None:
    plan, activity = _independent_activity(client, ctx)
    start = client.post(
        f"/api/v1/plans/{plan['plan_version']}/activities/{activity['activity_id']}/start",
        headers=ctx["student"],
    ).json()["data"]
    attempt_id = start["attempt"]["attempt_id"]

    response = client.post(
        "/api/v1/chat",
        headers=ctx["student"],
        json={
            "message": "这道题我卡住了，第一步怎么做",
            "node_id": "N01",
            "activity_id": activity["activity_id"],
            "plan_version": plan["plan_version"],
        },
    )
    assert response.status_code == 200, response.text

    attempt = session.get(models.AttemptRow, attempt_id)
    assert attempt is not None and attempt.assistance_status == "assisted"

    disclosures = session.scalars(
        select(models.HelpDisclosureRow).where(models.HelpDisclosureRow.attempt_id == attempt_id)
    ).all()
    assert disclosures and any(item.state == "confirmed" for item in disclosures)

    events = session.scalars(
        select(models.LearningEventRow).where(models.LearningEventRow.attempt_id == attempt_id)
    ).all()
    types = {item.event_type for item in events}
    assert "agent_help_used" in types
    assert "assisted_mode_confirmed" in types, "独立模式下的对话帮助也要先确认受助"


def test_chat_without_activity_does_not_touch_attempt(client: TestClient, ctx: dict, session: Session) -> None:
    response = client.post(
        "/api/v1/chat",
        headers=ctx["student"],
        json={"message": "我今天该练什么", "node_id": "N01"},
    )
    assert response.status_code == 200
    assert session.scalars(select(models.HelpDisclosureRow)).all() == []
