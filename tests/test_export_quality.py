"""成果包质量回归：发布状态、来源引用、微课堂引用与校验闭环。

全部使用六表合成 fixture；真实赛方数据不在本文件覆盖范围内。
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.db import models
from study_companion.db import version_repository as repo
from study_companion.jobs.worker import drain

FIXTURES = Path(__file__).parent / "fixtures" / "six_tables"
TABLES = ("requirements", "materials", "students", "nodes", "records", "dictionary")


def _teacher(client: TestClient, dataset_id: str | None = None) -> dict[str, str]:
    payload: dict[str, Any] = {"role": "teacher"}
    if dataset_id:
        payload["dataset_id"] = dataset_id
    token = client.post("/api/v1/demo-sessions", json=payload).json()["data"][
        "session_token"
    ]
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
    client: TestClient, headers: dict[str, str], run_id: str
) -> tuple[dict, dict, zipfile.ZipFile]:
    response = client.post(
        f"/api/v1/runs/{run_id}/exports",
        headers=headers,
        json={"format": "zip", "case_student_ids": ["S01"]},
    )
    assert response.status_code == 202, response.text
    export = response.json()["data"]
    assert drain() >= 1
    detail = client.get(
        f"/api/v1/exports/{export['export_id']}", headers=headers
    ).json()["data"]
    assert detail["status"] == "ready"
    download = client.get(
        f"/api/v1/exports/{export['export_id']}/download", headers=headers
    )
    assert download.status_code == 200, download.text
    archive = zipfile.ZipFile(io.BytesIO(download.content))
    return export, detail, archive


def _items(archive: zipfile.ZipFile, name: str) -> list[dict[str, Any]]:
    return json.loads(archive.read(name))["items"]


def _published(session: Session, version_id: str) -> models.Publication | None:
    return session.scalar(
        select(models.Publication).where(models.Publication.version_id == version_id)
    )


# --------------------------------------------------------------------------- #
# P0-01 发布状态
# --------------------------------------------------------------------------- #


def test_analysis_publication_status_comes_from_publication_record(
    client: TestClient, session: Session
) -> None:
    receipt = _import(client)
    headers = _teacher(client, receipt["dataset_id"])
    run_id = _run_id(client, receipt["dataset_id"], headers)
    _export_id, _detail, archive = _export(client, headers, run_id)
    with archive:
        students = _items(archive, "student_analysis.json")
        classes = _items(archive, "class_analysis.json")
        plans = _items(archive, "recommendations.json")

    assert students and classes
    student_by_key = {item["key"]: item for item in students}
    for record in students + classes:
        publication = _published(session, record["analysis_version"])
        assert publication is not None, "导出只应包含有发布记录的正式版本"
        assert record["publication_status"] == "published"
        assert record["publication"]["publication_id"] == publication.publication_id
        assert record["result"]["publication_status"] == "published", (
            "result.publication_status 必须按发布记录校正，不能保留载荷自述的 unpublished"
        )

    # 每份当前方案引用的 B 版本都要能在包内解析，并落在已发布记录上。
    for plan in (item for item in plans if item["current"]):
        for field in ("origin_analysis_key", "current_analysis_key"):
            key = plan.get(field) or ""
            assert key, f"当前方案缺少 {field}"
            record = student_by_key.get(key)
            assert record is not None, f"{field} 指向包内不存在的学情记录：{key}"
            assert record["publication_status"] == "published"

    # summary 为空时必须有可核对的空值语义，不能留一个无解释的 null。
    for record in students + classes:
        result = record["result"]
        if result.get("summary") is None:
            assert result["summary_status"] == "not_provided"
            assert result["summary_note"]
        else:
            assert result["summary_status"] == "provided"

    # 导出只读版本载荷，不把发布状态写回原始快照。
    version = session.get(models.Version, students[0]["analysis_version"])
    assert version is not None


# --------------------------------------------------------------------------- #
# P0-02 材料来源与知识点引用
# --------------------------------------------------------------------------- #


def test_material_source_and_kp_refs_are_resolvable(client: TestClient) -> None:
    receipt = _import(client)
    headers = _teacher(client, receipt["dataset_id"])
    run_id = _run_id(client, receipt["dataset_id"], headers)
    _export_id, _detail, archive = _export(client, headers, run_id)
    with archive:
        materials = _items(archive, "materials_index.json")
        knowledge = _items(archive, "knowledge_graph.json")
        curriculum = _items(archive, "curriculum.json")
        plans = _items(archive, "recommendations.json")

    knowledge_points = {
        item["data"]["kp_id"] for item in knowledge if item["kind"] == "point"
    }
    assert materials
    for material in materials:
        assert material["source_refs"], f"{material['material_id']} 缺少出处引用"
        for ref in material["source_refs"]:
            assert ref["material_id"] == material["material_id"]
            assert ref["source_file"]
            assert ref["verification_status"] in {"verified", "unverified"}
        assert material["kp_refs"], f"{material['material_id']} 缺少知识点引用"
        for ref in material["kp_refs"]:
            assert ref["kp_id"] in knowledge_points, "kp_ref 必须指向包内真实知识点"
            assert ref["role"] in {"primary", "supporting"}
            assert ref["source"] in {"annotation", "indicator_mapping"}
        assert material["kp_status"] == "mapped"
        assert any(ref["source"] == "annotation" for ref in material["kp_refs"])

    assert any(
        ref["source"] == "annotation"
        for material in materials
        for ref in material["kp_refs"]
    ), "至少一条材料关联来自正式知识标注"

    # 当前目录没有难度评估时保持 unknown，并写清理由，不临时编等级。
    for material in materials:
        assert material["difficulty_level"] == "unknown"
        assert material["difficulty_status"] == "unknown"
        assert material["difficulty_reason"]

    for requirement in curriculum:
        assert requirement["source_refs"]
        assert requirement["source_refs"][0]["curriculum_ref"]
        assert requirement["source_refs"][0]["curriculum_page"]
        assert requirement["source_refs"][0]["source"] == "requirements_table"

    material_ids = {material["material_id"] for material in materials}
    for plan in plans:
        for activity in plan["activities"]:
            if activity.get("material_id"):
                assert activity["material_id"] in material_ids
            for kp_id in activity.get("knowledge_ids") or []:
                assert kp_id in knowledge_points


# --------------------------------------------------------------------------- #
# P0-03 微课堂来源与引用索引
# --------------------------------------------------------------------------- #


def test_micro_lesson_refs_and_citation_indices_resolve(client: TestClient) -> None:
    receipt = _import(client)
    headers = _teacher(client, receipt["dataset_id"])
    run_id = _run_id(client, receipt["dataset_id"], headers)
    _export_id, _detail, archive = _export(client, headers, run_id)
    with archive:
        lessons = _items(archive, "micro_lessons.json")

    assert lessons
    evidence_only = 0
    for lesson in lessons:
        assert lesson["evidence_refs"], "微课堂必须导出正文 evidence_refs 的安全投影"
        for ref in lesson["evidence_refs"]:
            assert ref["kind"] and ref["id"]
        assert isinstance(lesson["source_refs"], list)
        assert lesson["source_status"] in {"verified", "unverified", "evidence_only"}
        assert lesson["citation_verified"] is (lesson["source_status"] == "verified")
        if lesson["source_status"] == "evidence_only":
            evidence_only += 1
            assert any("未引用可核验教材原文" in item for item in lesson["limitations"])
        for check in lesson["citation_checks"]:
            assert check["labels"], "引用组件标签不能为空"
            for label in check["labels"]:
                assert label["resolved"] is True, "引用索引必须能在 source_refs/evidence_refs 内解析"
                if label["source_index"] is not None:
                    assert label["source_index"] < len(lesson["source_refs"])
                if label["evidence_index"] is not None:
                    assert label["evidence_index"] < len(lesson["evidence_refs"])
    assert evidence_only, "合成数据没有教材来源时，微课堂应标 evidence_only 而不是假引用"


# --------------------------------------------------------------------------- #
# P1-06 回读校验闭环
# --------------------------------------------------------------------------- #


def test_manifest_payload_validation_is_traceable(client: TestClient) -> None:
    receipt = _import(client)
    headers = _teacher(client, receipt["dataset_id"])
    run_id = _run_id(client, receipt["dataset_id"], headers)
    _export_id, detail, archive = _export(client, headers, run_id)
    with archive:
        manifest_data = archive.read("manifest.json")
        manifest = json.loads(manifest_data)
        for item in manifest["files"]:
            content = archive.read(item["path"])
            assert hashlib.sha256(content).hexdigest() == item["sha256"]
            assert len(content) == item["bytes"]

    validation = manifest["validation"]
    assert validation["preflight"] == "passed"
    assert validation["payload"] == "passed", "ZIP 内 manifest 不能停留在 pending"
    assert validation["content"] in {"passed", "passed_with_limitations"}
    for issue in validation["content_issues"]:
        assert isinstance(issue, str) and issue
    assert validation["check_report_id"].startswith("check-")
    assert validation["checked_at"]
    assert "sha256" in validation["checks"]
    assert "json_parse" in validation["checks"]
    assert validation["issues"] == []
    assert detail["check_report"]["passed"] is True
    assert detail["check_report"]["report_id"] == validation["check_report_id"]
    assert detail["check_report"]["checked_at"] == validation["checked_at"]
    assert detail["check_report"]["content_status"] == validation["content"]
    assert detail["check_report"]["manifest_sha256"] == hashlib.sha256(
        manifest_data
    ).hexdigest()


# --------------------------------------------------------------------------- #
# P1-07 / P1-08 README 与数据来源一致
# --------------------------------------------------------------------------- #


def test_readme_generation_and_data_origin_are_module_level(
    client: TestClient, session: Session
) -> None:
    receipt = _import(client)
    dataset_id = receipt["dataset_id"]
    headers = _teacher(client, dataset_id)
    run_id = _run_id(client, dataset_id, headers)
    _export_id, detail, archive = _export(client, headers, run_id)
    with archive:
        readme = archive.read("README.md").decode("utf-8")

    manifest = detail["manifest"]
    assert "未调用模型" not in readme
    assert "生成方式（按模块记录）" in readme
    assert "导出打包" in readme and "A 知识体系" in readme
    assert set(manifest["generation_sources"]) == {
        "export",
        "knowledge",
        "student_analysis",
        "plan",
        "micro_lesson",
    }
    assert manifest["generation_sources"]["export"]["method"] == "rule"
    assert manifest["generation_method"] == ["rule"]

    origin = manifest["data_origin"]
    assert isinstance(origin, dict)
    assert origin["kind"] in {"official", "synthetic", "sample", "mixed", "unknown"}
    assert origin["dataset_id"] == dataset_id
    assert isinstance(origin["synthetic"], bool)
    assert origin["note"]
    assert "数据来源：ready" not in readme
    assert f"类型：{origin['kind']}" in readme

    # 给知识版本加上模型复核标记，再给学情加上模型超时限制：
    # 导出必须按版本载荷如实记录，不能继续写“未调用模型”。
    dataset = session.get(models.Dataset, dataset_id)
    assert dataset is not None
    knowledge = repo.read_formal(
        session,
        repo.FormalKey(
            family_id=dataset.family_id or "",
            dataset_id=dataset_id,
            kind="knowledge",
        ),
    )
    assert knowledge is not None
    payload = json.loads(json.dumps(knowledge.payload_json))
    payload["points"][0]["review_info"] = {
        "status": "validated",
        "method": "model",
        "checks": [],
        "prompt_version": "kp-extract-v1",
    }
    knowledge.payload_json = payload

    student_version = session.scalar(
        select(models.Version)
        .where(
            models.Version.dataset_id == dataset_id,
            models.Version.kind == "student_analysis",
            models.Version.node_key == "N01",
            models.Version.target_key == "S01",
        )
        .order_by(models.Version.created_at.desc())
        .limit(1)
    )
    assert student_version is not None
    student_payload = json.loads(json.dumps(student_version.payload_json))
    student_payload["limitations"] = list(student_payload.get("limitations") or []) + [
        "模型语义诊断未成功（MODEL_TIMEOUT），已保留确定性统计与评分，不生成模型解释。"
    ]
    student_version.payload_json = student_payload
    session.commit()

    _export_id2, detail2, archive2 = _export(client, headers, run_id)
    with archive2:
        readme2 = archive2.read("README.md").decode("utf-8")
    manifest2 = detail2["manifest"]
    knowledge_source = manifest2["generation_sources"]["knowledge"]
    assert knowledge_source["method"] in {"model", "hybrid"}
    assert knowledge_source["model_tagged_point_count"] >= 1
    assert "model" in manifest2["generation_method"]
    analysis_source = manifest2["generation_sources"]["student_analysis"]
    assert analysis_source["model_attempted"] is True
    assert analysis_source["method"] == "rule", "模型超时不能写成模型成功"
    assert "未成功" in analysis_source["note"]
    assert "未调用模型" not in readme2


# --------------------------------------------------------------------------- #
# P1-04 / P1-05 manifest 限制说明
# --------------------------------------------------------------------------- #


def test_manifest_notes_missing_group_fields_and_difficulty(
    client: TestClient,
) -> None:
    receipt = _import(client)
    headers = _teacher(client, receipt["dataset_id"])
    run_id = _run_id(client, receipt["dataset_id"], headers)
    _export_id, detail, archive = _export(client, headers, run_id)
    with archive:
        plans = _items(archive, "recommendations.json")
        materials = _items(archive, "materials_index.json")
        students = _items(archive, "student_analysis.json")
        classes = _items(archive, "class_analysis.json")

    groups = [group for plan in plans for group in plan["groups"]]
    assert groups
    # 任务组字段为空时，正式方案自带的理由仍要原样投影，便于核对而不是补写。
    for plan in plans:
        if not plan["groups"]:
            continue
        assert plan["recommendations"], "有任务组的方案不能丢失建议列表"
        for recommendation in plan["recommendations"]:
            assert recommendation["reason"]
            assert recommendation["strategy_types"]
    missing_reason = sum(1 for group in groups if not group["teacher_reason"].strip())
    missing_strategy = sum(
        1 for group in groups if group["primary_strategy"] is None
    )
    limitations = "\n".join(detail["manifest"]["limitations"])
    if missing_reason:
        assert "teacher_reason" in limitations
        assert "不补写" in limitations
    if missing_strategy:
        assert "primary_strategy" in limitations
    assert "difficulty" in limitations
    checks = detail["manifest"]["content_checks"]
    assert checks["materials_difficulty_unknown"] == len(materials)
    assert checks["activities"] == sum(len(plan["activities"]) for plan in plans)
    assert checks["activities_material_not_in_index"] == 0
    assert checks["activity_kp_refs_not_in_graph"] == 0
    assert checks["groups_missing_teacher_reason"] == missing_reason
    current_groups = [
        group for plan in plans if plan["current"] for group in plan["groups"]
    ]
    assert checks["groups_current"] == len(current_groups)
    assert checks["groups_missing_teacher_reason_current"] == sum(
        1 for group in current_groups if not group["teacher_reason"].strip()
    )
    assert checks["analysis_summary_missing"] == sum(
        1 for item in students + classes if item["result"]["summary"] is None
    )


def test_manifest_explains_empty_task_group_fields(
    client: TestClient, session: Session
) -> None:
    """正式方案任务组字段为空时，导出只保留空值并写限制，不临时补理由。"""

    receipt = _import(client)
    dataset = session.get(models.Dataset, receipt["dataset_id"])
    assert dataset is not None
    plan = repo.read_formal(
        session,
        repo.FormalKey(
            family_id=dataset.family_id or "",
            dataset_id=dataset.id,
            kind="plan",
            node_key="N01",
            target_key="S01",
        ),
    )
    assert plan is not None
    payload = json.loads(json.dumps(plan.payload_json))
    groups = payload.get("task_groups") or []
    assert groups
    for group in groups:
        group["teacher_reason"] = ""
        group["primary_strategy"] = None
    plan.payload_json = payload
    session.commit()

    headers = _teacher(client, dataset.id)
    run_id = _run_id(client, dataset.id, headers)
    _export_id, detail, archive = _export(client, headers, run_id)
    with archive:
        plans = _items(archive, "recommendations.json")
        exported = next(item for item in plans if item["plan_version"] == plan.version_id)
        assert exported["groups"]
        for group in exported["groups"]:
            assert group["teacher_reason"] == ""
            assert group["primary_strategy"] is None
        assert exported["recommendations"][0]["reason"]
        assert exported["recommendations"][0]["strategy_types"]

    limitations = "\n".join(detail["manifest"]["limitations"])
    assert "teacher_reason" in limitations
    assert "primary_strategy" in limitations
    assert "不补写" in limitations
    checks = detail["manifest"]["content_checks"]
    assert checks["groups_missing_teacher_reason"] >= len(groups)
    assert checks["groups_missing_primary_strategy"] >= len(groups)

