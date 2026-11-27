"""依赖索引写入：版本实际引用的输入（06 §4.3）。"""

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


def test_versions_record_their_real_inputs(imported: dict, session: Session) -> None:
    dataset_id = imported["dataset_id"]
    plan_ids = [
        row.version_id
        for row in session.scalars(
            select(models.Version).where(
                models.Version.dataset_id == dataset_id, models.Version.kind == "plan"
            )
        ).all()
    ]
    assert plan_ids
    uses = session.scalars(
        select(models.ArtifactUse).where(models.ArtifactUse.version_id.in_(plan_ids))
    ).all()
    assert uses, "方案版本必须登记实际引用的输入"
    kinds = {item.input_kind for item in uses}
    assert {"analysis", "catalog"} <= kinds
    for item in uses:
        assert item.input_key
        assert item.ref_hash
        assert item.family_id

    analysis_ids = [
        row.version_id
        for row in session.scalars(
            select(models.Version).where(
                models.Version.dataset_id == dataset_id,
                models.Version.kind == "student_analysis",
            )
        ).all()
    ]
    analysis_uses = session.scalars(
        select(models.ArtifactUse).where(models.ArtifactUse.version_id.in_(analysis_ids))
    ).all()
    assert analysis_uses, "学情版本也要登记目录依赖"


def test_artifact_use_is_idempotent(imported: dict, session: Session) -> None:
    plan = session.scalars(
        select(models.Version).where(
            models.Version.dataset_id == imported["dataset_id"],
            models.Version.kind == "plan",
        )
    ).first()
    assert plan is not None
    from study_companion.db import version_repository as repo

    before = session.scalars(
        select(models.ArtifactUse).where(models.ArtifactUse.version_id == plan.version_id)
    ).all()
    written = repo.record_artifact_use(
        session,
        version_id=plan.version_id,
        family_id=plan.family_id or "",
        inputs=[("analysis", before[0].input_key, {"version_id": before[0].input_key}, "input")]
        if before
        else [],
    )
    assert written == 0, "同一输入同一用途不重复登记"
