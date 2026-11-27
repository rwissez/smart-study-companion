"""Agent 候选、确认与教师审核（05 §7、04 §8 agent_revision）。"""

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
def teacher_ctx(client: TestClient) -> dict:
    bootstrap = {"Authorization": f"Bearer {_session(client, role='teacher')}"}
    files = [
        ("files", (f"{name}.csv", (FIXTURES / f"{name}.csv").read_bytes(), "text/csv"))
        for name in TABLES
    ]
    receipt = client.post(
        "/api/v1/imports",
        headers=bootstrap,
        files=files,
        data={"metadata": '{"intent":"new_family"}'},
    ).json()["data"]
    drain()
    token = _session(client, role="teacher", dataset_id=receipt["dataset_id"])
    return {"dataset_id": receipt["dataset_id"], "headers": {"Authorization": f"Bearer {token}"}}


def _conversation(client: TestClient, ctx: dict) -> str:
    response = client.post(
        "/api/v1/chat",
        headers=ctx["headers"],
        json={
            "message": "我想调整这个学生的安排",
            "target": {"type": "student", "id": "S01"},
            "node_id": "N01",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["conversation_id"]


def test_create_proposal_and_confirm_publishes_agent_revision(client: TestClient, teacher_ctx: dict) -> None:
    conversation_id = _conversation(client, teacher_ctx)
    before = client.get(
        f"/api/v1/datasets/{teacher_ctx['dataset_id']}/students/S01/plan",
        headers=teacher_ctx["headers"],
        params={"node_id": "N01"},
    ).json()["data"]["plan_version"]

    proposed = client.post(
        f"/api/v1/conversations/{conversation_id}/proposals",
        headers=teacher_ctx["headers"],
        json={"student_id": "S01", "node_id": "N01", "intent": "缩短本轮活动"},
    )
    assert proposed.status_code == 202, proposed.text
    data = proposed.json()["data"]
    assert data["status"] == "needs_confirmation"
    confirmation_id = data["confirmation"]["confirmation_id"]

    confirmed = client.post(
        f"/api/v1/conversations/{conversation_id}/confirm",
        headers=teacher_ctx["headers"],
        params={"node_id": "N01"},
        json={"confirmation_id": confirmation_id, "allow": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    operations = confirmed.json()["data"]["operations"]
    assert operations and operations[0]["status"] == "succeeded"
    assert operations[0]["publication_status"] == "published"
    new_version = operations[0]["new_plan_version"]
    assert new_version != before

    plan = client.get(
        f"/api/v1/datasets/{teacher_ctx['dataset_id']}/students/S01/plan",
        headers=teacher_ctx["headers"],
        params={"node_id": "N01"},
    ).json()["data"]
    assert plan["plan_version"] == new_version

    again = client.post(
        f"/api/v1/conversations/{conversation_id}/confirm",
        headers=teacher_ctx["headers"],
        params={"node_id": "N01"},
        json={"confirmation_id": confirmation_id, "allow": True},
    )
    assert again.status_code == 409, "同一确认不能重复执行"


def test_rejecting_confirmation_keeps_original_plan(client: TestClient, teacher_ctx: dict) -> None:
    conversation_id = _conversation(client, teacher_ctx)
    before = client.get(
        f"/api/v1/datasets/{teacher_ctx['dataset_id']}/students/S01/plan",
        headers=teacher_ctx["headers"],
        params={"node_id": "N01"},
    ).json()["data"]["plan_version"]
    proposed = client.post(
        f"/api/v1/conversations/{conversation_id}/proposals",
        headers=teacher_ctx["headers"],
        json={"student_id": "S01", "node_id": "N01"},
    ).json()["data"]
    rejected = client.post(
        f"/api/v1/conversations/{conversation_id}/confirm",
        headers=teacher_ctx["headers"],
        params={"node_id": "N01"},
        json={"confirmation_id": proposed["confirmation"]["confirmation_id"], "allow": False},
    )
    assert rejected.status_code == 200
    assert rejected.json()["data"]["decision"] == "rejected"
    after = client.get(
        f"/api/v1/datasets/{teacher_ctx['dataset_id']}/students/S01/plan",
        headers=teacher_ctx["headers"],
        params={"node_id": "N01"},
    ).json()["data"]["plan_version"]
    assert after == before, "拒绝候选不得改变正式方案"


def test_teacher_can_approve_agent_candidate_via_plan_reviews(client: TestClient, teacher_ctx: dict) -> None:
    conversation_id = _conversation(client, teacher_ctx)
    proposed = client.post(
        f"/api/v1/conversations/{conversation_id}/proposals",
        headers=teacher_ctx["headers"],
        json={"student_id": "S01", "node_id": "N01"},
    ).json()["data"]
    proposal_id = proposed["proposal"]["proposal_id"]

    listed = client.get(
        f"/api/v1/datasets/{teacher_ctx['dataset_id']}/plan-reviews", headers=teacher_ctx["headers"]
    ).json()["data"]
    assert any(item["review_id"] == proposal_id for item in listed["items"])

    approved = client.post(
        f"/api/v1/datasets/{teacher_ctx['dataset_id']}/plan-reviews/{proposal_id}",
        headers=teacher_ctx["headers"],
        params={"node_id": "N01"},
        json={"decision": "approve", "reason": "审核通过"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["data"]["publication_status"] == "published"
