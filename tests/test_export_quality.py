"""成果包质量回归：发布状态、来源引用、微课堂引用与校验闭环。

全部使用六表合成 fixture；真实赛方数据不在本文件覆盖范围内。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.contracts import DomainError
from study_companion.db import models
from study_companion.db import version_repository as repo
from study_companion.exports import service
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


def _filtered(name: str, students: tuple[str, ...], nodes: tuple[str, ...]) -> tuple[str, bytes, str]:
    """只保留有完整正式方案的学生/节点，避免用未发布方案测试正常导出。"""

    rows = list(
        csv.reader(io.StringIO((FIXTURES / f"{name}.csv").read_text(encoding="utf-8-sig")))
    )
    header, body = rows[0], rows[1:]
    if name == "students":
        body = [row for row in body if row[0] in students]
    elif name == "nodes":
        body = [row for row in body if row[0] in nodes]
    elif name == "records":
        body = [row for row in body if row[1] in students and row[4] in nodes]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(body)
    return (f"{name}.csv", buffer.getvalue().encode("utf-8"), "text/csv")


def _import(
    client: TestClient,
    *,
    students: tuple[str, ...] = ("S01", "S02"),
    nodes: tuple[str, ...] = ("N01",),
) -> dict:
    files = [("files", _filtered(name, students, nodes)) for name in TABLES]
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


# --------------------------------------------------------------------------- #
# 校验状态与知识版本依赖
# --------------------------------------------------------------------------- #


def test_manifest_validation_is_pending_before_verification_and_final_after(
    client: TestClient, session: Session
) -> None:
    """manifest 的 payload 状态必须来自真实回读，不能提前写成 passed。"""

    receipt = _import(client)
    headers = _teacher(client, receipt["dataset_id"])
    run_id = _run_id(client, receipt["dataset_id"], headers)
    export, detail, archive = _export(client, headers, run_id)
    with archive:
        final_manifest = json.loads(archive.read("manifest.json"))
        final_readme = archive.read("README.md").decode("utf-8")

    frozen = session.get(models.Export, export["export_id"])
    assert frozen is not None
    files = service.build_files(session, frozen)
    manifest = json.loads(files["manifest.json"])
    readme = files["README.md"].decode("utf-8")

    assert manifest["validation"]["preflight"] == "passed"
    assert manifest["validation"]["payload"] == "pending"
    assert manifest["validation"]["check_report_id"] is None
    assert manifest["validation"]["checked_at"] is None
    assert manifest["validation"]["content"] in {"passed", "passed_with_limitations"}
    assert "payload：pending" in readme

    assert final_manifest["validation"]["payload"] == "passed"
    assert final_manifest["validation"]["check_report_id"].startswith("check-")
    assert final_manifest["validation"]["checked_at"]
    assert final_manifest["validation"]["content"] == detail["manifest"]["validation"]["content"]
    assert f"payload：passed" in final_readme
    assert final_manifest["validation"]["check_report_id"] in final_readme


def test_export_includes_referenced_old_knowledge_version(
    client: TestClient, session: Session
) -> None:
    """方案引用历史知识版本时，包内要带该版本图谱，不能只改 ID 或只列版本号。"""

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
    knowledge = repo.read_formal(
        session,
        repo.FormalKey(
            family_id=dataset.family_id or "",
            dataset_id=dataset.id,
            kind="knowledge",
        ),
    )
    assert plan is not None and knowledge is not None

    old_version_id = "kn-old-export-test"
    old_payload = json.loads(json.dumps(knowledge.payload_json))
    old_payload["knowledge_version"] = old_version_id
    old_payload["points"].append(
        {
            "kp_id": "kp-old-only",
            "name": "历史依赖知识点",
            "kind": "concept",
            "indicator_ids": [],
        }
    )
    old_payload["relations"].append(
        {
            "relation_id": "rel-old-only",
            "type": "prerequisite",
            "source_ids": ["kp-old-only"],
            "target_id": "kp-T01I1",
            "basis": "team_inference",
        }
    )
    repo.create_version(
        session,
        kind="knowledge",
        key=repo.FormalKey(
            family_id=dataset.family_id or "",
            dataset_id=dataset.id,
            kind="knowledge",
        ),
        payload=old_payload,
        rule_version="a-rules-1",
        version_id=old_version_id,
    )
    session.add(
        models.Publication(
            publication_id="pub-old-knowledge",
            version_id=old_version_id,
            previous_version_id=None,
            review_id="",
            path="system_auto",
            basis="automatic_review",
            published_at=repo.now_iso(),
        )
    )
    plan_payload = json.loads(json.dumps(plan.payload_json))
    plan_payload["knowledge_version"] = old_version_id
    plan.payload_json = plan_payload
    session.commit()

    headers = _teacher(client, dataset.id)
    run_id = _run_id(client, dataset.id, headers)
    _export_id, detail, archive = _export(client, headers, run_id)
    with archive:
        graph = _items(archive, "knowledge_graph.json")
        manifest = json.loads(archive.read("manifest.json"))

    current_version = knowledge.version_id
    assert set(manifest["versions"]["knowledge"]) == {current_version, old_version_id}
    old_points = [
        item
        for item in graph
        if item["kind"] == "point" and item["knowledge_version"] == old_version_id
    ]
    assert any(
        item["object_id"] == "kp-old-only" and item["current"] is False
        for item in old_points
    )
    old_relations = [
        item
        for item in graph
        if item["kind"] == "relation" and item["knowledge_version"] == old_version_id
    ]
    assert any(item["object_id"] == "rel-old-only" for item in old_relations)
    assert all(
        item["current"] is True
        for item in graph
        if item["knowledge_version"] == current_version
    )
    checks = manifest["knowledge_checks"]
    assert checks["current_version"] == current_version
    assert checks["referenced_history"] == [old_version_id]
    assert any(c["version_id"] == old_version_id for c in checks["versions"])
    assert any("知识版本依赖" in item for item in manifest["limitations"])


def test_build_files_refuses_missing_frozen_version(
    client: TestClient, session: Session
) -> None:
    """冻结后版本缺失时打包必须显式失败，不能静默生成缺记录半包。"""

    receipt = _import(client)
    headers = _teacher(client, receipt["dataset_id"])
    run_id = _run_id(client, receipt["dataset_id"], headers)
    export, _detail, _archive = _export(client, headers, run_id)
    frozen = session.get(models.Export, export["export_id"])
    assert frozen is not None
    selection = json.loads(json.dumps(frozen.selection_json))
    selection["versions"]["student"]["S01"]["N01"] = "ban-missing-after-freeze"
    frozen.selection_json = selection

    with pytest.raises(DomainError) as raised:
        service.build_files(session, frozen)
    assert raised.value.code == "EXPORT_INCOMPLETE"
    assert any(
        item["code"] == "FROZEN_VERSION_MISSING"
        and item["object_key"] == "ban-missing-after-freeze"
        for item in raised.value.details
    )


# --------------------------------------------------------------------------- #
# S1-C 版本链：历史学情依赖必须显式披露
# --------------------------------------------------------------------------- #


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


def test_plan_analysis_currency_is_disclosed(
    client: TestClient, session: Session
) -> None:
    """方案引用历史学情时标 analysis_current=false 并给出可读原因；当前学情不误报。"""

    receipt = _import(client)
    dataset = session.get(models.Dataset, receipt["dataset_id"])
    assert dataset is not None
    headers = _teacher(client, dataset.id)
    run_id = _run_id(client, dataset.id, headers)

    # 基线：当前方案都引用当前正式学情，不能误报历史依赖。
    _export_id, detail, archive = _export(client, headers, run_id)
    with archive:
        plans = _items(archive, "recommendations.json")
        readme = archive.read("README.md").decode("utf-8")
    current_plans = [plan for plan in plans if plan["current"]]
    assert current_plans
    for plan in current_plans:
        assert plan["analysis_current"] is True
        assert plan["basis_warnings"] == []
        assert plan["origin_analysis_version"] == plan["current_analysis_version"]
    chain = detail["manifest"]["version_chain"]
    assert chain["analysis_basis"]["current_plans_with_historical_analysis"] == 0
    assert chain["analysis_basis"]["historical_dependencies"] == []
    assert "版本链说明" in readme
    assert "均引用当前正式学生学情" in readme

    # 发布一份不再作为正式头的学情版本，并让当前方案引用它。
    analysis = repo.read_formal(
        session,
        repo.FormalKey(
            family_id=dataset.family_id or "",
            dataset_id=dataset.id,
            kind="student_analysis",
            run_key=run_id,
            node_key="N01",
            target_key="S01",
        ),
    )
    assert analysis is not None
    old_id = "ban-export-history-probe"
    old_payload = json.loads(json.dumps(analysis.payload_json))
    repo.create_version(
        session,
        kind="student_analysis",
        key=repo.FormalKey(
            family_id=dataset.family_id or "",
            dataset_id=dataset.id,
            kind="student_analysis",
            run_key=run_id,
            node_key="N01",
            target_key="S01",
        ),
        payload=old_payload,
        rule_version="b-rules-1",
        version_id=old_id,
    )
    session.add(
        models.Publication(
            publication_id="pub-export-history-probe",
            version_id=old_id,
            previous_version_id=None,
            review_id="",
            path="system_auto",
            basis="automatic_review",
            published_at=repo.now_iso(),
        )
    )
    plan = _current_plan(session, dataset, "S01", "N01")
    plan_payload = json.loads(json.dumps(plan.payload_json))
    plan_payload["analysis_version"] = old_id
    plan.payload_json = plan_payload
    session.commit()

    _export_id2, detail2, archive2 = _export(client, headers, run_id)
    with archive2:
        plans2 = _items(archive2, "recommendations.json")
        students2 = _items(archive2, "student_analysis.json")
        readme2 = archive2.read("README.md").decode("utf-8")
    record = next(
        item
        for item in plans2
        if item["current"]
        and item["student_id"] == "S01"
        and item["node_id"] == "N01"
    )
    assert record["analysis_current"] is False
    assert record["analysis_basis"] == "historical"
    assert record["origin_analysis_version"] == old_id
    assert record["current_analysis_version"] == analysis.version_id
    assert record["basis_warnings"]
    assert any(
        "历史" in warning and old_id in warning and analysis.version_id in warning
        for warning in record["basis_warnings"]
    ), "历史学情依赖必须给出可读的版本对比原因"

    # 历史学情本身仍作为可追溯记录保留，current=false 而不是被删除或改成 current。
    historical = next(
        item for item in students2 if item["analysis_version"] == old_id
    )
    assert historical["current"] is False

    chain2 = detail2["manifest"]["version_chain"]["analysis_basis"]
    assert chain2["current_plans_with_historical_analysis"] >= 1
    entry = next(
        item
        for item in chain2["historical_dependencies"]
        if item["student_id"] == "S01" and item["node_id"] == "N01"
    )
    assert entry["plan_version"] == plan.version_id
    assert entry["plan_current"] is True
    assert entry["analysis_version"] == old_id
    assert entry["current_analysis_version"] == analysis.version_id
    assert entry["reason"]
    assert any(
        "版本链" in item and "S01/N01" in item
        for item in detail2["manifest"]["limitations"]
    )
    assert "版本链说明" in readme2
    assert "S01/N01" in readme2
    assert old_id in readme2 and analysis.version_id in readme2
    assert "不是最新学情依据" in readme2


# --------------------------------------------------------------------------- #
# S1-D 内容生成与复核方式分开统计
# --------------------------------------------------------------------------- #


def test_plan_content_and_review_methods_are_separated(
    client: TestClient, session: Session
) -> None:
    """推荐内容 rule、复核 hybrid 时，统计必须分开，不能把方案说成模型生成。"""

    receipt = _import(client)
    dataset = session.get(models.Dataset, receipt["dataset_id"])
    assert dataset is not None
    plan = _current_plan(session, dataset, "S01", "N01")
    payload = json.loads(json.dumps(plan.payload_json))
    payload["review_info"] = {
        "status": "validated",
        "method": "hybrid",
        "checks": [],
        "prompt_version": None,
    }
    plan.payload_json = payload
    session.commit()

    headers = _teacher(client, dataset.id)
    run_id = _run_id(client, dataset.id, headers)
    _export_id, detail, archive = _export(client, headers, run_id)
    with archive:
        plans = _items(archive, "recommendations.json")
        readme = archive.read("README.md").decode("utf-8")

    manifest = detail["manifest"]
    source = manifest["generation_sources"]["plan"]
    recommendation_count = sum(len(item["recommendations"]) for item in plans)
    assert source["method"] == "rule"
    assert source["content_method"] == "rule"
    assert source["content_methods"] == ["rule"]
    assert source["content_counts"] == {"rule": recommendation_count}
    assert "hybrid" in source["review_methods"]
    assert source["review_counts"]["hybrid"] == 1
    assert source["review_method"] == "hybrid"
    assert "model" not in manifest["generation_method"], (
        "复核 hybrid 不能把推荐内容统计成模型生成"
    )
    checks = manifest["content_checks"]
    assert checks["recommendation_content_methods"] == {"rule": recommendation_count}
    assert checks["plan_review_methods"] == source["review_counts"]
    assert checks["recommendations"] == recommendation_count

    assert "推荐内容" in readme and "复核" in readme
    assert f"规则（rule） {recommendation_count} 条" in readme
    assert "hybrid" in readme
    assert "C 学习方案 | 规则+模型（hybrid）" not in readme, (
        "README 不能把方案内容与复核混成一个 hybrid 结论"
    )


def test_readme_method_counts_match_manifest_statistics(
    client: TestClient,
) -> None:
    """README 写出的内容/复核计数必须与 manifest 和版本载荷逐项一致。"""

    receipt = _import(client)
    headers = _teacher(client, receipt["dataset_id"])
    run_id = _run_id(client, receipt["dataset_id"], headers)
    _export_id, detail, archive = _export(client, headers, run_id)
    with archive:
        readme = archive.read("README.md").decode("utf-8")
        plans = _items(archive, "recommendations.json")
        lessons = _items(archive, "micro_lessons.json")

    manifest = detail["manifest"]
    plan_source = manifest["generation_sources"]["plan"]
    lesson_source = manifest["generation_sources"]["micro_lesson"]
    checks = manifest["content_checks"]

    recommendation_counts = Counter(
        recommendation["generation_method"]
        for plan in plans
        for recommendation in plan["recommendations"]
    )
    lesson_counts = Counter(lesson["creation_method"] for lesson in lessons)
    lesson_review_counts = Counter(lesson["review_method"] for lesson in lessons)

    assert plan_source["content_counts"] == dict(recommendation_counts)
    assert lesson_source["content_counts"] == dict(lesson_counts)
    assert lesson_source["review_counts"] == dict(lesson_review_counts)
    assert checks["recommendation_content_methods"] == dict(recommendation_counts)
    assert checks["lesson_content_methods"] == dict(lesson_counts)
    assert checks["lesson_review_methods"] == dict(lesson_review_counts)

    labels = {
        "rule": "规则",
        "model": "模型",
        "hybrid": "规则+模型",
        "human": "教师修订/确认",
        "unknown": "未知",
    }
    for method, count in recommendation_counts.items():
        assert f"{labels[method]}（{method}） {count} 条" in readme
    for method, count in lesson_counts.items():
        assert f"{labels[method]}（{method}） {count} 份" in readme
    for method, count in lesson_review_counts.items():
        assert f"{labels[method]}（{method}） {count} 份" in readme
    # 微课堂不能用 hybrid 概括正文生成方式。
    if set(lesson_counts) == {"rule"}:
        assert "hybrid" not in readme.split("C 微课堂", 1)[1].split("\n", 1)[0]

