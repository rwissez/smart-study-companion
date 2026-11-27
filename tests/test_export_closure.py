"""正式导出：依赖闭包、预检与案例校验的回归（06 §8—§9）。

全部使用六表合成 fixture；真实赛方案例属 REL-02，需授权数据另行核对。
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.db import models
from study_companion.db import version_repository as repo
from study_companion.exports import service
from study_companion.jobs.worker import drain

FIXTURES = Path(__file__).parent / "fixtures" / "six_tables"
TABLES = ("requirements", "materials", "students", "nodes", "records", "dictionary")


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #


def _teacher(client: TestClient, dataset_id: str | None = None) -> dict[str, str]:
    payload: dict[str, Any] = {"role": "teacher"}
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


def _run_id(client: TestClient, dataset_id: str, headers: dict[str, str]) -> str:
    return client.get(
        f"/api/v1/datasets/{dataset_id}/runs", headers=headers
    ).json()["data"]["items"][0]["run_id"]


def _export(
    client: TestClient,
    headers: dict[str, str],
    run_id: str,
    case_ids: list[str] | None = None,
) -> dict:
    body: dict[str, Any] = {"format": "zip"}
    if case_ids is not None:
        body["case_student_ids"] = case_ids
    response = client.post(f"/api/v1/runs/{run_id}/exports", headers=headers, json=body)
    assert response.status_code == 202, response.text
    return response.json()["data"]


def _detail(client: TestClient, headers: dict[str, str], export_id: str) -> dict:
    return client.get(f"/api/v1/exports/{export_id}", headers=headers).json()["data"]


def _archive(client: TestClient, headers: dict[str, str], export_id: str) -> zipfile.ZipFile:
    response = client.get(f"/api/v1/exports/{export_id}/download", headers=headers)
    assert response.status_code == 200, response.text
    return zipfile.ZipFile(io.BytesIO(response.content))


def _dataset(session: Session, dataset_id: str) -> models.Dataset:
    row = session.get(models.Dataset, dataset_id)
    assert row is not None
    return row


def _current_plan(
    session: Session, dataset: models.Dataset, student_id: str, node_id: str
) -> models.Version:
    version = repo.read_formal(
        session,
        repo.FormalKey(
            family_id=dataset.family_id or "",
            dataset_id=dataset.id,
            kind="plan",
            node_key=node_id,
            target_key=student_id,
        ),
    )
    assert version is not None
    return version


def _published(session: Session, version_id: str) -> bool:
    return (
        session.scalar(
            select(models.Publication.publication_id).where(
                models.Publication.version_id == version_id
            )
        )
        is not None
    )


def _record_counts() -> dict[str, int]:
    from study_companion.db.session import get_sessionmaker

    session = get_sessionmaker()()
    try:
        return {
            "exports": len(session.scalars(select(models.Export)).all()),
            "jobs": len(session.scalars(select(models.Job)).all()),
            "versions": len(session.scalars(select(models.Version)).all()),
            "publications": len(session.scalars(select(models.Publication)).all()),
            "artifact_edges": len(session.scalars(select(models.ArtifactEdge)).all()),
            "formal_heads": len(session.scalars(select(models.FormalHead)).all()),
            "learning_events": len(session.scalars(select(models.LearningEventRow)).all()),
            "attempts": len(session.scalars(select(models.AttemptRow)).all()),
            "grades": len(session.scalars(select(models.Grade)).all()),
        }
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# 依赖闭包与 manifest
# --------------------------------------------------------------------------- #


def test_manifest_dependency_closure_and_content_are_package_scoped(
    client: TestClient, session: Session
) -> None:
    receipt = _import(client)
    dataset = _dataset(session, receipt["dataset_id"])
    headers = _teacher(client, dataset.id)
    run_id = _run_id(client, dataset.id, headers)

    export = _export(client, headers, run_id, ["S01"])
    assert drain() >= 1
    detail = _detail(client, headers, export["export_id"])
    assert detail["status"] == "ready"
    manifest = detail["manifest"]

    edges = manifest["dependency_edges"]
    assert edges, "正式包必须带真实依赖边"
    assert edges == sorted(edges, key=lambda item: (item["from"], item["to"], item["role"]))
    assert len(edges) == len({(item["from"], item["to"], item["role"]) for item in edges})
    for edge in edges:
        assert edge["from"] and edge["to"] and edge["role"]
        for version_id in (edge["from"], edge["to"]):
            row = session.get(models.Version, version_id)
            assert row is not None, f"依赖边指向不存在的版本 {version_id}"
            assert row.family_id == dataset.family_id
            assert _published(session, version_id)

    with _archive(client, headers, export["export_id"]) as archive:
        recommendations = json.loads(archive.read("recommendations.json"))["items"]
        content_versions = {
            activity["content_version"]
            for record in recommendations
            for activity in record["activities"]
            if activity.get("content_version")
        }
        assert content_versions, "该案例方案应引用微课堂内容"
        assert set(manifest["versions"]["content"]) == content_versions
        all_lessons = set(
            session.scalars(
                select(models.Version.version_id).where(
                    models.Version.dataset_id == dataset.id,
                    models.Version.kind == "lesson",
                )
            ).all()
        )
        assert content_versions <= all_lessons

        evidence_keys = {
            item["key"]
            for item in json.loads(archive.read("evidence_records.json"))["items"]
        }
        sample_input = json.loads(archive.read("examples/input.json"))["items"]
        assert sample_input
        assert sample_input[0]["record_keys"], "案例输入应列出该学生该节点的官方记录"
        assert set(sample_input[0]["record_keys"]) <= evidence_keys

        learning = json.loads(archive.read("learning_history.json"))["items"]
        assert all(item["kind"] == "event" for item in learning)
        assert any("在线尝试" in item for item in manifest["limitations"])

    # 当前方案 + 父方案都在闭包里，历史记录显式 current=false。
    assert len(manifest["versions"]["plans"]) >= len(
        {record["plan_version"] for record in recommendations if record["current"]}
    )
    assert any(record["current"] is False for record in recommendations)
    assert detail["check_report"]["case_selection"]["mode"] == "explicit"
    assert detail["check_report"]["dependency_edges"] == len(edges)


def test_auto_case_selection_is_noted(client: TestClient) -> None:
    receipt = _import(client)
    headers = _teacher(client, receipt["dataset_id"])
    run_id = _run_id(client, receipt["dataset_id"], headers)

    export = _export(client, headers, run_id)
    assert drain() >= 1
    detail = _detail(client, headers, export["export_id"])
    assert detail["check_report"]["case_selection"]["mode"] == "auto"
    assert "自动选取" in detail["check_report"]["case_selection"]["note"]
    assert any("自动选取" in item for item in detail["manifest"]["limitations"])
    assert detail["manifest"]["coverage"]["cases"] == 2

    with _archive(client, headers, export["export_id"]) as archive:
        cases = json.loads(archive.read("examples/input.json"))["items"]
    assert {item["case_id"] for item in cases} == {"S01", "S02"}


# --------------------------------------------------------------------------- #
# 预检
# --------------------------------------------------------------------------- #


def test_preflight_is_read_only_and_reports_blocking(client: TestClient) -> None:
    receipt = _import(client)
    headers = _teacher(client, receipt["dataset_id"])
    run_id = _run_id(client, receipt["dataset_id"], headers)
    before = _record_counts()

    ready = client.get(
        f"/api/v1/runs/{run_id}/exports/preflight", headers=headers
    )
    assert ready.status_code == 200, ready.text
    data = ready.json()["data"]
    assert data["ready"] is True
    assert data["blocking"] == []
    assert data["coverage"] == {"students": 3, "classes": 2, "cases": 2}
    assert {item["code"] for item in data["warnings"]} >= {"CASE_AUTO_SELECTED"}

    # 非法案例学生与越界节点只返回 blocking，不落库、不入队。
    invalid = client.get(
        f"/api/v1/runs/{run_id}/exports/preflight",
        headers=headers,
        params={"case_student_ids": "S-不存在,N01", "node_ids": "N99"},
    )
    assert invalid.status_code == 200, invalid.text
    payload = invalid.json()["data"]
    assert payload["ready"] is False
    codes = {(item["code"], item["object_key"]) for item in payload["blocking"]}
    assert ("CASE_STUDENT_NOT_IN_DATASET", "S-不存在") in codes
    assert ("NODE_OUT_OF_RUN", "N99") in codes
    for finding in payload["blocking"]:
        assert {"code", "object_key", "expected", "remedy"} <= set(finding)

    assert _record_counts() == before, "预检不得写任何导出、任务或版本记录"

    student_token = client.post(
        "/api/v1/demo-sessions",
        json={
            "role": "student",
            "dataset_id": receipt["dataset_id"],
            "student_id": "S01",
        },
    ).json()["data"]["session_token"]
    forbidden = client.get(
        f"/api/v1/runs/{run_id}/exports/preflight",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert forbidden.status_code == 403


# --------------------------------------------------------------------------- #
# 阻断：未发布、跨系列、悬空引用、非法案例
# --------------------------------------------------------------------------- #


def _add_dependency(session: Session, plan_version: str, dep_version_id: str) -> None:
    session.add(
        models.ArtifactEdge(
            version_id=plan_version,
            dep_version_id=dep_version_id,
            role="student_analysis",
            object_key="",
        )
    )
    session.commit()


def test_unpublished_and_cross_family_dependencies_block_export(
    client: TestClient, session: Session
) -> None:
    receipt = _import(client)
    dataset = _dataset(session, receipt["dataset_id"])
    headers = _teacher(client, dataset.id)
    run_id = _run_id(client, dataset.id, headers)
    plan = _current_plan(session, dataset, "S01", "N01")

    broken = models.Version(
        version_id="ban-broken",
        kind="student_analysis",
        family_id=dataset.family_id or "",
        dataset_id=dataset.id,
        node_key="N01",
        target_key="S01",
        payload_json={},
        created_at=repo.now_iso(),
        origin_kind="system",
    )
    session.add(broken)
    session.flush()
    _add_dependency(session, plan.version_id, broken.version_id)

    unpublished = client.post(
        f"/api/v1/runs/{run_id}/exports",
        headers=headers,
        json={"format": "zip", "case_student_ids": ["S01"]},
    )
    assert unpublished.status_code == 422, unpublished.text
    assert unpublished.json()["error"]["code"] == "EXPORT_INCOMPLETE"
    details = unpublished.json()["error"]["details"]
    assert any(
        item["code"] == "DEPENDENCY_UNPUBLISHED" and item["object_key"] == "ban-broken"
        for item in details
    )

    # 让同一版本换个系列并补发布证明：仍应被跨系列检查拦住。
    session.add(
        models.DatasetFamily(
            family_id="family-other",
            label="另一系列",
            data_origin="official",
            created_at=repo.now_iso(),
        )
    )
    session.flush()
    broken.family_id = "family-other"
    session.add(
        models.Publication(
            publication_id="pub-broken",
            version_id=broken.version_id,
            previous_version_id=None,
            review_id="",
            path="system_auto",
            basis="automatic_review",
            published_at=repo.now_iso(),
        )
    )
    session.commit()

    cross = client.post(
        f"/api/v1/runs/{run_id}/exports",
        headers=headers,
        json={"format": "zip", "case_student_ids": ["S01"]},
    )
    assert cross.status_code == 422, cross.text
    cross_details = cross.json()["error"]["details"]
    assert any(
        item["code"] == "DEPENDENCY_CROSS_FAMILY" and item["object_key"] == "ban-broken"
        for item in cross_details
    )


def test_dangling_content_reference_blocks_export(
    client: TestClient, session: Session
) -> None:
    receipt = _import(client)
    dataset = _dataset(session, receipt["dataset_id"])
    headers = _teacher(client, dataset.id)
    run_id = _run_id(client, dataset.id, headers)
    plan = _current_plan(session, dataset, "S01", "N01")

    payload = json.loads(json.dumps(plan.payload_json))
    replaced = False
    for recommendation in payload.get("recommendations", []):
        for activity in recommendation.get("activities", []):
            if activity.get("content_version"):
                activity["content_version"] = "lessonv-missing"
                replaced = True
                break
        if replaced:
            break
    assert replaced, "该案例方案应含微课堂内容引用"
    plan.payload_json = payload
    session.commit()

    response = client.post(
        f"/api/v1/runs/{run_id}/exports",
        headers=headers,
        json={"format": "zip", "case_student_ids": ["S01"]},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "EXPORT_INCOMPLETE"
    details = response.json()["error"]["details"]
    assert any(
        item["code"] == "DEPENDENCY_DANGLING"
        and item["object_key"] == "lessonv-missing"
        for item in details
    )


def test_case_student_requires_dataset_analysis_and_plan(
    client: TestClient, session: Session
) -> None:
    receipt = _import(client)
    dataset = _dataset(session, receipt["dataset_id"])
    headers = _teacher(client, dataset.id)
    run_id = _run_id(client, dataset.id, headers)

    # 名册内新增一名没有正式结果的学生：属于数据集，但没有分析也没有方案。
    session.add(
        models.Student(
            dataset_id=dataset.id,
            student_id="S99",
            class_id="DC01",
            student_uid="uid-s99",
        )
    )
    session.commit()

    response = client.post(
        f"/api/v1/runs/{run_id}/exports",
        headers=headers,
        json={"format": "zip", "case_student_ids": ["S99"]},
    )
    assert response.status_code == 422, response.text
    details = response.json()["error"]["details"]
    assert any(
        item["code"] == "CASE_STUDENT_NO_ANALYSIS" and item["object_key"] == "S99"
        for item in details
    )
    assert any(
        item["code"] == "STUDENT_ANALYSIS_MISSING"
        and item["object_key"] == "S99/N01"
        for item in details
    )


# --------------------------------------------------------------------------- #
# 适用性、兼容性与冻结稳定性
# --------------------------------------------------------------------------- #


def test_applicability_and_compatibility_are_package_scoped(
    client: TestClient, session: Session
) -> None:
    receipt = _import(client)
    dataset = _dataset(session, receipt["dataset_id"])
    headers = _teacher(client, dataset.id)
    run_id = _run_id(client, dataset.id, headers)
    plan = _current_plan(session, dataset, "S01", "N01")
    # 包外方案：新建一个未发布到任何 head 的版本，确保它不在导出闭包里。
    other_plan = models.Version(
        version_id="plan-outside-package",
        kind="plan",
        family_id=dataset.family_id or "",
        dataset_id=dataset.id,
        node_key="N01",
        target_key="S01",
        schema_version="1.1",
        rule_version="c-rules-1",
        config_hash="",
        input_digest="",
        body_hash="",
        payload_json={"analysis_version": (plan.payload_json or {}).get("analysis_version", "")},
        created_at=repo.now_iso(),
        origin_kind="system",
    )
    session.add(other_plan)
    session.flush()
    knowledge = repo.read_formal(
        session,
        repo.FormalKey(
            family_id=dataset.family_id or "", dataset_id=dataset.id, kind="knowledge"
        ),
    )
    catalog = repo.read_formal(
        session,
        repo.FormalKey(
            family_id=dataset.family_id or "", dataset_id=dataset.id, kind="catalog"
        ),
    )
    assert knowledge is not None and catalog is not None

    session.add(
        models.PlanApplicability(
            applicability_id="appl-package",
            plan_version=plan.version_id,
            target_analysis_version=plan.payload_json["analysis_version"],
            target_knowledge_version=knowledge.version_id,
            target_catalog_version=catalog.version_id,
            policy_hash="policy-1",
            event_cutoff=6,
            activity_digest="digest-package",
            checked_at=repo.now_iso(),
            result="applicable",
            activity_ids_json=[],
            evidence_refs_json=[],
            limitations_json=[],
        )
    )
    session.add(
        models.PlanApplicability(
            applicability_id="appl-other",
            plan_version=other_plan.version_id,
            target_analysis_version=other_plan.payload_json["analysis_version"],
            target_knowledge_version=knowledge.version_id,
            target_catalog_version=catalog.version_id,
            policy_hash="policy-1",
            event_cutoff=6,
            activity_digest="digest-other",
            checked_at=repo.now_iso(),
            result="incompatible",
            activity_ids_json=[],
            evidence_refs_json=[],
            limitations_json=[],
        )
    )
    session.add(
        models.VersionCompatibility(
            kind="knowledge",
            from_version=knowledge.version_id,
            to_version=knowledge.version_id,
            scope_hash="scope-package",
            policy_hash="policy-1",
            scope_json={},
            reason="同一知识版本的展示名调整",
            proof_refs_json=[],
            checked_at=repo.now_iso(),
        )
    )
    session.add(
        models.VersionCompatibility(
            kind="plan",
            from_version=other_plan.version_id,
            to_version=other_plan.version_id,
            scope_hash="scope-other",
            policy_hash="policy-1",
            scope_json={},
            reason="包外方案",
            proof_refs_json=[],
            checked_at=repo.now_iso(),
        )
    )
    session.commit()

    export = _export(client, headers, run_id, ["S01"])
    assert drain() >= 1
    manifest = _detail(client, headers, export["export_id"])["manifest"]
    assert "appl-package" in manifest["applicability_ids"]
    assert "appl-other" not in manifest["applicability_ids"]
    assert manifest["compatibility_refs"] == [
        f"knowledge:{knowledge.version_id}:{knowledge.version_id}:scope-package:policy-1"
    ]

    with _archive(client, headers, export["export_id"]) as archive:
        records = json.loads(archive.read("recommendations.json"))["items"]
    current = [item for item in records if item["current"] and item["student_id"] == "S01"]
    # 初次发布也会写一条真实适用性记录；包内取最新一条，且必须属于本包集合。
    assert current and current[0]["applicability"]["applicability_id"] in manifest["applicability_ids"]


def test_same_freeze_is_unchanged_after_new_grade(
    client: TestClient, session: Session
) -> None:
    receipt = _import(client)
    dataset = _dataset(session, receipt["dataset_id"])
    headers = _teacher(client, dataset.id)
    run_id = _run_id(client, dataset.id, headers)
    student = session.get(models.Student, (dataset.id, "S01"))
    assert student is not None and student.student_uid
    plan = _current_plan(session, dataset, "S01", "N01")

    export = _export(client, headers, run_id, ["S01"])
    assert drain() >= 1
    frozen = session.get(models.Export, export["export_id"])
    assert frozen is not None
    original_files = service.build_files(session, frozen)

    with _archive(client, headers, export["export_id"]) as archive:
        original_history = archive.read("learning_history.json")
    assert original_files["learning_history.json"] == original_history

    # 冻结之后才出现的在线尝试与评分修订。
    time.sleep(0.02)
    created_at = datetime.now(UTC).isoformat(timespec="milliseconds")
    attempt = models.AttemptRow(
        attempt_id="att-after-freeze",
        plan_version=plan.version_id,
        activity_id="act-after-freeze",
        student_uid=student.student_uid,
        catalog_version=dataset.id,
        material_id="Q1",
        status="completed",
        submission_no=1,
        assistance_status="independent",
        created_at=created_at,
        submitted_at=created_at,
        answer_json={"final_answer": "B"},
    )
    session.add(attempt)
    session.flush()
    session.add(
        models.Grade(
            grade_id="grade-after-freeze",
            attempt_id=attempt.attempt_id,
            revision=1,
            status="graded",
            validation_status="validated",
            score=5,
            max_score=5,
            payload_json={
                "method": "rule",
                "rubric_scores": [],
                "limitations": [],
            },
            created_at=created_at,
        )
    )
    session.commit()

    rebuilt = service.build_files(session, frozen)
    assert rebuilt["learning_history.json"] == original_history, "旧冻结不得吸收新成绩"
    assert rebuilt["student_analysis.json"] == original_files["student_analysis.json"]
    assert rebuilt["micro_lessons.json"] == original_files["micro_lessons.json"]

    newer = _export(client, headers, run_id, ["S01"])
    assert drain() >= 1
    with _archive(client, headers, newer["export_id"]) as archive:
        history = json.loads(archive.read("learning_history.json"))["items"]
        newer_manifest = json.loads(archive.read("manifest.json"))
    kinds = {item["kind"] for item in history}
    assert {"attempt", "grade"} <= kinds
    attempt_item = next(item for item in history if item["kind"] == "attempt")
    assert attempt_item["data"]["attempt_id"] == "att-after-freeze"
    grade_item = next(item for item in history if item["kind"] == "grade")
    assert grade_item["data"]["score"] == 5
    assert not any("没有在线尝试" in item for item in newer_manifest["limitations"])

def test_export_includes_plan_for_every_selected_node(client: TestClient) -> None:
    """多节点 run：每个选中节点都要有对应方案，不能只导出第一个节点。"""

    receipt = _import(client)
    headers = _teacher(client, receipt["dataset_id"])
    run_id = _run_id(client, receipt["dataset_id"], headers)
    export = _export(client, headers, run_id, ["S01"])
    assert drain() >= 1
    with _archive(client, headers, export["export_id"]) as archive:
        records = json.loads(archive.read("recommendations.json"))["items"]
        manifest = json.loads(archive.read("manifest.json"))
    nodes = {item["node_id"] for item in records if item["student_id"] == "S01"}
    assert nodes == set(manifest["node_ids"]), f"方案节点 {nodes} 与导出范围 {manifest['node_ids']} 不一致"
