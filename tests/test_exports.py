"""导出回归：冻结 → 打包 → 回读校验 → 授权下载（ZIP-01/02 的机制部分）。

真实赛方数据的三学生两班案例属 REL-02，需要授权数据，本文件只用合成数据。
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from study_companion.jobs.worker import drain

FIXTURES = Path(__file__).parent / "fixtures" / "six_tables"
TABLES = ("requirements", "materials", "students", "nodes", "records", "dictionary")


def _teacher(client: TestClient, dataset_id: str | None = None) -> dict:
    payload = {"role": "teacher"}
    if dataset_id:
        payload["dataset_id"] = dataset_id
    token = client.post("/api/v1/demo-sessions", json=payload).json()["data"]["session_token"]
    return {"Authorization": f"Bearer {token}"}


def _import(client: TestClient) -> dict:
    files = [
        ("files", (f"{name}.csv", (FIXTURES / f"{name}.csv").read_bytes(), "text/csv"))
        for name in TABLES
    ]
    receipt = client.post(
        "/api/v1/imports",
        headers=_teacher(client),
        files=files,
        data={"metadata": '{"intent":"new_family"}'},
    ).json()["data"]
    drain()
    return receipt


def test_export_zip_contains_required_files_and_hashes(client: TestClient) -> None:
    receipt = _import(client)
    headers = _teacher(client, receipt["dataset_id"])
    run = client.get(
        f"/api/v1/datasets/{receipt['dataset_id']}/runs", headers=headers
    ).json()["data"]["items"][0]["run_id"]

    created = client.post(
        f"/api/v1/runs/{run}/exports",
        headers={**headers, "Idempotency-Key": "export-test-1"},
        json={"format": "zip", "case_student_ids": ["S01"]},
    )
    assert created.status_code == 202, created.text
    payload = created.json()["data"]
    assert payload["status"] == "queued"
    assert drain() >= 1

    detail = client.get(f"/api/v1/exports/{payload['export_id']}", headers=headers).json()["data"]
    assert detail["status"] == "ready"
    assert detail["check_report"]["passed"] is True

    download = client.get(f"/api/v1/exports/{payload['export_id']}/download", headers=headers)
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        names = set(archive.namelist())
        for required in (
            "manifest.json",
            "README.md",
            "curriculum.json",
            "knowledge_graph.json",
            "student_analysis.json",
            "class_analysis.json",
            "recommendations.json",
            "metrics.csv",
            "examples/input.json",
            "examples/output.json",
        ):
            assert required in names
        manifest = json.loads(archive.read("manifest.json"))
        for item in manifest["files"]:
            assert len(archive.read(item["path"])) == item["bytes"]


def test_export_rejects_unpublished_scope_and_unauthorized_student(
    client: TestClient,
) -> None:
    receipt = _import(client)
    headers = _teacher(client, receipt["dataset_id"])
    run = client.get(
        f"/api/v1/datasets/{receipt['dataset_id']}/runs", headers=headers
    ).json()["data"]["items"][0]["run_id"]

    rejected = client.post(
        f"/api/v1/runs/{run}/exports?published_only=false",
        headers=headers,
        json={"format": "zip"},
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "EXPORT_SCOPE_INVALID"

    missing = client.post(
        f"/api/v1/runs/{run}/exports",
        headers=headers,
        json={"format": "zip", "case_student_ids": ["S-不存在"]},
    )
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "EXPORT_INCOMPLETE"

    student_token = client.post(
        "/api/v1/demo-sessions",
        json={"role": "student", "dataset_id": receipt["dataset_id"], "student_id": "S01"},
    ).json()["data"]["session_token"]
    forbidden = client.post(
        f"/api/v1/runs/{run}/exports",
        headers={"Authorization": f"Bearer {student_token}"},
        json={"format": "zip"},
    )
    assert forbidden.status_code == 403
