"""教师微课堂预览：不受学生完成状态限制，带审核信息（04 §9.1）。"""

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
    headers = {"Authorization": f"Bearer {_session(client, role='teacher', dataset_id=dataset_id)}"}
    run_id = client.get(f"/api/v1/datasets/{dataset_id}/runs", headers=headers).json()["data"]["items"][0]["run_id"]
    return {
        "dataset_id": dataset_id,
        "headers": headers,
        "run_id": run_id,
        "student": {"Authorization": f"Bearer {_session(client, role='student', dataset_id=dataset_id, student_id='S01')}"},
    }


def _lesson_activity(client: TestClient, ctx: dict) -> dict:
    plan = client.get(
        f"/api/v1/datasets/{ctx['dataset_id']}/students/S01/plan",
        headers=ctx["headers"],
        params={"node_id": "N01"},
    ).json()["data"]
    for recommendation in plan["plan"]["recommendations"]:
        for activity in recommendation["activities"]:
            if activity["activity_kind"] == "micro_lesson":
                return {"plan": plan, "activity": activity}
    pytest.skip("本轮方案没有微课堂活动")


def test_teacher_previews_micro_lesson_with_review_info(client: TestClient, ctx: dict) -> None:
    found = _lesson_activity(client, ctx)
    response = client.get(
        f"/api/v1/runs/{ctx['run_id']}/students/S01/activities/{found['activity']['activity_id']}/micro-lesson/preview",
        headers=ctx["headers"],
        params={"plan_version": found["plan"]["plan_version"]},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["components"], "预览应返回真实组件"
    assert data["review_info"].get("status")
    assert data["content_version"] == found["activity"]["content_version"]
    assert data["editable"] is False
    assert data["editable_reason"]


def test_student_cannot_use_teacher_preview(client: TestClient, ctx: dict) -> None:
    found = _lesson_activity(client, ctx)
    response = client.get(
        f"/api/v1/runs/{ctx['run_id']}/students/S01/activities/{found['activity']['activity_id']}/micro-lesson/preview",
        headers=ctx["student"],
        params={"plan_version": found["plan"]["plan_version"]},
    )
    assert response.status_code == 403
