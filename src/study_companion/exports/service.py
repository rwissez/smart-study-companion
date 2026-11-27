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
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.contracts import (
    AttemptView,
    DomainError,
    GradingResultView,
    RecommendationBundle,
    RubricScore,
)
from study_companion.db import models
from study_companion.db import version_repository as repo

SCHEMA_VERSION = "1.1"
EXPORT_FORMAT = "zip"
AUTO_CASE_LIMIT = 3


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
# 预检与冻结共用的检查
# --------------------------------------------------------------------------- #


def _finding(code: str, object_key: str, expected: str, remedy: str) -> dict[str, str]:
    """统一的问题定位形状（06 §8.2）。"""

    return {
        "code": code,
        "object_key": str(object_key),
        "expected": expected,
        "remedy": remedy,
    }


def _merge_findings(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    merged: list[dict[str, str]] = []
    for item in items:
        marker = (item["code"], item["object_key"])
        if marker in seen:
            continue
        seen.add(marker)
        merged.append(item)
    return merged


def _is_published(db: Session, version_id: str) -> bool:
    return (
        db.scalar(
            select(models.Publication.publication_id).where(
                models.Publication.version_id == version_id
            )
        )
        is not None
    )


_KIND_BY_ROLE = {
    "student_analysis": "student_analysis",
    "class_analysis": "class_analysis",
    "knowledge": "knowledge",
    "parent_plan": "plan",
    "plan": "plan",
    "content": "lesson",
    "lesson": "lesson",
}


def _check_version(
    db: Session,
    dataset: models.Dataset,
    version_id: str,
    *,
    expected: str,
    expecting_kind: str | None = None,
) -> tuple[models.Version | None, list[dict[str, str]]]:
    """按精确版本校验存在性、系列归属与发布状态。"""

    row = db.get(models.Version, version_id)
    if row is None:
        return None, [
            _finding(
                "DEPENDENCY_DANGLING",
                version_id,
                expected,
                "恢复该版本或重新生成依赖它的正式成果后再导出",
            )
        ]
    findings: list[dict[str, str]] = []
    if row.family_id != dataset.family_id:
        findings.append(
            _finding(
                "DEPENDENCY_CROSS_FAMILY",
                version_id,
                f"属于数据集系列 {dataset.family_id} 的正式版本",
                "移除跨系列引用，改用本系列的正式版本",
            )
        )
    if not _is_published(db, version_id):
        findings.append(
            _finding(
                "DEPENDENCY_UNPUBLISHED",
                version_id,
                "已发布的正式版本",
                "完成审核并发布该版本，或重新生成依赖它的正式成果",
            )
        )
    if expecting_kind and row.kind != expecting_kind:
        findings.append(
            _finding(
                "DEPENDENCY_KIND_MISMATCH",
                version_id,
                f"类型为 {expecting_kind} 的版本",
                "修正依赖登记后重新导出",
            )
        )
    return row, findings


def _build_closure(
    db: Session,
    dataset: models.Dataset,
    *,
    catalog_version: str,
    plan_versions: dict[str, dict[str, str]],
    student_versions: dict[str, dict[str, str]],
    class_versions: dict[str, dict[str, str]],
    knowledge_version: str | None,
    blocking: list[dict[str, str]],
    warnings: list[dict[str, str]],
) -> dict[str, Any]:
    """沿 artifact_edges 与方案载荷收集精确依赖闭包，并校验每个版本。

    只记录真实的 ``artifact_edges``；方案载荷里的 analysis/knowledge/parent
    引用作为兜底闭包继续解析，但不伪装成数据库依赖边。
    """

    student_info: dict[str, dict[str, Any]] = {}
    plan_info: dict[str, dict[str, Any]] = {}
    class_info: dict[str, dict[str, Any]] = {}
    knowledge_info: dict[str, bool] = {}
    content_info: dict[str, bool] = {}
    edges: set[tuple[str, str, str]] = set()
    seen: set[str] = set()
    queue: deque[models.Version] = deque()

    def mark(
        row: models.Version,
        *,
        student_id: str = "",
        node_id: str = "",
        current: bool = False,
    ) -> None:
        if row.version_id not in seen:
            seen.add(row.version_id)
            queue.append(row)
        if row.kind == "student_analysis":
            bucket = student_info.setdefault(
                row.version_id,
                {
                    "student_id": row.target_key or student_id,
                    "node_id": row.node_key or node_id,
                    "current": False,
                },
            )
            bucket["current"] = bool(bucket["current"] or current)
        elif row.kind == "plan":
            bucket = plan_info.setdefault(
                row.version_id,
                {
                    "student_id": row.target_key or student_id,
                    "node_id": row.node_key or node_id,
                    "current": False,
                },
            )
            bucket["current"] = bool(bucket["current"] or current)
        elif row.kind == "class_analysis":
            bucket = class_info.setdefault(
                row.version_id,
                {
                    "class_id": row.target_key,
                    "node_id": row.node_key,
                    "current": False,
                },
            )
            bucket["current"] = bool(bucket["current"] or current)
        elif row.kind == "knowledge":
            knowledge_info[row.version_id] = True
        elif row.kind == "lesson":
            content_info[row.version_id] = True

    def follow(
        version_id: str,
        *,
        role: str,
        parent_id: str = "",
        expected: str,
        student_id: str = "",
        node_id: str = "",
        current: bool = False,
    ) -> None:
        row, findings = _check_version(
            db,
            dataset,
            version_id,
            expected=expected,
            expecting_kind=_KIND_BY_ROLE.get(role),
        )
        blocking.extend(findings)
        if row is None:
            return
        if parent_id:
            edges.add((parent_id, row.version_id, role))
        mark(row, student_id=student_id, node_id=node_id, current=current)

    # 本包直接选中的当前版本：即使没有任何依赖边也要进入闭包。
    for student_id, node_map in student_versions.items():
        for node, version_id in node_map.items():
            row = db.get(models.Version, version_id)
            if row is not None:
                mark(row, student_id=student_id, node_id=node, current=True)
    for _class_id, node_map in class_versions.items():
        for node, version_id in node_map.items():
            row = db.get(models.Version, version_id)
            if row is not None:
                mark(row, node_id=node, current=True)
    for student_id, node_map in plan_versions.items():
        for node, version_id in node_map.items():
            row = db.get(models.Version, version_id)
            if row is not None:
                mark(row, student_id=student_id, node_id=node, current=True)
    if knowledge_version:
        row = db.get(models.Version, knowledge_version)
        if row is not None:
            mark(row)

    while queue:
        row = queue.popleft()
        if row.kind == "plan":
            payload = dict(row.payload_json or {})
            meta = plan_info.get(row.version_id, {})
            student_id = meta.get("student_id") or row.target_key
            node_id = meta.get("node_id") or row.node_key
            for role, reference, expected in (
                ("student_analysis", payload.get("analysis_version"), "已发布的正式学生学情"),
                ("knowledge", payload.get("knowledge_version"), "已发布的正式知识体系"),
                ("parent_plan", payload.get("parent_plan_version"), "已发布的父方案版本"),
            ):
                if reference:
                    follow(
                        reference,
                        role=role,
                        expected=expected,
                        student_id=student_id,
                        node_id=node_id,
                    )
            for recommendation in payload.get("recommendations", []) or []:
                for activity in recommendation.get("activities", []) or []:
                    content_version = activity.get("content_version")
                    content_ref = activity.get("content_ref") or {}
                    if not content_version and isinstance(content_ref, dict):
                        content_version = content_ref.get("content_version")
                    if content_version:
                        follow(
                            content_version,
                            role="content",
                            expected="已发布的微课堂内容版本",
                            student_id=student_id,
                            node_id=node_id,
                        )
        for edge in repo.dependencies(db, row):
            follow(
                edge.dep_version_id,
                role=edge.role,
                parent_id=row.version_id,
                expected="该依赖边指向已发布的正式版本",
            )

    catalog = db.get(models.Catalog, catalog_version)
    if catalog is None:
        blocking.append(
            _finding(
                "CATALOG_MISSING",
                catalog_version,
                "已发布的正式课程目录",
                "重新构建并发布课程目录后再导出",
            )
        )
    else:
        catalog_dataset = db.get(models.Dataset, catalog.dataset_id)
        if catalog_dataset is None or catalog_dataset.family_id != dataset.family_id:
            blocking.append(
                _finding(
                    "CATALOG_CROSS_FAMILY",
                    catalog_version,
                    f"属于数据集系列 {dataset.family_id} 的课程目录",
                    "选择本系列的目录版本后再导出",
                )
            )

    # 适用性只取本包当前方案版本相关记录，不把其他学生的记录带进来。
    current_plan_ids = sorted({version_id for node_map in plan_versions.values() for version_id in node_map.values()})
    applicability_ids: list[str] = []
    if current_plan_ids:
        for row in db.scalars(
            select(models.PlanApplicability)
            .where(models.PlanApplicability.plan_version.in_(current_plan_ids))
            .order_by(models.PlanApplicability.applicability_id)
        ).all():
            applicability_ids.append(row.applicability_id)
            if row.result == "incompatible":
                blocking.append(
                    _finding(
                        "APPLICABILITY_INCOMPATIBLE",
                        row.applicability_id,
                        "方案对当前学情仍可用或已明确限制",
                        "重新生成并发布适用当前学情的方案后再导出",
                    )
                )
            elif row.result == "limited":
                warnings.append(
                    _finding(
                        "APPLICABILITY_LIMITED",
                        row.applicability_id,
                        "方案仅局部适用，导出会保留限制说明",
                        "在教师端复核该方案的适用范围",
                    )
                )

    compatibility_refs: list[str] = []
    if seen:
        from sqlalchemy import or_

        for row in db.scalars(
            select(models.VersionCompatibility)
            .where(
                or_(
                    models.VersionCompatibility.from_version.in_(sorted(seen)),
                    models.VersionCompatibility.to_version.in_(sorted(seen)),
                )
            )
            .order_by(models.VersionCompatibility.kind)
        ).all():
            compatibility_refs.append(
                f"{row.kind}:{row.from_version}:{row.to_version}:{row.scope_hash}:{row.policy_hash}"
            )

    return {
        "student": student_info,
        "class": class_info,
        "plan": plan_info,
        "knowledge": sorted(knowledge_info),
        "content": sorted(content_info),
        "dependency_edges": [
            {"from": source, "to": target, "role": role}
            for source, target, role in sorted(edges)
        ],
        "applicability_ids": sorted(set(applicability_ids)),
        "compatibility_refs": sorted(set(compatibility_refs)),
    }


def assess_export_scope(
    db: Session,
    *,
    run: models.AnalysisRun,
    dataset: models.Dataset,
    node_ids: list[str] | None,
    case_student_ids: list[str] | None,
) -> dict[str, Any]:
    """只读地评估一次导出范围；预检与冻结共用同一套判定。"""

    blocking: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    family_id = dataset.family_id or ""
    if not family_id:
        blocking.append(
            _finding(
                "FAMILY_MISSING",
                dataset.id,
                "数据集具备系列身份",
                "为数据集登记系列后再导出",
            )
        )

    available_nodes = list(run.node_ids_json or [])
    selected_nodes = list(dict.fromkeys(node_ids)) if node_ids else list(available_nodes)
    if not selected_nodes:
        blocking.append(
            _finding(
                "NODE_SCOPE_EMPTY",
                run.id,
                "至少一个已发布测验节点",
                "先完成该运行的学情分析",
            )
        )
    for node in selected_nodes:
        if node not in available_nodes:
            blocking.append(
                _finding(
                    "NODE_OUT_OF_RUN",
                    node,
                    "节点属于该运行",
                    "选择该运行范围内的节点后重试",
                )
            )

    roster = db.scalars(
        select(models.Student).where(models.Student.dataset_id == dataset.id)
    ).all()
    students = [row.student_id for row in roster]
    classes = sorted({row.class_id for row in roster})
    if not students:
        blocking.append(
            _finding(
                "ROSTER_EMPTY",
                dataset.id,
                "数据集包含学生名册",
                "先导入学生名册并完成分析",
            )
        )

    student_versions: dict[str, dict[str, str]] = {}
    for node in selected_nodes:
        for student_id in students:
            version = repo.read_formal(
                db,
                repo.FormalKey(
                    family_id=family_id,
                    dataset_id=dataset.id,
                    kind="student_analysis",
                    run_key=run.id,
                    node_key=node,
                    target_key=student_id,
                ),
            )
            if version is None:
                blocking.append(
                    _finding(
                        "STUDENT_ANALYSIS_MISSING",
                        f"{student_id}/{node}",
                        "已发布的正式学生学情",
                        "等待学情分析完成并发布，或调整导出节点",
                    )
                )
                continue
            student_versions.setdefault(student_id, {})[node] = version.version_id

    class_versions: dict[str, dict[str, str]] = {}
    for node in selected_nodes:
        for class_id in classes:
            version = repo.read_formal(
                db,
                repo.FormalKey(
                    family_id=family_id,
                    dataset_id=dataset.id,
                    kind="class_analysis",
                    run_key=run.id,
                    node_key=node,
                    target_key=class_id,
                ),
            )
            if version is None:
                blocking.append(
                    _finding(
                        "CLASS_ANALYSIS_MISSING",
                        f"{class_id}/{node}",
                        "已发布的正式班级分析",
                        "等待班级聚合完成并发布，或调整导出节点",
                    )
                )
                continue
            class_versions.setdefault(class_id, {})[node] = version.version_id

    plans: dict[str, dict[str, models.Version]] = {}
    usables: set[str] = set()
    for student_id in students:
        for node in selected_nodes:
            version = repo.read_formal(
                db,
                repo.FormalKey(
                    family_id=family_id,
                    dataset_id=dataset.id,
                    kind="plan",
                    node_key=node,
                    target_key=student_id,
                ),
            )
            if version is None:
                continue
            plans.setdefault(student_id, {})[node] = version
            try:
                bundle = RecommendationBundle.model_validate(version.payload_json or {})
            except Exception:
                blocking.append(
                    _finding(
                        "PLAN_INVALID",
                        version.version_id,
                        "可解析的正式学习方案",
                        "重新审核并发布该学生的方案",
                    )
                )
                continue
            if any(
                recommendation.activities for recommendation in bundle.recommendations
            ):
                usables.add(student_id)
            else:
                # 已审核的"无适配材料/补证或介入"结果：允许导出，但案例不能选它。
                warnings.append(
                    _finding(
                        "PLAN_NO_ACTIVITY",
                        version.version_id,
                        "该学生方案为正式审核过的无适配材料/补证结果",
                        "导出会保留空活动与限制说明；如需可执行建议，请先补充可用材料",
                    )
                )
    for student_id in students:
        if student_id not in plans:
            blocking.append(
                _finding(
                    "PLAN_MISSING",
                    student_id,
                    "已发布的正式学习方案",
                    "为该学生生成并发布方案，或缩小导出范围",
                )
            )

    knowledge_version = None
    knowledge_row = repo.read_formal(
        db,
        repo.FormalKey(family_id=family_id, dataset_id=dataset.id, kind="knowledge"),
    )
    if knowledge_row is not None:
        knowledge_version = knowledge_row.version_id
    else:
        warnings.append(
            _finding(
                "KNOWLEDGE_MISSING",
                dataset.id,
                "已发布的正式知识体系",
                "发布知识体系后可提供完整知识图谱",
            )
        )

    auto_selected = not case_student_ids
    if case_student_ids:
        case_ids = list(dict.fromkeys(case_student_ids))
        if not case_ids:
            blocking.append(
                _finding(
                    "CASE_STUDENT_INVALID",
                    "case_student_ids",
                    "至少一名典型案例学生",
                    "去掉空值或让学生端显式指定学生编号",
                )
            )
        first_node = selected_nodes[0] if selected_nodes else ""
        for student_id in case_ids:
            if student_id not in students:
                blocking.append(
                    _finding(
                        "CASE_STUDENT_NOT_IN_DATASET",
                        student_id,
                        "属于该数据集的学生",
                        "从该数据集学生名册中选择典型案例",
                    )
                )
            elif student_id not in student_versions:
                blocking.append(
                    _finding(
                        "CASE_STUDENT_NO_ANALYSIS",
                        student_id,
                        "该学生在本运行有正式学情结果",
                        "等待该学生分析发布，或更换案例学生",
                    )
                )
            elif student_id not in usables:
                blocking.append(
                    _finding(
                        "CASE_STUDENT_NO_PLAN",
                        student_id,
                        "至少一份可用学习方案",
                        "为该学生生成并发布可用方案后再选为案例",
                    )
                )
            else:
                case_node = next(iter(plans[student_id].values())).node_key or first_node
                if case_node and case_node not in student_versions.get(student_id, {}):
                    blocking.append(
                        _finding(
                            "CASE_STUDENT_NO_NODE_ANALYSIS",
                            f"{student_id}/{case_node}",
                            "该学生在该方案节点有正式学情结果",
                            "更换导出节点或案例学生",
                        )
                    )
    else:
        candidates = [
            student_id
            for student_id in sorted(student_versions)
            if student_id in usables
        ]
        case_ids = candidates[:AUTO_CASE_LIMIT]
        if not case_ids:
            blocking.append(
                _finding(
                    "CASE_NONE_AVAILABLE",
                    dataset.id,
                    "至少一名有正式结果与可用方案的案例学生",
                    "先完成学情与方案发布，或显式指定已授权案例学生",
                )
            )
        else:
            warnings.append(
                _finding(
                    "CASE_AUTO_SELECTED",
                    ",".join(case_ids),
                    "默认按正式学情结果自动选取前三名有方案的学生",
                    "如需指定典型案例，请在请求中提交 case_student_ids",
                )
            )

    closure = _build_closure(
        db,
        dataset,
        catalog_version=run.catalog_version,
        plan_versions={
            student_id: {node: version.version_id for node, version in node_map.items()}
            for student_id, node_map in plans.items()
        },
        student_versions=student_versions,
        class_versions=class_versions,
        knowledge_version=knowledge_version,
        blocking=blocking,
        warnings=warnings,
    )

    return {
        "run_id": run.id,
        "node_ids": selected_nodes,
        "students": students,
        "classes": classes,
        "student_versions": student_versions,
        "class_versions": class_versions,
        "plan_versions": {
            student_id: {node: version.version_id for node, version in node_map.items()}
            for student_id, node_map in plans.items()
        },
        "knowledge_version": knowledge_version,
        "case_ids": case_ids,
        "case_selection": "auto" if auto_selected else "explicit",
        "blocking": _merge_findings(blocking),
        "warnings": _merge_findings(warnings),
        "closure": closure,
        "coverage": {
            "students": len(student_versions),
            "classes": len(class_versions),
            "cases": len(case_ids),
        },
    }


def _case_selection_note(mode: str) -> str:
    if mode == "auto":
        return "典型案例学生由系统按正式学情结果自动选取，正式提交前需教师确认"
    return "典型案例学生由教师显式指定"


def preflight_export(
    db: Session,
    *,
    run: models.AnalysisRun,
    dataset: models.Dataset,
    node_ids: list[str] | None,
    case_student_ids: list[str] | None,
) -> dict[str, Any]:
    """只读预检：不写导出记录、不入队任务，返回与冻结同源的检查结果。"""

    assessment = assess_export_scope(
        db,
        run=run,
        dataset=dataset,
        node_ids=node_ids,
        case_student_ids=case_student_ids,
    )
    return {
        "ready": not assessment["blocking"],
        "blocking": assessment["blocking"],
        "warnings": assessment["warnings"],
        "coverage": assessment["coverage"],
    }


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

    assessment = assess_export_scope(
        db,
        run=run,
        dataset=dataset,
        node_ids=node_ids,
        case_student_ids=case_student_ids,
    )
    if assessment["blocking"]:
        raise DomainError(
            "EXPORT_INCOMPLETE",
            "正式成果不完整，不能生成正式包",
            assessment["blocking"][:20],
        )

    selection = {
        "run_id": run_id,
        "dataset_id": dataset.id,
        "dataset_family_id": dataset.family_id,
        "catalog_version": run.catalog_version,
        "rule_version": run.rule_version,
        "node_ids": assessment["node_ids"],
        "case_student_ids": assessment["case_ids"],
        "case_selection": assessment["case_selection"],
        "versions": {
            "student": assessment["student_versions"],
            "class": assessment["class_versions"],
            "plans": assessment["plan_versions"],
            "knowledge": assessment["knowledge_version"],
        },
        "closure": assessment["closure"],
        "warnings": assessment["warnings"],
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
        check_report_json={
            "preflight": "passed",
            "payload": "pending",
            "case_selection": {
                "mode": assessment["case_selection"],
                "note": _case_selection_note(assessment["case_selection"]),
            },
            "warnings": assessment["warnings"],
        },
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


def _within_cutoff(value: str | None, cutoff: str | None) -> bool:
    """冻结后新产生的在线记录不进入旧包。"""

    if not value or not cutoff:
        return True
    try:
        return datetime.fromisoformat(value) <= datetime.fromisoformat(cutoff)
    except (TypeError, ValueError):
        return str(value) <= str(cutoff)


def build_files(db: Session, export: models.Export) -> dict[str, bytes]:
    selection = dict(export.selection_json or {})
    run = db.get(models.AnalysisRun, selection["run_id"])
    dataset = db.get(models.Dataset, selection["dataset_id"])
    catalog_row = db.get(models.Catalog, selection["catalog_version"])
    catalog = None
    if catalog_row is not None:
        from study_companion.library.datasets import load_catalog

        catalog = load_catalog(db, selection["catalog_version"], dataset.id if dataset else None)
    if run is None or dataset is None or catalog_row is None:
        raise DomainError("EXPORT_INCOMPLETE", "冻结范围引用的对象已不可读")
    node_ids = selection["node_ids"]
    versions = selection["versions"]
    closure = dict(selection.get("closure") or {})
    knowledge_version = versions.get("knowledge")

    student_entries: dict[str, dict[str, Any]] = dict(closure.get("student") or {})
    plan_entries: dict[str, dict[str, Any]] = dict(closure.get("plan") or {})
    content_ids: list[str] = list(closure.get("content") or [])
    if not student_entries:
        for student_id, node_map in versions["student"].items():
            for node, version_id in node_map.items():
                student_entries[version_id] = {
                    "student_id": student_id,
                    "node_id": node,
                    "current": True,
                }
    if not plan_entries:
        for student_id, node_map in versions["plans"].items():
            for node, version_id in node_map.items():
                plan_entries[version_id] = {
                    "student_id": student_id,
                    "node_id": node,
                    "current": True,
                }
    if not content_ids:
        content_ids = sorted(
            db.scalars(
                select(models.Version.version_id).where(
                    models.Version.dataset_id == dataset.id,
                    models.Version.kind == "lesson",
                )
            ).all()
        )

    curriculum: list[dict[str, Any]] = []
    catalog_indicators = [item.model_dump(mode="json") for item in catalog.indicators] if catalog else []
    catalog_materials = [item.model_dump(mode="json") for item in catalog.materials] if catalog else []
    for indicator in catalog_indicators:
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
    for material in catalog_materials:
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
    for version_id, meta in sorted(student_entries.items()):
        student_id = str(meta.get("student_id") or "")
        node = str(meta.get("node_id") or "")
        current = bool(meta.get("current"))
        payload = _load(db, version_id)
        student_records.append(
            {
                "key": f"student:{version_id}:{student_id}",
                "student_id": student_id,
                "node_id": node,
                "analysis_version": version_id,
                "current": current,
                "knowledge_version": knowledge_version,
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
                    "true" if current else "false",
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
    content_activity: dict[str, dict[str, str]] = {}
    for version_id, meta in sorted(plan_entries.items()):
        student_id = str(meta.get("student_id") or "")
        payload = _load(db, version_id)
        plan = RecommendationBundle.model_validate(payload)
        node_id = plan.node_id or str(meta.get("node_id") or "")
        current = bool(meta.get("current"))
        origin_version = plan.analysis_version or ""
        current_version = (
            versions["student"].get(student_id, {}).get(node_id)
            or versions["student"].get(student_id, {}).get(node_ids[0], "")
        )
        activities: list[dict[str, Any]] = []
        for recommendation in plan.recommendations:
            for activity in recommendation.activities:
                projection = activity.model_dump(mode="json")
                if activity.content_version:
                    projection["content_key"] = f"content:{activity.content_version}"
                    content_activity.setdefault(
                        activity.content_version,
                        {"activity_id": activity.activity_id, "plan_version": version_id},
                    )
                activities.append(projection)
        applicability = None
        if current:
            applicability_row = db.scalar(
                select(models.PlanApplicability)
                .where(models.PlanApplicability.plan_version == version_id)
                .order_by(models.PlanApplicability.checked_at.desc())
                .limit(1)
            )
            if applicability_row is not None:
                applicability = {
                    "applicability_id": applicability_row.applicability_id,
                    "result": applicability_row.result,
                    "checked_at": applicability_row.checked_at,
                    "event_cutoff": applicability_row.event_cutoff,
                    "activity_ids": list(applicability_row.activity_ids_json or []),
                    "evidence_refs": list(applicability_row.evidence_refs_json or []),
                    "limitations": list(applicability_row.limitations_json or []),
                }
        plan_records.append(
            {
                "key": f"plan:{version_id}:{student_id}",
                "student_id": student_id,
                "node_id": node_id,
                "plan_version": version_id,
                "current": current,
                "origin_analysis_key": f"student:{origin_version}:{student_id}"
                if origin_version
                else "",
                "current_analysis_key": f"student:{current_version}:{student_id}"
                if current_version
                else "",
                "knowledge_version": plan.knowledge_version,
                "catalog_version": plan.catalog_version,
                "review_status": (plan.review_info.status if plan.review_info else "validated"),
                "publication_basis": plan.publication_basis or "automatic_review",
                "applicability": applicability,
                "groups": [group.model_dump(mode="json") for group in plan.task_groups],
                "activities": activities,
                "limitations": plan.limitations,
            }
        )

    evidence: list[dict[str, Any]] = []
    record_keys: dict[tuple[str, str], list[str]] = {}
    roster_ids = list(
        db.scalars(
            select(models.Student.student_id).where(models.Student.dataset_id == dataset.id)
        ).all()
    )
    events = db.scalars(
        select(models.AnswerEvent)
        .where(
            models.AnswerEvent.dataset_id == dataset.id,
            models.AnswerEvent.student_id.in_(roster_ids),
            models.AnswerEvent.node_id.in_(node_ids),
        )
        .order_by(models.AnswerEvent.event_id)
        .limit(500)
    ).all()
    for event in events:
        payload = dict(event.payload_json or {})
        record_id = payload.get("selected_record_id") or event.event_id
        key = f"ev:{selection['run_id']}:{record_id}"
        record_keys.setdefault((event.student_id, event.node_id), []).append(key)
        evidence.append(
            {
                "key": key,
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

    cutoff = selection.get("created_at")
    learning: list[dict[str, Any]] = []
    for row in db.scalars(
        select(models.LearningEventRow)
        .where(models.LearningEventRow.dataset_id == dataset.id)
        .order_by(models.LearningEventRow.offset)
        .limit(500)
    ).all():
        if not _within_cutoff(row.occurred_at, cutoff):
            continue
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

    student_uid_to_id = {
        row.student_uid: row.student_id
        for row in db.scalars(
            select(models.Student).where(models.Student.dataset_id == dataset.id)
        ).all()
        if row.student_uid
    }
    attempt_rows = []
    if student_uid_to_id:
        attempt_rows = [
            row
            for row in db.scalars(
                select(models.AttemptRow)
                .join(
                    models.Version,
                    models.Version.version_id == models.AttemptRow.plan_version,
                )
                .where(
                    models.AttemptRow.student_uid.in_(sorted(student_uid_to_id)),
                    models.Version.dataset_id == dataset.id,
                )
                .order_by(models.AttemptRow.created_at)
            ).all()
            if _within_cutoff(row.created_at, cutoff)
        ]
    attempt_by_id = {row.attempt_id: row for row in attempt_rows}
    for row in attempt_rows:
        answer = dict(row.answer_json or {})
        view = AttemptView(
            attempt_id=row.attempt_id,
            student_id=student_uid_to_id.get(row.student_uid, ""),
            plan_version=row.plan_version,
            activity_id=row.activity_id,
            material_id=row.material_id,
            status=row.status,
            submitted_at=row.submitted_at,
            final_answer=answer.get("final_answer"),
            solution_steps=answer.get("solution_steps"),
            assistance_status=row.assistance_status,
            hint_max_level=answer.get("hint_max_level"),
            agent_help_count=answer.get("agent_help_count"),
            submission_no=row.submission_no,
            latest_grade_id=row.latest_grade_id,
        )
        learning.append(
            {
                "key": f"attempt:{row.attempt_id}",
                "kind": "attempt",
                "plan_version": row.plan_version,
                "activity_id": row.activity_id,
                "data": view.model_dump(mode="json"),
            }
        )

    grade_rows = []
    if attempt_by_id:
        grade_rows = [
            row
            for row in db.scalars(
                select(models.Grade)
                .where(models.Grade.attempt_id.in_(sorted(attempt_by_id)))
                .order_by(models.Grade.created_at, models.Grade.revision)
            ).all()
            if _within_cutoff(row.created_at, cutoff)
        ]
    for row in grade_rows:
        attempt = attempt_by_id[row.attempt_id]
        grade_payload = dict(row.payload_json or {})
        grade_status = (
            row.status
            if row.status in {"pending", "graded", "needs_review", "unanswered"}
            else "needs_review"
        )
        validation_status = (
            row.validation_status
            if row.validation_status in {"not_checked", "validated", "rejected"}
            else "not_checked"
        )
        rubric_scores: list[RubricScore] = []
        if grade_status == "graded":
            for item in grade_payload.get("rubric_scores", []) or []:
                try:
                    rubric_scores.append(RubricScore.model_validate(item))
                except Exception:
                    continue
        method = grade_payload.get("method")
        if method not in {"rule", "model", "hybrid", "teacher"}:
            method = "rule"
        view = GradingResultView(
            grade_id=row.grade_id,
            attempt_id=row.attempt_id,
            revision=row.revision,
            status=grade_status,
            validation_status=validation_status,
            score=row.score if grade_status == "graded" else None,
            max_score=row.max_score,
            rubric_scores=rubric_scores,
            method=method,
            evidence_refs=[],
            limitations=[str(item) for item in grade_payload.get("limitations", []) or []],
            created_at=row.created_at,
            rule_version=str(grade_payload.get("rule_version", "") or ""),
            prompt_version=grade_payload.get("prompt_version"),
        )
        learning.append(
            {
                "key": f"grade:{row.grade_id}",
                "kind": "grade",
                "plan_version": attempt.plan_version,
                "activity_id": attempt.activity_id,
                "data": view.model_dump(mode="json"),
            }
        )
    has_online_history = bool(attempt_rows or grade_rows)

    micro_lessons: list[dict[str, Any]] = []
    for version_id in sorted(content_ids):
        version = db.get(models.Version, version_id)
        if version is None:
            continue
        payload = dict(version.payload_json or {})
        link = content_activity.get(version_id, {})
        micro_lessons.append(
            {
                "key": f"content:{version_id}",
                "content_id": payload.get("content_id"),
                "content_version": version_id,
                "activity_id": link.get("activity_id", ""),
                "plan_version": payload.get("origin_plan_version")
                or link.get("plan_version", ""),
                "body": payload.get("body"),
                "review_status": (payload.get("review_info") or {}).get("status"),
                "evidence_refs": [],
            }
        )

    case_ids = selection["case_student_ids"]
    case_mode = selection.get("case_selection", "explicit")
    student_class = {
        row.student_id: row.class_id
        for row in db.scalars(
            select(models.Student).where(models.Student.dataset_id == dataset.id)
        ).all()
    }
    input_items: list[dict[str, Any]] = []
    output_items: list[dict[str, Any]] = []
    for student_id in case_ids:
        plan_node_map = versions["plans"].get(student_id, {})
        plan_version = next((plan_node_map.get(node) for node in node_ids if plan_node_map.get(node)), "")
        if not plan_version and plan_node_map:
            plan_version = next(iter(plan_node_map.values()))
        plan_payload = _load(db, plan_version)
        node = plan_payload.get("node_id") or node_ids[0]
        version_id = versions["student"].get(student_id, {}).get(node, "")
        case_limitations = ["输入为正式快照中的真实记录；原始作答按授权访问"]
        if case_mode == "auto":
            case_limitations.append(
                "该案例学生由系统按正式学情结果自动选取，需教师确认后作为正式提交名单"
            )
        input_items.append(
            {
                "key": f"in:{student_id}",
                "case_id": student_id,
                "student_id": student_id,
                "node_id": node,
                "scope": {"node_id": node},
                "input_versions": [
                    item
                    for item in (
                        dataset.id,
                        selection["catalog_version"],
                        knowledge_version,
                    )
                    if item
                ],
                "requirement_keys": [
                    f"req:{selection['catalog_version']}:{item['indicator_id']}"
                    for item in curriculum
                ],
                "record_keys": list(record_keys.get((student_id, node), [])),
                "limitations": case_limitations,
            }
        )
        payload = _load(db, version_id)
        class_id = student_class.get(student_id, "")
        class_version = versions["class"].get(class_id, {}).get(node, "")
        activity_ids = [
            activity.get("activity_id")
            for recommendation in plan_payload.get("recommendations", []) or []
            for activity in recommendation.get("activities", []) or []
            if activity.get("activity_id")
        ]
        output_items.append(
            {
                "key": f"out:{student_id}",
                "case_id": student_id,
                "student_analysis_key": f"student:{version_id}:{student_id}",
                "plan_key": f"plan:{plan_version}:{student_id}" if plan_version else "",
                "class_analysis_keys": (
                    [f"class:{class_version}:{class_id}"] if class_version else []
                ),
                "finding_ids": [
                    item.get("finding_id") for item in payload.get("findings", [])
                ],
                "metric_keys": [
                    f"metric:{version_id}:{item.get('metric_id')}"
                    for item in payload.get("metrics", [])
                ],
                "activity_ids": activity_ids,
                "evidence_refs": [],
                "limitations": payload.get("limitations", []),
            }
        )

    manifest_limitations: list[str] = []
    if case_mode == "auto":
        manifest_limitations.append(
            "典型案例学生未由教师显式指定：系统按正式学情结果自动选取有可用方案的学生，正式提交前需教师确认"
        )
    if not has_online_history:
        manifest_limitations.append(
            "本包学生没有在线尝试与评分记录：learning_history.json 的 attempt/grade 条目为空，只保留学习事件"
        )
    if any(not bool(meta.get("current")) for meta in student_entries.values()) or any(
        not bool(meta.get("current")) for meta in plan_entries.values()
    ):
        manifest_limitations.append(
            "包内含历史依赖版本：student_analysis.json 与 recommendations.json 以 current=false 标记"
        )
    warning_codes = {
        str(item.get("code")) for item in (selection.get("warnings") or []) if isinstance(item, dict)
    }
    if "PLAN_NO_ACTIVITY" in warning_codes:
        manifest_limitations.append(
            "部分学生的正式方案为已审核的\u201c无适配材料/补证或介入\u201d结果，recommendations.json 保留空活动与限制说明"
        )
    closure_knowledge = sorted(closure.get("knowledge") or [])
    if knowledge_version and knowledge_version not in closure_knowledge:
        closure_knowledge.append(knowledge_version)

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
            "knowledge": sorted(closure_knowledge),
            "student": sorted(student_entries),
            "class": sorted(
                {v for node_map in versions["class"].values() for v in node_map.values()}
            ),
            "plans": sorted(plan_entries),
            "content": sorted(content_ids),
        },
        "dependency_edges": list(closure.get("dependency_edges") or []),
        "applicability_ids": sorted(closure.get("applicability_ids") or []),
        "compatibility_refs": sorted(closure.get("compatibility_refs") or []),
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
        "limitations": manifest_limitations,
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
    selection = dict(export.selection_json or {})
    case_mode = selection.get("case_selection", "explicit")
    report["case_selection"] = {
        "mode": case_mode,
        "note": _case_selection_note(case_mode),
    }
    if selection.get("warnings"):
        report["warnings"] = list(selection["warnings"])
    report["dependency_edges"] = len(manifest.get("dependency_edges") or [])
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
