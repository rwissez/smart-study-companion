"""知识体系重建：任务化、发布新版本、不覆盖历史（02 §7）。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.db import models
from study_companion.jobs.worker import drain


def _session(client: TestClient, **payload) -> str:
    return client.post("/api/v1/demo-sessions", json=payload).json()["data"]["session_token"]


def test_rebuild_enqueues_job_and_publishes_new_version(
    client: TestClient, seeded: dict, session: Session
) -> None:
    dataset_id = seeded["dataset_id"]
    dataset = session.get(models.Dataset, dataset_id)
    if dataset is not None and not dataset.family_id:
        family = models.DatasetFamily(
            family_id="fam-rebuild-test",
            label="test",
            data_origin="synthetic",
            created_at="2026-11-27T00:00:00+00:00",
            generation=0,
        )
        session.add(family)
        session.flush()
        dataset.family_id = family.family_id
        session.commit()
    token = _session(client, role="teacher", dataset_id=dataset_id)
    headers = {"Authorization": f"Bearer {token}"}
    before = session.scalar(
        select(models.Version.version_id)
        .where(models.Version.dataset_id == dataset_id, models.Version.kind == "knowledge")
        .order_by(models.Version.created_at.desc())
        .limit(1)
    )
    response = client.post(
        f"/api/v1/datasets/{dataset_id}/knowledge/rebuild", headers=headers
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["data"]["job_id"]
    drain()
    job = client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()["data"]
    assert job["status"] == "succeeded", job
    result = job.get("result_json") or job.get("result") or {}
    assert result.get("published") is True
    new_version = result.get("knowledge_version")
    assert new_version and new_version != before

    head = session.scalar(
        select(models.FormalHead).where(
            models.FormalHead.dataset_id == dataset_id,
            models.FormalHead.kind == "knowledge",
        )
    )
    assert head is not None and head.version_id == new_version
    # 历史版本仍存在（若本次之前没有正式知识版本，则跳过）
    if before:
        old = session.get(models.Version, before)
        assert old is not None and old.payload_json


def test_rebuild_requires_teacher_capability(client: TestClient, seeded: dict) -> None:
    dataset_id = seeded["dataset_id"]
    token = _session(client, role="student", dataset_id=dataset_id, student_id="S01")
    response = client.post(
        f"/api/v1/datasets/{dataset_id}/knowledge/rebuild",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
