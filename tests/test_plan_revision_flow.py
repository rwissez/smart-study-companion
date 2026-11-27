"""教师方案编辑：预览、确认发布与继承活动状态（04 §9.1）。"""

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
    headers = {
        "Authorization": f"Bearer {_session(client, role='teacher', dataset_id=receipt['dataset_id'])}"
    }
    run_id = client.get(
        f"/api/v1/datasets/{receipt['dataset_id']}/runs", headers=headers
    ).json()["data"]["items"][0]["run_id"]
    return {"dataset_id": receipt["dataset_id"], "headers": headers, "run_id": run_id}


def _plan(client: TestClient, ctx: dict, student: str = "S01") -> dict:
    return client.get(
        f"/api/v1/datasets/{ctx['dataset_id']}/students/{student}/plan",
        headers=ctx["headers"],
        params={"node_id": "N01"},
    ).json()["data"]


def test_text_only_revision_publishes_and_keeps_activity_state(
    client: TestClient, ctx: dict, session: Session
) -> None:
    plan = _plan(client, ctx)
    before_states = session.scalars(select(models.ActivityStateRow)).all()
    before_ids = {row.activity_id for row in before_states}

    preview = client.post(
        f"/api/v1/runs/{ctx['run_id']}/students/S01/recommendation-revisions/preview",
        headers=ctx["headers"],
        json={
            "node_id": "N01",
            "base_plan_version": plan["plan_version"],
            "reason": "只调整开场白和理由，活动不变",
            "changes": {"student_intro": "我们从上次那道去括号题继续。"},
        },
    )
    assert preview.status_code == 200, preview.text
    data = preview.json()["data"]

    published = client.post(
        f"/api/v1/runs/{ctx['run_id']}/students/S01/recommendation-revisions/{data['preview_id']}/publish",
        headers=ctx["headers"],
        params={"node_id": "N01"},
        json={"warning_digest": data["warning_digest"]},
    )
    assert published.status_code == 200, published.text
    result = published.json()["data"]
    assert result["publication_status"] == "published"
    assert result["review_status"] == "human_confirmed"
    assert result["plan_version"] != plan["plan_version"]

    current = _plan(client, ctx)
    assert current["plan_version"] == result["plan_version"]
    import json

    assert "我们从上次那道去括号题继续。" in json.dumps(current, ensure_ascii=False)

    after_states = session.scalars(select(models.ActivityStateRow)).all()
    assert {row.activity_id for row in after_states} == before_ids, "继承活动不应重复建状态行"
    assert all(
        row.state == before.state
        for row in after_states
        for before in before_states
        if row.activity_id == before.activity_id
    )


def test_stale_base_version_is_rejected(client: TestClient, ctx: dict) -> None:
    plan = _plan(client, ctx)
    preview = client.post(
        f"/api/v1/runs/{ctx['run_id']}/students/S01/recommendation-revisions/preview",
        headers=ctx["headers"],
        json={
            "node_id": "N01",
            "base_plan_version": plan["plan_version"],
            "reason": "第一次修改",
            "changes": {"student_intro": "第一版"},
        },
    ).json()["data"]
    first = client.post(
        f"/api/v1/runs/{ctx['run_id']}/students/S01/recommendation-revisions/{preview['preview_id']}/publish",
        headers=ctx["headers"],
        params={"node_id": "N01"},
        json={"warning_digest": preview["warning_digest"]},
    )
    assert first.status_code == 200

    stale = client.post(
        f"/api/v1/runs/{ctx['run_id']}/students/S01/recommendation-revisions/{preview['preview_id']}/publish",
        headers=ctx["headers"],
        params={"node_id": "N01"},
        json={"warning_digest": preview["warning_digest"]},
    )
    assert stale.status_code == 409, "同一预览不能重复发布"
