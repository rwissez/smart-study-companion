"""正式成果导出：冻结、序列化、打包、回读校验与授权下载（06 §8—§9）。

关键约束：
* 导出**只读冻结的正式版本集合**，不重算业务、不调用模型；
* 冻结在事务内解析当前正式指针并记录 selection，之后的新版本不会混入本包；
* 打包完成后回读校验每个文件的 SHA-256 与 JSON/CSV 可解析性，再原子落位；
* 下载再次校验当前教师权限与文件哈希，权限撤销后旧包也不再下发。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.contracts import (
    DomainError,
    RecommendationBundle,
    StudentResult,
)
from study_companion.db import models
from study_companion.db import version_repository as repo

SCHEMA_VERSION = "1.1"
EXPORT_FORMAT = "zip"


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp() -> str:
    return _now().isoformat(timespec="milliseconds")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(payload: Any) -> bytes:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    return (text + "\n").encode("utf-8")


def _items(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "items": records}


def _sorted_items(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(records, key=lambda item: str(item.get("key", "")).encode("utf-8"))


def _format_decimal(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.6f}".rstrip("0").rstrip(".") if "." in f"{value:.6f}" else f"{value:.6f}"


# --------------------------------------------------------------------------- #
# 冻结
# --------------------------------------------------------------------------- #


def freeze_export(
    db: Session,
    *,
    run_id: str,
    dataset: models.Dataset,
    node_ids: list[str] | None,
    case_student_ids: list[str] | None,
    actor: str,
) -> models.Export:
    run = db.get(models.AnalysisRun, run_id)
    if run is None:
        raise DomainError("NOT_FOUND", "分析运行不存在")
    if run.dataset_id != dataset.id:
        raise DomainError("FORBIDDEN", "运行不属于该数据集")
    if not dataset.family_id:
        raise DomainError("UNPROCESSABLE", "数据集缺少系列身份")

    available_nodes = list(run.node_ids_json or [])
    selected_nodes = node_ids or available_nodes
    if not selected_nodes:
        raise DomainError("EXPORT_INCOMPLETE", "该运行没有可导出的测验节点")
    for node in selected_nodes:
        if node not in available_nodes:
            raise DomainError(
                "EXPORT_INCOMPLETE",
                f"节点 {node} 不在该运行范围内",
                [{"field": "node_ids", "reason": "节点不属于本次运行"}],
            )

    students = db.scalars(
        select(models.Student.student_id).where(models.Student.dataset_id == dataset.id)
    ).all()
    if not students:
        raise DomainError("EXPORT_INCOMPLETE", "该数据集没有学生名册")

    student_versions: dict[str, dict[str, str]] = {}
    missing: list[dict[str, str]] = []
    for node in selected_nodes:
        for student_id in students:
            key = repo.FormalKey(
                family_id=dataset.family_id,
                dataset_id=dataset.id,
                kind="student_analysis",
                run_key=run_id,
                node_key=node,
                target_key=student_id,
            )
            version = repo.read_formal(db, key)
            if version is None:
                missing.append({"code": "STUDENT_ANALYSIS_MISSING", "object": f"{student_id}/{node}"})
                continue
            student_versions.setdefault(student_id, {})[node] = version.version_id

    classes = sorted(
        {
            row
            for row in db.scalars(
                select(models.Student.class_id).where(models.Student.dataset_id == dataset.id)
            ).all()
        }
    )
    class_versions: dict[str, dict[str, str]] = {}
    for node in selected_nodes:
        for class_id in classes:
            key = repo.FormalKey(
                family_id=dataset.family_id,
                dataset_id=dataset.id,
                kind="class_analysis",
                run_key=run_id,
                node_key=node,
                target_key=class_id,
            )
            version = repo.read_formal(db, key)
            if version is None:
                missing.append({"code": "CLASS_ANALYSIS_MISSING", "object": f"{class_id}/{node}"})
                continue
            class_versions.setdefault(class_id, {})[node] = version.version_id

    plan_versions: dict[str, str] = {}
    for student_id in students:
        for node in selected_nodes:
            key = repo.FormalKey(
                family_id=dataset.family_id,
                dataset_id=dataset.id,
                kind="plan",
                node_key=node,
                target_key=student_id,
            )
            version = repo.read_formal(db, key)
            if version is not None:
                plan_versions[student_id] = version.version_id
    if not plan_versions:
        missing.append({"code": "PLAN_MISSING", "object": "全部学生都没有正式学习方案"})

    knowledge_version = None
    knowledge_key = repo.FormalKey(
        family_id=dataset.family_id, dataset_id=dataset.id, kind="knowledge"
    )
    knowledge_row = repo.read_formal(db, knowledge_key)
    if knowledge_row is not None:
        knowledge_version = knowledge_row.version_id

    selected_students = case_student_ids or sorted(student_versions)[:3]
    for student_id in selected_students:
        if student_id not in student_versions:
            raise DomainError(
                "EXPORT_INCOMPLETE",
                f"典型案例学生 {student_id} 没有正式学情结果",
                [{"field": "case_student_ids", "reason": "缺少该学生的正式分析"}],
            )
    if missing and any(item["code"] in {"PLAN_MISSING", "CLASS_ANALYSIS_MISSING"} for item in missing):
        raise DomainError(
            "EXPORT_INCOMPLETE",
            "正式成果不完整，不能生成正式包",
            [{"field": item["object"], "reason": item["code"]} for item in missing[:5]],
        )

    selection = {
        "run_id": run_id,
        "dataset_id": dataset.id,
        "dataset_family_id": dataset.family_id,
        "catalog_version": run.catalog_version,
        "rule_version": run.rule_version,
        "node_ids": selected_nodes,
        "case_student_ids": selected_students,
        "versions": {
            "student": student_versions,
            "class": class_versions,
            "plans": plan_versions,
            "knowledge": knowledge_version,
        },
        "event_cutoff": int(run.event_cutoff or 0),
        "created_at": _stamp(),
    }
    selection_hash = _sha(_json_bytes(selection))
    export_id = repo.new_id("export")
    snapshot_id = repo.new_id("snap")

    from study_companion.jobs import enqueue

    job = enqueue(
        db,
        kind="export_build",
        scope={"dataset_id": dataset.id, "export_id": export_id},
        payload={"export_id": export_id, "selection": selection},
        dataset_id=dataset.id,
    )
    row = models.Export(
        export_id=export_id,
        snapshot_id=snapshot_id,
        job_id=job.id,
        run_id=run_id,
        scope_key=_sha(_json_bytes([dataset.id, run_id]))[:32],
        actor_ref=actor,
        selection_json=selection,
        selection_hash=selection_hash,
        event_cutoff=int(run.event_cutoff or 0),
        manifest_json=None,
        storage_key=None,
        archive_hash=None,
        bytes=None,
        check_report_json={"preflight": "passed", "payload": "pending"},
        status="queued",
        created_at=_stamp(),
        updated_at=_stamp(),
    )
    db.add(row)
    db.flush()
    return row


# --------------------------------------------------------------------------- #
# 序列化
# --------------------------------------------------------------------------- #


def _load(db: Session, version_id: str | None) -> dict[str, Any]:
    if not version_id:
        return {}
    row = db.get(models.Version, version_id)
    return dict(row.payload_json or {}) if row is not None else {}


def build_files(db: Session, export: models.Export) -> dict[str, bytes]:
    selection = dict(export.selection_json or {})
    run = db.get(models.AnalysisRun, selection["run_id"])
    dataset = db.get(models.Dataset, selection["dataset_id"])
    catalog_row = db.get(models.Catalog, selection["catalog_version"])
    if run is None or dataset is None or catalog_row is None:
        raise DomainError("EXPORT_INCOMPLETE", "冻结范围引用的对象已不可读")
    node_ids = selection["node_ids"]
    versions = selection["versions"]

    curriculum: list[dict[str, Any]] = []
    for indicator in (catalog_row.payload_json or {}).get("indicators", []):
        curriculum.append(
            {
                "key": f"req:{selection['catalog_version']}:{indicator['indicator_id']}",
                "catalog_version": selection["catalog_version"],
                "topic_id": indicator.get("topic_id"),
                "indicator_id": indicator.get("indicator_id"),
                "title": indicator.get("description", ""),
                "requirement": indicator.get("description", ""),
                "node_ids": node_ids,
                "source_refs": [
                    {
                        "kind": "curriculum",
                        "id": indicator.get("indicator_id"),
                        "catalog_version": selection["catalog_version"],
                    }
                ],
            }
        )

    knowledge: list[dict[str, Any]] = []
    knowledge_payload = _load(db, versions.get("knowledge"))
    for point in knowledge_payload.get("points", []):
        knowledge.append(
            {
                "key": f"kp:{versions.get('knowledge')}:{point['kp_id']}",
                "kind": "point",
                "knowledge_version": versions.get("knowledge"),
                "object_id": point["kp_id"],
                "current": True,
                "data": point,
                "evidence_refs": [],
            }
        )
    for relation in knowledge_payload.get("relations", []):
        knowledge.append(
            {
                "key": f"rel:{versions.get('knowledge')}:{relation['relation_id']}",
                "kind": "relation",
                "knowledge_version": versions.get("knowledge"),
                "object_id": relation["relation_id"],
                "current": True,
                "data": relation,
                "evidence_refs": [],
            }
        )
    for annotation in knowledge_payload.get("annotations", []):
        knowledge.append(
            {
                "key": f"ann:{versions.get('knowledge')}:{annotation['annotation_id']}",
                "kind": "annotation",
                "knowledge_version": versions.get("knowledge"),
                "object_id": annotation["annotation_id"],
                "current": True,
                "data": annotation,
                "evidence_refs": [],
            }
        )

    materials: list[dict[str, Any]] = []
    for material in (catalog_row.payload_json or {}).get("materials", []):
        materials.append(
            {
                "key": f"mat:{selection['catalog_version']}:{material['material_id']}",
                "catalog_version": selection["catalog_version"],
                "material_id": material["material_id"],
                "kind": material.get("material_type"),
                "title": (material.get("content") or "")[:80],
                "node_ids": node_ids,
                "kp_refs": [],
                "source_refs": [],
                "difficulty_level": (material.get("difficulty") or {}).get("level"),
                "difficulty_status": (material.get("difficulty") or {}).get("status"),
            }
        )

    quality: list[dict[str, Any]] = []
    for issue in db.scalars(
        select(models.QualityIssue).where(models.QualityIssue.dataset_id == dataset.id)
    ).all():
        payload = dict(issue.payload_json or {})
        quality.append(
            {
                "key": f"quality:{dataset.id}:{issue.id}",
                "module": "import",
                "scope": {"dataset_id": dataset.id},
                "kind": "issue",
                "code": payload.get("code", "UNKNOWN"),
                "disposition": payload.get("action", "retain"),
                "count": len(payload.get("record_ids", [])),
                "evidence_refs": [],
                "limitations": [payload.get("message", "")],
            }
        )

    student_records: list[dict[str, Any]] = []
    class_records: list[dict[str, Any]] = []
    metric_rows: list[list[str]] = []
    for student_id, node_map in versions["student"].items():
        for node, version_id in node_map.items():
            payload = _load(db, version_id)
            student_records.append(
                {
                    "key": f"student:{version_id}:{student_id}",
                    "student_id": student_id,
                    "node_id": node,
                    "analysis_version": version_id,
                    "current": True,
                    "knowledge_version": versions.get("knowledge"),
                    "catalog_version": selection["catalog_version"],
                    "event_cutoff": selection["event_cutoff"],
                    "result": payload,
                    "limitations": payload.get("limitations", []),
                }
            )
            for metric in payload.get("metrics", []):
                metric_rows.append(
                    [
                        metric.get("metric_id", ""),
                        "student",
                        student_id,
                        node,
                        (metric.get("scope") or {}).get("indicator_id") or "",
                        (metric.get("scope") or {}).get("kp_id") or "",
                        "" if metric.get("numerator") is None else str(metric["numerator"]),
                        "" if metric.get("denominator") is None else str(metric["denominator"]),
                        _format_decimal(metric.get("value")),
                        metric.get("unit", ""),
                        version_id,
                        f"metric:{version_id}:{metric.get('metric_id')}",
                        metric.get("evidence_status") or "",
                        "true",
                    ]
                )
    for class_id, node_map in versions["class"].items():
        for node, version_id in node_map.items():
            payload = _load(db, version_id)
            members = [
                {
                    "student_key": item,
                    "student_analysis_key": versions["student"].get(item, {}).get(node, ""),
                    "member_state": "available"
                    if versions["student"].get(item, {}).get(node)
                    else "no_observation",
                }
                for item in payload.get("member_analysis_versions", [])
            ]
            class_records.append(
                {
                    "key": f"class:{version_id}:{class_id}",
                    "class_id": class_id,
                    "node_id": node,
                    "analysis_version": version_id,
                    "current": True,
                    "result": payload,
                    "members": members,
                    "limitations": payload.get("limitations", []),
                }
            )
            for metric in payload.get("metrics", []):
                metric_rows.append(
                    [
                        metric.get("metric_id", ""),
                        "class",
                        class_id,
                        node,
                        (metric.get("scope") or {}).get("indicator_id") or "",
                        (metric.get("scope") or {}).get("kp_id") or "",
                        "" if metric.get("numerator") is None else str(metric["numerator"]),
                        "" if metric.get("denominator") is None else str(metric["denominator"]),
                        _format_decimal(metric.get("value")),
                        metric.get("unit", ""),
                        version_id,
                        f"metric:{version_id}:{metric.get('metric_id')}",
                        metric.get("evidence_status") or "",
                        "true",
                    ]
                )

    plan_records: list[dict[str, Any]] = []
    for student_id, version_id in versions["plans"].items():
        payload = _load(db, version_id)
        plan = RecommendationBundle.model_validate(payload)
        origin = plan.analysis_version or ""
        current = versions["student"].get(student_id, {}).get(node_ids[0], "")
        plan_records.append(
            {
                "key": f"plan:{version_id}:{student_id}",
                "student_id": student_id,
                "node_id": plan.node_id,
                "plan_version": version_id,
                "origin_analysis_key": origin,
                "current_analysis_key": current,
                "knowledge_version": plan.knowledge_version,
                "catalog_version": plan.catalog_version,
                "review_status": (plan.review_info.status if plan.review_info else "validated"),
                "publication_basis": plan.publication_basis or "automatic_review",
                "applicability": None,
                "groups": [group.model_dump(mode="json") for group in plan.task_groups],
                "activities": [activity.model_dump(mode="json") for activity in
                               (item for rec in plan.recommendations for item in rec.activities)],
                "limitations": plan.limitations,
            }
        )

    evidence: list[dict[str, Any]] = []
    events = db.scalars(
        select(models.AnswerEvent)
        .where(models.AnswerEvent.dataset_id == dataset.id)
        .order_by(models.AnswerEvent.event_id)
        .limit(500)
    ).all()
    for event in events:
        payload = dict(event.payload_json or {})
        record_id = payload.get("selected_record_id") or event.event_id
        evidence.append(
            {
                "key": f"ev:{selection['run_id']}:{record_id}",
                "ref": {"kind": "record", "id": record_id, "run_id": selection["run_id"]},
                "detail_type": "record",
                "detail": {
                    "student_id": event.student_id,
                    "question": {
                        "material_id": event.material_id,
                        "catalog_version": selection["catalog_version"],
                    },
                    "status": payload.get("status", "pending"),
                    "score": payload.get("score"),
                    "max_score": payload.get("max_score"),
                    "rubric_scores": [],
                    "quality_issues": [],
                    "related_findings": [],
                },
                "related_refs": [],
                "limitations": [],
            }
        )

    learning: list[dict[str, Any]] = []
    for row in db.scalars(
        select(models.LearningEventRow)
        .where(models.LearningEventRow.dataset_id == dataset.id)
        .order_by(models.LearningEventRow.offset)
        .limit(500)
    ).all():
        identity = db.get(models.StudentIdentity, row.student_uid)
        learning.append(
            {
                "key": f"event:{row.event_id}",
                "kind": "event",
                "plan_version": row.plan_version or "",
                "activity_id": row.activity_id or "",
                "data": {
                    "event_id": row.event_id,
                    "event_type": row.event_type,
                    "occurred_at": row.occurred_at,
                    "summary": "",
                    "payload": dict(row.payload_json or {}),
                    "student_id": identity.external_id if identity else "",
                },
            }
        )

    micro_lessons: list[dict[str, Any]] = []
    for version in db.scalars(
        select(models.Version).where(
            models.Version.dataset_id == dataset.id, models.Version.kind == "lesson"
        )
    ).all():
        payload = dict(version.payload_json or {})
        micro_lessons.append(
            {
                "key": f"content:{version.version_id}",
                "content_id": payload.get("content_id"),
                "content_version": version.version_id,
                "activity_id": "",
                "plan_version": payload.get("origin_plan_version"),
                "body": payload.get("body"),
                "review_status": (payload.get("review_info") or {}).get("status"),
                "evidence_refs": [],
            }
        )

    case_ids = selection["case_student_ids"]
    input_items: list[dict[str, Any]] = []
    output_items: list[dict[str, Any]] = []
    for student_id in case_ids:
        node = node_ids[0]
        version_id = versions["student"].get(student_id, {}).get(node, "")
        plan_version = versions["plans"].get(student_id, "")
        input_items.append(
            {
                "key": f"in:{student_id}",
                "case_id": student_id,
                "student_id": student_id,
                "node_id": node,
                "scope": {"node_id": node},
                "input_versions": [dataset.id, selection["catalog_version"], versions.get("knowledge")],
                "requirement_keys": [f"req:{selection['catalog_version']}:{item['indicator_id']}" for item in curriculum],
                "record_keys": [],
                "limitations": ["输入为正式快照中的真实记录；原始作答按授权访问"],
            }
        )
        payload = _load(db, version_id)
        output_items.append(
            {
                "key": f"out:{student_id}",
                "case_id": student_id,
                "student_analysis_key": f"student:{version_id}:{student_id}",
                "plan_key": f"plan:{plan_version}:{student_id}" if plan_version else "",
                "class_analysis_keys": [],
                "finding_ids": [item.get("finding_id") for item in payload.get("findings", [])],
                "metric_keys": [f"metric:{version_id}:{item.get('metric_id')}" for item in payload.get("metrics", [])],
                "activity_ids": [],
                "evidence_refs": [],
                "limitations": payload.get("limitations", []),
            }
        )

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": selection["run_id"],
        "dataset_id": dataset.id,
        "dataset_family_id": dataset.family_id,
        "catalog_version": selection["catalog_version"],
        "rule_version": selection["rule_version"],
        "created_at": _stamp(),
        "generation_method": ["rule"],
        "data_origin": dataset.state or "official",
        "snapshot_id": export.snapshot_id,
        "selection_hash": export.selection_hash,
        "event_cutoff": selection["event_cutoff"],
        "node_ids": node_ids,
        "versions": {
            "catalog": [selection["catalog_version"]],
            "knowledge": [versions.get("knowledge")] if versions.get("knowledge") else [],
            "student": sorted({v for node_map in versions["student"].values() for v in node_map.values()}),
            "class": sorted({v for node_map in versions["class"].values() for v in node_map.values()}),
            "plans": sorted(versions["plans"].values()),
            "content": [item["content_version"] for item in micro_lessons],
        },
        "dependency_edges": [],
        "applicability_ids": [],
        "compatibility_refs": [],
        "evidence_index": {"file": "evidence_records.json", "record_count": len(evidence)},
        "coverage": {
            "students": len(versions["student"]),
            "classes": len(versions["class"]),
            "cases": len(case_ids),
            "limitations": [
                "数据集为合成测试数据，不能用于证明真实教学成效"
                if (dataset.manifest_json or {}).get("synthetic", False)
                else "数据来源与使用范围以参赛说明为准"
            ],
        },
        "files": [],
        "validation": {"preflight": "passed", "payload": "pending"},
        "limitations": [],
    }

    payloads: dict[str, bytes] = {
        "curriculum.json": _json_bytes(_items(_sorted_items(curriculum))),
        "knowledge_graph.json": _json_bytes(_items(_sorted_items(knowledge))),
        "materials_index.json": _json_bytes(_items(_sorted_items(materials))),
        "data_quality.json": _json_bytes(_items(_sorted_items(quality))),
        "student_analysis.json": _json_bytes(_items(_sorted_items(student_records))),
        "class_analysis.json": _json_bytes(_items(_sorted_items(class_records))),
        "recommendations.json": _json_bytes(_items(_sorted_items(plan_records))),
        "evidence_records.json": _json_bytes(_items(_sorted_items(evidence))),
        "learning_history.json": _json_bytes(_items(_sorted_items(learning))),
        "micro_lessons.json": _json_bytes(_items(_sorted_items(micro_lessons))),
        "examples/input.json": _json_bytes(_items(_sorted_items(input_items))),
        "examples/output.json": _json_bytes(_items(_sorted_items(output_items))),
    }
    payloads["metrics.csv"] = _metrics_csv(metric_rows)
    payloads["field_dictionary.json"] = _json_bytes(_field_dictionary(payloads))
    manifest["files"] = [
        {"path": path, "sha256": _sha(data), "bytes": len(data)}
        for path, data in sorted(payloads.items())
    ]
    payloads["manifest.json"] = _json_bytes(manifest)
    payloads["README.md"] = _readme(manifest, dataset).encode("utf-8")
    return payloads


def _metrics_csv(rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(
        [
            "metric_id",
            "subject_kind",
            "subject_id",
            "node_id",
            "indicator_id",
            "kp_id",
            "numerator",
            "denominator",
            "value",
            "unit",
            "analysis_version",
            "metric_key",
            "evidence_status",
            "current",
        ]
    )
    for row in sorted(rows):
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8-sig")


def _field_dictionary(payloads: dict[str, bytes]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for path, data in sorted(payloads.items()):
        if not path.endswith(".json"):
            continue
        try:
            parsed = json.loads(data.decode("utf-8"))
        except json.JSONDecodeError:
            continue
        first = (parsed.get("items") or [None])[0]
        if not isinstance(first, dict):
            continue
        for field, value in first.items():
            items.append(
                {
                    "key": f"{path}:{field}",
                    "file": path,
                    "field_path": field,
                    "type": type(value).__name__,
                    "required": value is not None,
                    "nullable": value is None,
                    "meaning": f"{path} 中的 {field} 字段（结构以 01 共享契约为准）",
                    "example": json.dumps(value, ensure_ascii=False)[:120],
                }
            )
    items.append(
        {
            "key": "metrics.csv:value",
            "file": "metrics.csv",
            "field_path": "value",
            "type": "decimal",
            "required": True,
            "nullable": True,
            "unit": "见本行 unit",
            "meaning": "已发布指标值；0 与空值语义不同",
        }
    )
    return _items(items)


def _readme(manifest: dict[str, Any], dataset: models.Dataset) -> str:
    return f"""# 湛知正式成果包

- 数据集：{dataset.id}（系列 {dataset.family_id}）
- 运行：{manifest['run_id']}
- 目录版本：{manifest['catalog_version']}
- 节点：{'、'.join(manifest['node_ids'])}
- 生成时间：{manifest['created_at']}
- 生成方式：规则（未调用模型）
- 数据来源：{manifest['data_origin']}

## 三项任务对应文件

1. 课程要求与知识体系：`curriculum.json`、`knowledge_graph.json`、`materials_index.json`
2. 学情分析：`student_analysis.json`、`class_analysis.json`、`metrics.csv`、`data_quality.json`
3. 个性化方案：`recommendations.json`、`micro_lessons.json`、`learning_history.json`

样例核查入口：`examples/input.json` 与 `examples/output.json` 按 `case_id` 一一对应。

## 核对方式

- 每个文件在 `manifest.json` 的 `files` 中都有 `sha256` 与 `bytes`，可直接校验；
- `evidence_records.json` 提供可解析的证据引用，指向冻结时的正式记录；
- `field_dictionary.json` 逐文件列出字段含义与空值语义。

## 适用边界

- 本包冻结自提交时的正式版本，之后的新作答与新版方案不会改变本包；
- 学情结论保留证据充分性与限制，未把"无记录"当作 0 分；
- `manifest.json` 中的 `validation.payload` 是打包时写入的自检占位，**最终回读校验结论**
  记录在系统导出记录的 `check_report` 中（两者都通过才会出现在这里）；
- 若源数据为合成测试数据，本包只能证明系统结果、证据与建议的合理性，不能证明真实教学成效。
"""


# --------------------------------------------------------------------------- #
# 打包与回读
# --------------------------------------------------------------------------- #


def build_archive(db: Session, export: models.Export, output_root: Path) -> dict[str, Any]:
    files = build_files(db, export)
    target_dir = output_root / "exports"
    target_dir.mkdir(parents=True, exist_ok=True)
    final_path = target_dir / f"{export.export_id}.zip"
    handle, temp_name = tempfile.mkstemp(dir=target_dir, prefix=".tmp-", suffix=".zip")
    os.close(handle)
    try:
        with zipfile.ZipFile(temp_name, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(files):
                info = zipfile.ZipInfo(path)
                info.date_time = (2026, 1, 1, 0, 0, 0)
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, files[path])
        report = verify_archive(Path(temp_name), files)
        if not report["passed"]:
            raise DomainError("EXPORT_ARTIFACT_INVALID", "导出包回读校验失败：" + "；".join(report["issues"][:3]))
        os.replace(temp_name, final_path)
    finally:
        Path(temp_name).unlink(missing_ok=True)
    archive_hash = _sha(final_path.read_bytes())
    manifest = json.loads(files["manifest.json"].decode("utf-8"))
    manifest["validation"] = {"preflight": "passed", "payload": "passed"}
    return {
        "storage_key": f"exports/{export.export_id}.zip",
        "archive_hash": archive_hash,
        "bytes": final_path.stat().st_size,
        "manifest": manifest,
        "check_report": report,
    }


def verify_archive(path: Path, expected: dict[str, bytes]) -> dict[str, Any]:
    issues: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                issues.append("ZIP 中存在重复成员")
            for name in names:
                if name.startswith("/") or ".." in name.split("/"):
                    issues.append(f"非法路径：{name}")
            for name, data in expected.items():
                if name not in names:
                    issues.append(f"缺少文件：{name}")
                    continue
                content = archive.read(name)
                if _sha(content) != _sha(data):
                    issues.append(f"内容哈希不符：{name}")
                if name.endswith(".json"):
                    try:
                        json.loads(content.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        issues.append(f"JSON 不可解析：{name}")
                if name.endswith(".csv"):
                    try:
                        content.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        issues.append(f"CSV 编码无效：{name}")
    except zipfile.BadZipFile:
        issues.append("ZIP 文件损坏，无法回读")
    return {"passed": not issues, "issues": issues, "file_count": len(expected)}


def download_path(export: models.Export, output_root: Path) -> Path:
    if export.status != "ready" or not export.storage_key:
        raise DomainError("RESULT_NOT_READY", "导出包尚未生成完成")
    candidate = (output_root / export.storage_key).resolve()
    root = output_root.resolve()
    if not str(candidate).startswith(str(root) + os.sep):
        raise DomainError("FORBIDDEN", "非法的导出文件定位")
    if not candidate.is_file():
        raise DomainError("EXPORT_ARTIFACT_INVALID", "导出文件缺失")
    digest = _sha(candidate.read_bytes())
    if export.archive_hash and digest != export.archive_hash:
        raise DomainError("EXPORT_ARTIFACT_INVALID", "导出文件哈希校验失败")
    return candidate


def export_view(export: models.Export) -> dict[str, Any]:
    return {
        "export_id": export.export_id,
        "snapshot_id": export.snapshot_id,
        "job_id": export.job_id,
        "run_id": export.run_id,
        "status": export.status,
        "created_at": export.created_at,
        "updated_at": export.updated_at,
        "selection_hash": export.selection_hash,
        "event_cutoff": export.event_cutoff,
        "archive_hash": export.archive_hash,
        "bytes": export.bytes,
        "check_report": export.check_report_json,
        "manifest": export.manifest_json,
        "download_url": f"/api/v1/exports/{export.export_id}/download"
        if export.status == "ready"
        else None,
    }
