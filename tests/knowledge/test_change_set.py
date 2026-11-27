"""知识版本差异与影响集（02 §11）。"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.db import models
from study_companion.jobs.worker import drain
from study_companion.knowledge.change_set import diff_payloads, impact_of

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "six_tables"
TABLES = ("requirements", "materials", "students", "nodes", "records", "dictionary")


def _session(client: TestClient, **payload) -> str:
    return client.post("/api/v1/demo-sessions", json=payload).json()["data"]["session_token"]


@pytest.fixture
def ctx(client: TestClient, session: Session) -> dict:
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
    knowledge = session.scalars(
        select(models.Version).where(
            models.Version.dataset_id == dataset_id, models.Version.kind == "knowledge"
        )
    ).first()
    assert knowledge is not None
    newer = models.Version(
        version_id="kn-next",
        kind="knowledge",
        family_id=knowledge.family_id,
        dataset_id=dataset_id,
        run_id=None,
        node_key=None,
        target_key=None,
        schema_version=knowledge.schema_version,
        rule_version=knowledge.rule_version,
        config_hash=knowledge.config_hash,
        input_digest="digest-next",
        body_hash="hash-next",
        payload_json={},
        file_id=None,
        job_id=None,
        created_at="2026-11-27T00:00:00+00:00",
        origin_kind="system",
    )
    payload = copy.deepcopy(knowledge.payload_json or {})
    points = payload.get("points") or []
    assert points
    points.append(
        {
            "kp_id": "kp-new",
            "name": "新知识点",
            "aliases": [],
            "kind": "concept",
            "definition": "新知识点定义",
            "scope_role": "core",
            "topic_ids": [points[0]["topic_ids"][0]] if points[0].get("topic_ids") else [],
            "indicator_ids": [points[0]["indicator_ids"][0]] if points[0].get("indicator_ids") else [],
            "snippet": "新知识点",
            "source_locators": [],
            "review_info": {"status": "validated", "method": "rule", "checks": []},
            "content_hash": "hash-new",
        }
    )
    points[0]["definition"] = points[0]["definition"] + "（修订）"
    points[0]["content_hash"] = "hash-changed"
    newer.payload_json = payload
    session.add(newer)
    session.commit()
    return {
        "dataset_id": dataset_id,
        "knowledge_version": knowledge.version_id,
        "teacher": {"Authorization": f"Bearer {_session(client, role='teacher', dataset_id=dataset_id)}"},
    }


def test_diff_and_impact_cover_all_four_kinds() -> None:
    previous = {
        "points": [
            {"kp_id": "kp-a", "name": "甲", "content_hash": "h1", "indicator_ids": ["I1"]},
            {"kp_id": "kp-b", "name": "乙", "content_hash": "h2", "indicator_ids": ["I2"]},
        ],
        "annotations": [
            {
                "annotation_id": "an-1",
                "source_type": "material",
                "source_id": "Q1",
                "target_ids": ["kp-a"],
            }
        ],
    }
    current = {
        "points": [
            {"kp_id": "kp-a2", "name": "甲", "content_hash": "h1", "indicator_ids": ["I1"]},
            {"kp_id": "kp-b", "name": "乙", "content_hash": "h3", "indicator_ids": ["I2"]},
            {"kp_id": "kp-c", "name": "丙", "content_hash": "h4", "indicator_ids": ["I3"]},
        ],
        "annotations": [
            {
                "annotation_id": "an-1",
                "source_type": "material",
                "source_id": "Q1",
                "target_ids": ["kp-a2"],
            }
        ],
    }
    changes = diff_payloads(previous, current)
    kinds = {item.kind for item in changes}
    assert kinds == {"added", "renamed", "changed"}, kinds
    renamed = next(item for item in changes if item.kind == "renamed")
    assert renamed.previous_id == "kp-a" and renamed.object_id == "kp-a2"
    impact = impact_of(changes, current)
    assert "I1" in impact["indicator_ids"] and "I3" in impact["indicator_ids"]
    assert impact["requires_replan"] is True


def test_route_reports_change_set_and_affected_plans(client: TestClient, ctx: dict) -> None:
    response = client.get(
        f"/api/v1/datasets/{ctx['dataset_id']}/knowledge/change-set",
        headers=ctx["teacher"],
        params={"from_version": ctx["knowledge_version"], "to_version": "kn-next"},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["from_version"] == ctx["knowledge_version"]
    assert data["to_version"] == "kn-next"
    assert data["counts"]["added"] == 1
    assert data["counts"]["changed"] >= 1
    assert data["impact"]["requires_replan"] is True
    assert data["affected_plan_versions"], "引用旧知识版本的已发布方案应被列出"


def test_route_rejects_foreign_version(client: TestClient, ctx: dict) -> None:
    response = client.get(
        f"/api/v1/datasets/{ctx['dataset_id']}/knowledge/change-set",
        headers=ctx["teacher"],
        params={"from_version": "missing", "to_version": "kn-next"},
    )
    assert response.status_code == 404
