"""端到端合成用例：教师导入 → 任务 → 学生读方案/作答/提示 → 教师读画像。

这些用例使用 tests/fixtures/six_tables 的**合成**六表数据，覆盖：
* 导入 → A(基础目录) → B → C 的正式发布链（JOB-01/02、DB-04 的机制部分）；
* 学生只能读本人（SEC-01）；
* 选项题规则评分、提示事件、活动完成与事件账本（B-02、JOB-03 机制）；
* 未提交前不下发答案（SEC-02 的路由层）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from study_companion.jobs.worker import drain

FIXTURES = Path(__file__).parent / "fixtures" / "six_tables"
TABLES = ("requirements", "materials", "students", "nodes", "records", "dictionary")


def _teacher_token(client: TestClient, dataset_id: str | None = None) -> str:
    payload = {"role": "teacher"}
    if dataset_id:
        payload["dataset_id"] = dataset_id
    response = client.post("/api/v1/demo-sessions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["data"]["session_token"]


def _student_token(client: TestClient, dataset_id: str, student_id: str) -> str:
    response = client.post(
        "/api/v1/demo-sessions",
        json={"role": "student", "dataset_id": dataset_id, "student_id": student_id},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["session_token"]


@pytest.fixture
def imported(client: TestClient) -> dict:
    headers = {"Authorization": f"Bearer {_teacher_token(client)}"}
    files = [
        ("files", (f"{name}.csv", (FIXTURES / f"{name}.csv").read_bytes(), "text/csv"))
        for name in TABLES
    ]
    response = client.post(
        "/api/v1/imports",
        headers=headers,
        files=files,
        data={"metadata": '{"intent":"new_family"}'},
    )
    assert response.status_code == 202, response.text
    receipt = response.json()["data"]
    assert receipt["classification_status"] == "confirmed"
    assert receipt["input_status"] == "ready"
    assert receipt["dataset_id"]
    drain()
    return receipt


def test_import_runs_pipeline_and_publishes_plans(client: TestClient, imported: dict) -> None:
    headers = {"Authorization": f"Bearer {_teacher_token(client, imported['dataset_id'])}"}
    job = client.get(f"/api/v1/jobs/{imported['job_id']}", headers=headers).json()["data"]
    assert job["status"] == "succeeded"
    assert [step["step_key"] for step in job["steps"]] == [
        "load",
        "knowledge",
        "analysis",
        "plans",
    ]
    assert all(step["status"] == "succeeded" for step in job["steps"])
    assert job["result"]["published_plan_count"] >= 1

    profile = client.get(
        f"/api/v1/datasets/{imported['dataset_id']}/students/S01/profile", headers=headers
    )
    assert profile.status_code == 200, profile.text
    result = profile.json()["data"]["result"]
    assert result["student_id"] == "S01"
    assert result["metrics"], "个人画像必须包含真实统计指标"
    # B 的指标按 (学生, 节点, 指标) 发布：T01I1 = Q1 5/5，T01I2 = Q2 0/5。
    by_indicator = {
        item["scope"]["indicator_id"]: item
        for item in result["metrics"]
        if item["name"] == "valid_score_ratio"
    }
    assert by_indicator["T01I1"]["numerator"] == 5
    assert by_indicator["T01I1"]["denominator"] == 5
    assert by_indicator["T01I1"]["value"] == 1.0
    assert by_indicator["T01I2"]["numerator"] == 0
    assert by_indicator["T01I2"]["denominator"] == 5
    assert by_indicator["T01I2"]["value"] == 0.0  # 真实 0 分，不是"缺失"


def test_class_profile_keeps_roster_denominator(client: TestClient, imported: dict) -> None:
    headers = {"Authorization": f"Bearer {_teacher_token(client, imported['dataset_id'])}"}
    response = client.get(
        f"/api/v1/datasets/{imported['dataset_id']}/classes/DC01/profile", headers=headers
    )
    assert response.status_code == 200, response.text
    result = response.json()["data"]["result"]
    assert result["roster_count"] == 2  # S01 + S03（S03 无记录也不能被剔除）
    assert result["observed_student_count"] == 1


def test_student_cannot_read_another_student(client: TestClient, imported: dict) -> None:
    token = _student_token(client, imported["dataset_id"], "S01")
    response = client.get(
        f"/api/v1/datasets/{imported['dataset_id']}/students/S02/profile",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_student_learning_loop_grades_and_records_events(
    client: TestClient, imported: dict
) -> None:
    dataset_id = imported["dataset_id"]
    token = _student_token(client, dataset_id, "S01")
    student = {"Authorization": f"Bearer {token}"}

    plan_response = client.get(f"/api/v1/datasets/{dataset_id}/students/S01/plan", headers=student)
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()["data"]
    assert plan["plan"]["task_groups"], "正式方案必须至少有一个任务组"

    activity_id = plan["plan"]["task_groups"][0]["activity_ids"][0]
    start = client.post(
        f"/api/v1/plans/{plan['plan_version']}/activities/{activity_id}/start", headers=student
    )
    assert start.status_code == 200, start.text
    attempt = start.json()["data"]["attempt"]
    assert attempt["status"] == "in_progress"

    # 未提交前，材料接口不得返回答案字段
    material = client.get(
        f"/api/v1/materials/{attempt['material_id']}", headers=student
    ).json()["data"]
    assert "reference_answer" not in material
    assert "rubric_points" not in material

    hint = client.post(
        f"/api/v1/attempts/{attempt['attempt_id']}/hints",
        headers=student,
        json={"level": 1},
    )
    assert hint.status_code == 200, hint.text
    assert hint.json()["data"]["hint"]

    submit = client.post(
        f"/api/v1/attempts/{attempt['attempt_id']}/submit",
        headers={**student, "Idempotency-Key": "it-submit-1"},
        json={"final_answer": "B", "unanswered": False},
    )
    assert submit.status_code == 200, submit.text
    data = submit.json()["data"]
    assert data["attempt"]["status"] == "completed"
    assert data["grade"]["status"] in {"graded", "needs_review", "pending"}
    assert data["attempt"]["assistance_status"] == "assisted"

    again = client.post(
        f"/api/v1/attempts/{attempt['attempt_id']}/submit",
        headers={**student, "Idempotency-Key": "it-submit-1"},
        json={"final_answer": "B", "unanswered": False},
    )
    assert again.status_code == 200
    assert again.json()["data"]["attempt"]["submission_no"] == data["attempt"]["submission_no"]

    teacher = {"Authorization": f"Bearer {_teacher_token(client, dataset_id)}"}
    events = client.get(
        f"/api/v1/datasets/{dataset_id}/students/S01/learning-events", headers=teacher
    ).json()["data"]
    types = {item["event_type"] for item in events["items"]}
    assert {"answer_submitted", "grading_completed", "hint_requested", "activity_completed"} <= types


def test_job_retry_requires_failed_job(client: TestClient, imported: dict) -> None:
    headers = {"Authorization": f"Bearer {_teacher_token(client, imported['dataset_id'])}"}
    response = client.post(
        f"/api/v1/jobs/{imported['job_id']}/retry",
        headers={**headers, "Idempotency-Key": "retry-1"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "JOB_RETRY_NOT_AVAILABLE"
