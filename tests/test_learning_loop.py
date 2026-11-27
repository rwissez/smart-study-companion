"""学习闭环：完成整组 → 组完成事件 → 局部 B → C 复查 → 可见状态。

覆盖 09 的 JOB-03 / B-04 / C-03 / C-04 的机制部分（合成数据）。
解答题在未配置模型时只能 `needs_review`（不给分），因此这里显式区分两条分支：
可规则判分的小组会触发组完成与局部更新；含解答题的小组保持待复核，不伪造完成。
"""

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


def _group_activities(plan: dict, group_id: str) -> list[dict]:
    return [
        activity
        for recommendation in plan["plan"]["recommendations"]
        if recommendation["group_id"] == group_id
        for activity in recommendation["activities"]
    ]


def _question_type(client: TestClient, student: dict, activity: dict) -> str | None:
    if activity["activity_kind"] != "question":
        return None
    response = client.get(f"/api/v1/materials/{activity['material_id']}", headers=student)
    return response.json()["data"].get("question_type")


def _complete(client: TestClient, student: dict, plan: dict, activity: dict) -> dict | None:
    kind = activity["activity_kind"]
    plan_version = plan["plan_version"]
    activity_id = activity["activity_id"]
    if kind == "question":
        start = client.post(
            f"/api/v1/plans/{plan_version}/activities/{activity_id}/start", headers=student
        ).json()["data"]
        attempt = start["attempt"]
        response = client.post(
            f"/api/v1/attempts/{attempt['attempt_id']}/submit",
            headers={**student, "Idempotency-Key": f"sub-{attempt['attempt_id']}"},
            json={"final_answer": "B", "unanswered": False},
        )
        return response.json()["data"]
    if kind == "micro_lesson":
        run_id = plan["plan"]["run_id"]
        version = activity["content_version"]
        view = client.get(
            f"/api/v1/runs/{run_id}/students/S01/activities/{activity_id}/micro-lesson",
            headers=student,
            params={"plan_version": plan_version},
        )
        assert view.status_code == 200, view.text
        response = client.post(
            f"/api/v1/runs/{run_id}/students/S01/activities/{activity_id}/micro-lesson/complete",
            headers=student,
            json={"plan_version": plan_version, "content_version": version},
        )
        assert response.status_code == 200, response.text
        return response.json()["data"]
    response = client.post(
        f"/api/v1/plans/{plan_version}/activities/{activity_id}/complete", headers=student
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_group_completion_triggers_local_update(client: TestClient, imported: dict) -> None:
    dataset_id = imported["dataset_id"]
    student = {"Authorization": f"Bearer {_session(client, role='student', dataset_id=dataset_id, student_id='S01')}"}
    teacher = {"Authorization": f"Bearer {_session(client, role='teacher', dataset_id=dataset_id)}"}

    plan = client.get(f"/api/v1/datasets/{dataset_id}/students/S01/plan", headers=student).json()["data"]
    plan["plan"]["run_id"] = plan["plan"]["run_id"]

    chosen: dict | None = None
    for group in plan["plan"]["task_groups"]:
        activities = _group_activities(plan, group["group_id"])
        types = [_question_type(client, student, item) for item in activities]
        if activities and all(item in {None, "choice", "fill_blank"} for item in types):
            chosen = {"group": group, "activities": activities}
            break
    assert chosen is not None, "方案中应至少有一个可规则判分的小组"

    for activity in chosen["activities"]:
        _complete(client, student, plan, activity)

    events = client.get(
        f"/api/v1/datasets/{dataset_id}/students/S01/learning-events", headers=teacher
    ).json()["data"]
    types = [item["event_type"] for item in events["items"]]
    assert "group_completed" in types
    assert "micro_lesson_completed" in types

    processed = drain()
    assert processed >= 1
    jobs = client.get(f"/api/v1/jobs?dataset_id={dataset_id}", headers=teacher).json()["data"]
    updates = [item for item in jobs["items"] if item["kind"] == "student_update"]
    assert updates, "组完成应触发局部更新任务"
    latest = updates[0]
    assert latest["status"] == "succeeded", latest
    assert latest["result"]["student_id"] == "S01"


def test_solution_question_stays_pending_without_model(client: TestClient, imported: dict) -> None:
    dataset_id = imported["dataset_id"]
    student = {"Authorization": f"Bearer {_session(client, role='student', dataset_id=dataset_id, student_id='S02')}"}
    plan = client.get(f"/api/v1/datasets/{dataset_id}/students/S02/plan", headers=student).json()["data"]
    solution = None
    for group in plan["plan"]["task_groups"]:
        for activity in _group_activities(plan, group["group_id"]):
            if _question_type(client, student, activity) == "solution":
                solution = activity
                break
        if solution:
            break
    if solution is None:
        pytest.skip("该学生方案中没有解答题")
    data = _complete(client, student, plan, solution)
    assert data is not None
    assert data["grade"]["status"] in {"needs_review", "pending"}
    assert data["grade"]["score"] is None, "未评出分数不能显示 0 分"
    assert data["attempt"]["status"] != "completed"


def test_micro_lesson_serves_fixed_component_schema(client: TestClient, imported: dict) -> None:
    dataset_id = imported["dataset_id"]
    student = {"Authorization": f"Bearer {_session(client, role='student', dataset_id=dataset_id, student_id='S01')}"}
    plan = client.get(f"/api/v1/datasets/{dataset_id}/students/S01/plan", headers=student).json()["data"]
    lessons = [
        activity
        for group in plan["plan"]["task_groups"]
        for activity in _group_activities(plan, group["group_id"])
        if activity["activity_kind"] == "micro_lesson"
    ]
    assert lessons, "方案应包含已审核微课堂"
    activity = lessons[0]
    view = client.get(
        f"/api/v1/runs/{plan['plan']['run_id']}/students/S01/activities/{activity['activity_id']}/micro-lesson",
        headers=student,
        params={"plan_version": plan["plan_version"]},
    )
    assert view.status_code == 200, view.text
    body = view.json()["data"]
    allowed = {"text", "formula", "derivation", "contrast", "number_line", "citation"}
    assert body["components"]
    for component in body["components"]:
        assert component["type"] in allowed
        if component["type"] == "text":
            assert 1 <= len(component["paragraphs"]) <= 4
        if component["type"] == "citation":
            for label in component["labels"]:
                assert (label.get("source_index") is None) != (label.get("evidence_index") is None)
