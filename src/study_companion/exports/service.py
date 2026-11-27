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
                version = _latest_published(
                    db, dataset.id, "student_analysis", node, student_id
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
                version = _latest_published(
                    db, dataset.id, "class_analysis", node, class_id
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


def _publication_record(db: Session, version_id: str) -> dict[str, Any]:
    """读取该版本的正式发布记录；没有记录就是未发布，不能凭载荷自述。"""

    row = db.scalar(
        select(models.Publication).where(models.Publication.version_id == version_id)
    )
    if row is None:
        return {
            "status": "unpublished",
            "publication_id": None,
            "published_at": None,
            "path": None,
            "basis": None,
            "review_id": None,
        }
    return {
        "status": "published",
        "publication_id": row.publication_id,
        "published_at": row.published_at,
        "path": row.path,
        "basis": row.basis,
        "review_id": row.review_id or None,
    }


def _safe_evidence_ref(item: Any) -> dict[str, Any] | None:
    """证据引用的安全投影：只保留定位字段，不携带正文或答案。"""

    if not isinstance(item, dict):
        return None
    ref_id = str(item.get("id") or "").strip()
    kind = str(item.get("kind") or "").strip()
    if not ref_id or not kind:
        return None
    allowed = (
        "run_id",
        "catalog_version",
        "dataset_id",
        "knowledge_version",
        "analysis_version",
        "plan_version",
        "material_id",
        "attempt_id",
        "curriculum_type",
    )
    projected = {"kind": kind, "id": ref_id}
    for field in allowed:
        value = item.get(field)
        if value:
            projected[field] = value
    return projected


def _module_method(methods: set[str]) -> str:
    """把模块内出现的方法合并成可读标签；模型参与优先，教师确认单独保留。"""

    known = {
        item for item in methods if item in {"rule", "model", "hybrid", "human"}
    }
    if not known:
        return "unknown"
    if "model" in known or "hybrid" in known:
        return "hybrid"
    if known == {"human"}:
        return "human"
    if "human" in known:
        return "rule+human"
    return "rule"


def _source_locator_ref(
    locator: dict[str, Any],
    *,
    material_id: str,
    catalog_version: str,
    source_label: str | None = None,
) -> dict[str, Any]:
    """材料出处投影；只搬运目录里已登记的定位字段，不补页码。"""

    ref: dict[str, Any] = {
        "kind": "material_source",
        "id": material_id,
        "material_id": material_id,
        "catalog_version": catalog_version,
        "source_file": str(locator.get("source_file") or ""),
        "verification_status": str(locator.get("verification_status") or "unverified"),
    }
    for field in (
        "source_kind",
        "file_id",
        "chapter",
        "section",
        "pdf_page",
        "page_end",
        "printed_page",
    ):
        value = locator.get(field)
        if value not in (None, ""):
            ref[field] = value
    if source_label:
        ref["source_label"] = source_label
    return ref


def _latest_published(
    db: Session, dataset_id: str, kind: str, node_key: str, target_key: str
) -> models.Version | None:
    """该对象该节点最近一次已发布版本。

    局部更新会产生只含个别学生的运行，导出仍应使用每个对象最近一次正式版本，
    而不是被"最新运行"卡住。
    """

    return db.scalar(
        select(models.Version)
        .join(models.Publication, models.Publication.version_id == models.Version.version_id)
        .where(
            models.Version.dataset_id == dataset_id,
            models.Version.kind == kind,
            models.Version.node_key == node_key,
            models.Version.target_key == target_key,
        )
        .order_by(models.Version.created_at.desc())
        .limit(1)
    )

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
        requirement_refs: list[dict[str, Any]] = [
            {
                "kind": "curriculum",
                "id": indicator.get("indicator_id"),
                "catalog_version": selection["catalog_version"],
                "curriculum_ref": indicator.get("curriculum_ref") or "",
                "curriculum_page": indicator.get("curriculum_page") or "",
                "textbook_pages": indicator.get("textbook_pages") or "",
                "source": "requirements_table",
                "verification_status": "unverified",
            }
        ]
        for index, locator in enumerate(indicator.get("textbook_refs") or [], start=1):
            if not isinstance(locator, dict):
                continue
            requirement_refs.append(
                {
                    "kind": "textbook",
                    "id": f"{indicator.get('indicator_id')}:textbook:{index:02d}",
                    "catalog_version": selection["catalog_version"],
                    "locator": dict(locator),
                    "verification_status": str(
                        locator.get("verification_status") or "unverified"
                    ),
                }
            )
        curriculum.append(
            {
                "key": f"req:{selection['catalog_version']}:{indicator['indicator_id']}",
                "catalog_version": selection["catalog_version"],
                "topic_id": indicator.get("topic_id"),
                "indicator_id": indicator.get("indicator_id"),
                "title": indicator.get("description", ""),
                "requirement": indicator.get("description", ""),
                "node_ids": node_ids,
                "source_refs": requirement_refs,
            }
        )

    knowledge: list[dict[str, Any]] = []
    knowledge_payload = _load(db, versions.get("knowledge"))
    knowledge_point_ids = {
        str(point.get("kp_id")) for point in knowledge_payload.get("points", [])
    }
    kp_annotations: dict[str, list[dict[str, Any]]] = {}
    for annotation in knowledge_payload.get("annotations", []):
        if annotation.get("source_type") != "material":
            continue
        source_id = str(annotation.get("source_id") or "")
        if not source_id:
            continue
        for target in annotation.get("target_ids") or []:
            kp_annotations.setdefault(source_id, []).append(
                {
                    "kp_id": str(target),
                    "role": annotation.get("knowledge_role") or "primary",
                    "basis": annotation.get("basis") or "standard",
                    "annotation_id": annotation.get("annotation_id"),
                    "knowledge_version": versions.get("knowledge"),
                    "source": "annotation",
                }
            )
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
    materials_without_source = 0
    materials_without_kp = 0
    materials_difficulty_unknown = 0
    for material in catalog_materials:
        material_id = str(material["material_id"])
        locator = material.get("source_locator") or {}
        source_refs: list[dict[str, Any]] = []
        if isinstance(locator, dict) and locator.get("source_file"):
            source_refs.append(
                _source_locator_ref(
                    locator,
                    material_id=material_id,
                    catalog_version=selection["catalog_version"],
                    source_label=material.get("source_label"),
                )
            )
            structured = any(
                locator.get(field)
                for field in ("file_id", "chapter", "section", "pdf_page", "printed_page")
            )
            if structured:
                locator_level = "structured"
            elif str(locator.get("source_file")) not in {"题目与材料", "材料表"}:
                locator_level = "label_only"
            else:
                locator_level = "table_only"
            source_refs[0]["locator_level"] = locator_level
        if source_refs:
            verified = (
                source_refs[0].get("verification_status") == "verified"
                and source_refs[0].get("locator_level") == "structured"
            )
            source_status = "verified" if verified else "unverified"
            if source_refs[0].get("locator_level") == "table_only":
                source_note = (
                    "来源只登记到输入表名，未定位到教材文件、章节或页码，标记 unverified"
                )
            elif source_refs[0].get("locator_level") == "label_only":
                source_note = (
                    "来源只有文字标签，未登记可解析的文件、章节或页码，标记 unverified"
                )
            else:
                source_note = "来源为目录登记的结构化定位，未在导出时复核原件"
        else:
            source_status = "unverified"
            source_note = "目录材料未登记出处定位，来源未知"
            materials_without_source += 1

        kp_refs: list[dict[str, Any]] = [
            dict(ref)
            for ref in kp_annotations.get(material_id, [])
            if ref["kp_id"] in knowledge_point_ids
        ]
        if not kp_refs:
            # 目录的材料-指标归属是正式映射，可回推到知识点；只在知识点确实存在时使用。
            for indicator_id in material.get("indicator_ids") or []:
                kp_id = f"kp-{indicator_id}"
                if kp_id in knowledge_point_ids:
                    kp_refs.append(
                        {
                            "kp_id": kp_id,
                            "role": "primary",
                            "basis": "standard",
                            "annotation_id": None,
                            "knowledge_version": versions.get("knowledge"),
                            "source": "indicator_mapping",
                        }
                    )
        kp_status = "mapped" if kp_refs else "unknown"
        if not kp_refs:
            materials_without_kp += 1

        difficulty = material.get("difficulty") or {}
        if difficulty.get("level") and difficulty.get("status"):
            difficulty_level = difficulty.get("level")
            difficulty_status = difficulty.get("status")
            difficulty_method = difficulty.get("method")
            difficulty_reason = difficulty.get("reason") or ""
            difficulty_rule_version = difficulty.get("rule_version")
        else:
            difficulty_level = "unknown"
            difficulty_status = "unknown"
            difficulty_method = None
            difficulty_reason = "目录材料 difficulty 为空，导出不推断难度"
            difficulty_rule_version = None
            materials_difficulty_unknown += 1

        materials.append(
            {
                "key": f"mat:{selection['catalog_version']}:{material_id}",
                "catalog_version": selection["catalog_version"],
                "material_id": material_id,
                "kind": material.get("material_type"),
                "title": (material.get("content") or "")[:80],
                "topic_ids": list(material.get("topic_ids") or []),
                "indicator_ids": list(material.get("indicator_ids") or []),
                "node_ids": node_ids,
                "kp_refs": kp_refs,
                "kp_status": kp_status,
                "kp_note": ""
                if kp_refs
                else "没有可核验的知识标注或指标映射，知识点关联标记 unknown",
                "source_refs": source_refs,
                "source_status": source_status,
                "source_note": source_note,
                "source_label": material.get("source_label"),
                "difficulty_level": difficulty_level,
                "difficulty_status": difficulty_status,
                "difficulty_method": difficulty_method,
                "difficulty_reason": difficulty_reason,
                "difficulty_rule_version": difficulty_rule_version,
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
    publication_counts: dict[str, dict[str, int]] = {
        "student_analysis": {"published": 0, "unpublished": 0},
        "class_analysis": {"published": 0, "unpublished": 0},
        "plan": {"published": 0, "unpublished": 0},
        "lesson": {"published": 0, "unpublished": 0},
    }
    summary_missing = 0
    summary_missing_current = 0

    def _analysis_result(payload: dict[str, Any], publication: dict[str, Any]) -> dict[str, Any]:
        """按发布记录校正载荷自述状态，并显式说明空摘要的含义。"""

        result = dict(payload)
        result["publication_status"] = publication["status"]
        raw_summary = result.get("summary")
        if raw_summary is None or not str(raw_summary).strip():
            result["summary"] = None
            result["summary_status"] = "not_provided"
            result["summary_note"] = (
                "正式学情结果未提供摘要字段，summary=null 表示无正式摘要，"
                "不代表没有结论；结论以 metrics、findings 与 evidence_refs 为准"
            )
        else:
            result["summary_status"] = "provided"
            result["summary_note"] = ""
        return result

    for version_id, meta in sorted(student_entries.items()):
        student_id = str(meta.get("student_id") or "")
        node = str(meta.get("node_id") or "")
        current = bool(meta.get("current"))
        payload = _load(db, version_id)
        publication = _publication_record(db, version_id)
        publication_counts["student_analysis"][publication["status"]] += 1
        result = _analysis_result(payload, publication)
        limitations = list(payload.get("limitations", []))
        if result["summary_status"] == "not_provided":
            summary_missing += 1
            if current:
                summary_missing_current += 1
            limitations.append(result["summary_note"])
        if publication["status"] != "published":
            limitations.append("该学情版本没有正式发布记录，包内按未发布状态保留")
        student_records.append(
            {
                "key": f"student:{version_id}:{student_id}",
                "student_id": student_id,
                "node_id": node,
                "analysis_version": version_id,
                "current": current,
                "publication": publication,
                "publication_status": publication["status"],
                "summary_status": result["summary_status"],
                "summary_note": result["summary_note"],
                "knowledge_version": knowledge_version,
                "catalog_version": selection["catalog_version"],
                "event_cutoff": selection["event_cutoff"],
                "result": result,
                "limitations": limitations,
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
            publication = _publication_record(db, version_id)
            publication_counts["class_analysis"][publication["status"]] += 1
            result = _analysis_result(payload, publication)
            limitations = list(payload.get("limitations", []))
            if result["summary_status"] == "not_provided":
                summary_missing += 1
                summary_missing_current += 1
                limitations.append(result["summary_note"])
            if publication["status"] != "published":
                limitations.append("该班级学情版本没有正式发布记录，包内按未发布状态保留")
            members = []
            for item in payload.get("member_analysis_versions", []) or []:
                if isinstance(item, dict):
                    member_id = str(item.get("student_id") or item.get("student_key") or "")
                    analysis_version = str(
                        item.get("analysis_version")
                        or versions["student"].get(member_id, {}).get(node, "")
                    )
                    member_state = str(
                        item.get("member_state")
                        or ("available" if analysis_version else "no_observation")
                    )
                else:
                    member_id = str(item)
                    analysis_version = versions["student"].get(member_id, {}).get(node, "")
                    member_state = "available" if analysis_version else "no_observation"
                members.append(
                    {
                        "student_key": member_id,
                        "student_analysis_key": f"student:{analysis_version}:{member_id}"
                        if analysis_version
                        else "",
                        "member_state": member_state,
                    }
                )
            class_records.append(
                {
                    "key": f"class:{version_id}:{class_id}",
                    "class_id": class_id,
                    "node_id": node,
                    "analysis_version": version_id,
                    "current": True,
                    "publication": publication,
                    "publication_status": publication["status"],
                    "summary_status": result["summary_status"],
                    "summary_note": result["summary_note"],
                    "result": result,
                    "members": members,
                    "limitations": limitations,
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
    groups_missing_teacher_reason = 0
    groups_missing_primary_strategy = 0
    groups_total = 0
    groups_current = 0
    groups_missing_teacher_reason_current = 0
    groups_missing_primary_strategy_current = 0
    plan_methods: set[str] = set()
    for version_id, meta in sorted(plan_entries.items()):
        student_id = str(meta.get("student_id") or "")
        payload = _load(db, version_id)
        plan = RecommendationBundle.model_validate(payload)
        publication = _publication_record(db, version_id)
        publication_counts["plan"][publication["status"]] += 1
        for recommendation in plan.recommendations:
            plan_methods.add(str(recommendation.generation_method))
        node_id = plan.node_id or str(meta.get("node_id") or "")
        current = bool(meta.get("current"))
        origin_version = plan.analysis_version or ""
        current_version = (
            versions["student"].get(student_id, {}).get(node_id)
            or versions["student"].get(student_id, {}).get(node_ids[0], "")
        )
        groups = [group.model_dump(mode="json") for group in plan.task_groups]
        recommendation_items = [
            {
                "recommendation_id": recommendation.recommendation_id,
                "group_id": recommendation.group_id,
                "indicator_id": recommendation.indicator_id,
                "goal": recommendation.goal,
                "reason": recommendation.reason,
                "student_intro": recommendation.student_intro,
                "strategy_types": list(recommendation.strategy_types),
                "kp_ids": list(recommendation.kp_ids),
                "review_status": recommendation.review_status,
                "generation_method": recommendation.generation_method,
                "evidence_refs": [
                    projected
                    for projected in (
                        _safe_evidence_ref(ref.model_dump(mode="json"))
                        for ref in recommendation.evidence_refs
                    )
                    if projected is not None
                ],
            }
            for recommendation in plan.recommendations
        ]
        for group in plan.task_groups:
            groups_total += 1
            if current:
                groups_current += 1
            if not str(group.teacher_reason or "").strip():
                groups_missing_teacher_reason += 1
                if current:
                    groups_missing_teacher_reason_current += 1
            if group.primary_strategy is None:
                groups_missing_primary_strategy += 1
                if current:
                    groups_missing_primary_strategy_current += 1
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
                "publication": publication,
                "publication_status": publication["status"],
                "publication_basis": plan.publication_basis or "automatic_review",
                "applicability": applicability,
                "groups": groups,
                "recommendations": recommendation_items,
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
    lessons_without_source = 0
    lessons_with_unresolved_citation = 0
    lesson_methods: set[str] = set()
    for version_id in sorted(content_ids):
        version = db.get(models.Version, version_id)
        if version is None:
            continue
        payload = dict(version.payload_json or {})
        body = dict(payload.get("body") or {})
        link = content_activity.get(version_id, {})
        publication = _publication_record(db, version_id)
        publication_counts["lesson"][publication["status"]] += 1
        evidence_refs = [
            ref
            for ref in (_safe_evidence_ref(item) for item in body.get("evidence_refs") or [])
            if ref is not None
        ]
        source_refs = [
            dict(item)
            for item in body.get("source_refs") or []
            if isinstance(item, dict) and item.get("kind")
        ]
        citation_checks: list[dict[str, Any]] = []
        for component in body.get("components") or []:
            if not isinstance(component, dict) or component.get("type") != "citation":
                continue
            labels: list[dict[str, Any]] = []
            for label in component.get("labels") or []:
                if not isinstance(label, dict):
                    continue
                source_index = label.get("source_index")
                evidence_index = label.get("evidence_index")
                resolved_ref: dict[str, Any] | None = None
                resolved_kind: str | None = None
                if (
                    isinstance(source_index, int)
                    and 0 <= source_index < len(source_refs)
                ):
                    resolved_ref = source_refs[source_index]
                    resolved_kind = "source"
                elif (
                    isinstance(evidence_index, int)
                    and 0 <= evidence_index < len(evidence_refs)
                ):
                    resolved_ref = evidence_refs[evidence_index]
                    resolved_kind = "evidence"
                labels.append(
                    {
                        "label": label.get("label", ""),
                        "source_index": source_index,
                        "evidence_index": evidence_index,
                        "resolved": resolved_ref is not None,
                        "resolved_kind": resolved_kind,
                        "resolved_ref": resolved_ref,
                    }
                )
            citation_checks.append(
                {
                    "component_id": component.get("id", ""),
                    "labels": labels,
                }
            )
        unresolved = [
            label
            for check in citation_checks
            for label in check["labels"]
            if not label["resolved"]
        ]
        if source_refs:
            verified_sources = all(
                str(item.get("verification_status") or "unverified") == "verified"
                for item in source_refs
            )
            source_status = "verified" if verified_sources else "unverified"
        elif evidence_refs:
            source_status = "evidence_only"
        else:
            source_status = "unverified"
        limitations = [str(item) for item in payload.get("limitations", []) or []]
        if not source_refs:
            lessons_without_source += 1
            limitations.append(
                "本微课堂未引用可核验教材原文；引用组件只解析到知识点/证据索引，"
                "source_status 标记 unverified"
            )
        if unresolved:
            lessons_with_unresolved_citation += 1
            limitations.append("引用组件存在无法解析的索引，已标记 unverified，不补造来源")
        creation_method = str(
            payload.get("creation_method")
            or (payload.get("review_info") or {}).get("method")
            or "unknown"
        )
        lesson_methods.add(creation_method)
        micro_lessons.append(
            {
                "key": f"content:{version_id}",
                "content_id": payload.get("content_id"),
                "content_version": version_id,
                "activity_id": link.get("activity_id", ""),
                "plan_version": payload.get("origin_plan_version")
                or link.get("plan_version", ""),
                "body": payload.get("body"),
                "creation_method": creation_method,
                "review_status": (payload.get("review_info") or {}).get("status"),
                "publication": publication,
                "publication_status": publication["status"],
                "evidence_refs": evidence_refs,
                "source_refs": source_refs,
                "source_status": source_status,
                "citation_verified": source_status == "verified",
                "citation_checks": citation_checks,
                "limitations": limitations,
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

    knowledge_methods: set[str] = set()
    knowledge_prompts: set[str] = set()
    knowledge_model_tagged = 0
    for point in knowledge_payload.get("points", []):
        review = point.get("review_info") or {}
        if review.get("method"):
            knowledge_methods.add(str(review["method"]))
            if str(review["method"]) == "model":
                knowledge_model_tagged += 1
        if review.get("prompt_version"):
            knowledge_prompts.add(str(review["prompt_version"]))
    for collection in ("relations", "annotations"):
        for item in knowledge_payload.get(collection, []):
            review = item.get("review_info") or {}
            if review.get("method"):
                knowledge_methods.add(str(review["method"]))
            if review.get("prompt_version"):
                knowledge_prompts.add(str(review["prompt_version"]))

    analysis_methods: set[str] = set()
    analysis_limitations: list[str] = []
    for record in student_records + class_records:
        result = record.get("result") or {}
        analysis_limitations.extend(str(item) for item in result.get("limitations", []) or [])
        for finding in list(result.get("findings", [])) + list(
            result.get("common_findings", [])
        ):
            if finding.get("generation_method"):
                analysis_methods.add(str(finding["generation_method"]))
    analysis_model_attempted = any(
        "MODEL_TIMEOUT" in item or "模型" in item for item in analysis_limitations
    )

    plan_prompts: set[str] = set()
    for version_id in plan_entries:
        payload = _load(db, version_id)
        review = payload.get("review_info") or {}
        if review.get("method"):
            plan_methods.add(str(review["method"]))
        if review.get("prompt_version"):
            plan_prompts.add(str(review["prompt_version"]))

    plan_method = _module_method(plan_methods)
    if plan_method == "rule":
        plan_note = "方案建议由规则生成并审核"
    elif "model" in plan_methods or "hybrid" in plan_methods:
        plan_note = "方案版本载荷记录了模型参与；以载荷为准，本次导出不调用模型"
    else:
        plan_note = "部分方案版本由教师修订或确认（generation_method=human）；以载荷为准"
    plan_generation = {
        "method": plan_method,
        "methods": sorted(plan_methods),
        "prompt_versions": sorted(plan_prompts),
        "provenance": "正式方案版本载荷 recommendation.generation_method / review_info",
        "note": plan_note,
    }
    knowledge_generation = {
        "method": _module_method(knowledge_methods),
        "methods": sorted(knowledge_methods),
        "prompt_versions": sorted(knowledge_prompts),
        "model_tagged_point_count": knowledge_model_tagged,
        "provenance": "正式知识版本载荷 review_info",
        "note": (
            "知识版本中有 "
            f"{knowledge_model_tagged} 个知识点记录 review_info.method=model；"
            "该标记来自版本创建或复核时，不代表本次导出调用模型"
            if knowledge_model_tagged
            else "知识版本的复核记录为规则方法"
        ),
    }
    analysis_generation = {
        "method": _module_method(analysis_methods),
        "methods": sorted(analysis_methods),
        "model_attempted": analysis_model_attempted,
        "provenance": "正式学情版本载荷 findings.generation_method / limitations",
        "note": (
            "模型语义诊断曾尝试但未成功（存在 MODEL_TIMEOUT 等限制），"
            "本包只含规则统计与评分，未生成模型解释"
            if analysis_model_attempted
            else "学情统计、评分与诊断由规则生成"
        ),
    }
    lesson_generation = {
        "method": _module_method(lesson_methods),
        "methods": sorted(lesson_methods),
        "provenance": "正式微课堂版本载荷 creation_method / review_info",
        "note": "微课堂正文由规则模板生成"
        if _module_method(lesson_methods) == "rule"
        else "微课堂版本载荷记录了模型参与；以载荷为准",
    }
    generation_sources = {
        "export": {
            "method": "rule",
            "note": "导出只读取冻结的正式版本并序列化，不调用模型",
        },
        "knowledge": knowledge_generation,
        "student_analysis": analysis_generation,
        "plan": plan_generation,
        "micro_lesson": lesson_generation,
    }
    used_methods = {"rule"} if generation_sources["export"]["method"] == "rule" else set()
    for module in ("knowledge", "student_analysis", "plan", "micro_lesson"):
        method = generation_sources[module]["method"]
        if method in {"model", "hybrid"}:
            used_methods.add("model")
        if "rule" in method:
            used_methods.add("rule")
        if "human" in method:
            used_methods.add("human")
    generation_method = [
        method
        for method in ("rule", "model", "hybrid", "human")
        if method in used_methods
    ] or ["unknown"]

    source_manifest = dict(dataset.manifest_json or {})
    family = (
        db.get(models.DatasetFamily, dataset.family_id) if dataset.family_id else None
    )
    synthetic = bool(source_manifest.get("synthetic", False))
    declared_origin = str(
        source_manifest.get("data_origin") or (family.data_origin if family else "") or ""
    ).strip()
    if synthetic:
        data_origin_kind = "synthetic"
    elif declared_origin in {"official", "synthetic", "sample", "mixed"}:
        data_origin_kind = declared_origin
    else:
        data_origin_kind = "unknown"
    origin_notes = {
        "official": "赛方提供的正式数据；使用范围以参赛授权与数据说明为准",
        "synthetic": "团队构造的合成测试数据，只用于验证系统流程，不能用于证明真实教学成效",
        "sample": "赛方样例数据，只用于演示流程，不代表真实教学结果",
        "mixed": "本包混合了多种来源的数据，需按 source_packages 分开核对",
        "unknown": "数据集未登记可识别的来源类型，导出不做推断",
    }
    source_files = [
        {"role": row.source_key, "file_id": row.file_id}
        for row in db.scalars(
            select(models.DatasetSource)
            .where(models.DatasetSource.dataset_id == dataset.id)
            .order_by(models.DatasetSource.source_key)
        ).all()
    ]
    data_origin = {
        "kind": data_origin_kind,
        "synthetic": synthetic,
        "dataset_id": dataset.id,
        "dataset_name": dataset.display_name or "",
        "dataset_family_id": dataset.family_id,
        "dataset_created_at": dataset.created_at,
        "snapshot_hash": dataset.snapshot_hash,
        "parent_dataset_id": source_manifest.get("parent_dataset_id"),
        "catalog_version": selection["catalog_version"],
        "rule_version": selection["rule_version"],
        "table_row_counts": dict(source_manifest.get("table_row_counts") or {}),
        "source_packages": [
            {
                "dataset_id": dataset.id,
                "parent_dataset_id": source_manifest.get("parent_dataset_id"),
                "declared_origin": declared_origin or None,
                "synthetic": synthetic,
            }
        ],
        "source_files": source_files,
        "declared_by": "dataset_family.data_origin"
        if family is not None and family.data_origin
        else "source_manifest.data_origin",
        "note": origin_notes[data_origin_kind],
        "limitations": [
            "来源类型取自导入登记，未与外部数据包原件逐项核对",
        ],
    }

    material_id_set = {str(material["material_id"]) for material in materials}
    activity_material_missing = 0
    activity_kp_missing = 0
    for plan in plan_records:
        for activity in plan.get("activities") or []:
            material_id = str(activity.get("material_id") or "")
            if material_id and material_id not in material_id_set:
                activity_material_missing += 1
            for kp_id in activity.get("knowledge_ids") or []:
                if str(kp_id) not in knowledge_point_ids:
                    activity_kp_missing += 1

    manifest_limitations: list[str] = []
    if data_origin["synthetic"]:
        manifest_limitations.append(
            "数据集为团队构造的合成测试数据，只用于验证系统流程，不能用于证明真实教学成效"
        )
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
    if summary_missing:
        manifest_limitations.append(
            f"学情摘要为空：{summary_missing} 份学生/班级正式结果的 summary 为 null"
            f"（其中当前版本 {summary_missing_current} 份）。"
            "B 未提供摘要字段，导出保留 null 并用 summary_status=not_provided 标明，"
            "结论以 metrics、findings 与 evidence_refs 为准"
        )
    if materials_difficulty_unknown:
        manifest_limitations.append(
            f"材料难度未知：{materials_difficulty_unknown} 条材料的目录 difficulty 为 null。"
            "导出把 difficulty_level/difficulty_status 标为 unknown，"
            "不按历史得分或题干猜测难度"
        )
    if groups_missing_teacher_reason or groups_missing_primary_strategy:
        manifest_limitations.append(
            "任务组解释字段为空："
            f"{groups_missing_teacher_reason}/{groups_total} 组缺 teacher_reason，"
            f"{groups_missing_primary_strategy}/{groups_total} 组缺 primary_strategy"
            f"（当前方案 {groups_missing_teacher_reason_current}/{groups_current} 组缺 reason，"
            f"{groups_missing_primary_strategy_current}/{groups_current} 组缺 strategy）。"
            "导出层如实保留正式方案载荷里的空值，不补写理由或策略；"
            "教师理由与策略列表见 recommendations.json 的 recommendations[].reason/strategy_types"
        )
    if lessons_without_source:
        manifest_limitations.append(
            f"微课堂教材来源：{lessons_without_source}/{len(micro_lessons)} 份的正文 source_refs 为空。"
            "这些微课堂按 evidence_only/unverified 输出，引用组件只指向证据索引，"
            "不显示成已引用教材原文"
        )
    if lessons_with_unresolved_citation:
        manifest_limitations.append(
            f"微课堂引用索引：{lessons_with_unresolved_citation}/{len(micro_lessons)} 份存在无法解析的引用标签，"
            "已标记 unverified，不补造来源"
        )
    if activity_material_missing:
        manifest_limitations.append(
            f"活动材料引用：{activity_material_missing} 项活动的 material_id 不在 materials_index.json，"
            "未静默改写，需回到正式题库核对"
        )
    if activity_kp_missing:
        manifest_limitations.append(
            f"活动知识点引用：{activity_kp_missing} 个 knowledge_ids 不在 knowledge_graph.json，"
            "未补造知识点"
        )
    if knowledge_version is None:
        manifest_limitations.append("本包没有正式知识版本，材料知识点关联只能是 unknown")
    unpublished_analysis = (
        publication_counts["student_analysis"]["unpublished"]
        + publication_counts["class_analysis"]["unpublished"]
    )
    if unpublished_analysis:
        manifest_limitations.append(
            f"包内有 {unpublished_analysis} 份学情版本没有正式发布记录，"
            "result.publication_status 按真实状态保留为 unpublished"
        )
    data_origin_limitations = list(data_origin["limitations"])
    content_issues: list[str] = []
    if activity_material_missing:
        content_issues.append(
            f"{activity_material_missing} 项活动引用了材料目录外的题目"
        )
    if activity_kp_missing:
        content_issues.append(
            f"{activity_kp_missing} 个活动知识点引用不在知识图谱内"
        )
    if materials_without_source:
        content_issues.append(f"{materials_without_source} 条材料缺少出处引用")
    if materials_without_kp:
        content_issues.append(f"{materials_without_kp} 条材料缺少可解析知识点引用")
    if materials_difficulty_unknown:
        content_issues.append(f"{materials_difficulty_unknown} 条材料难度未知")
    if summary_missing:
        content_issues.append(f"{summary_missing} 份学情结果没有正式摘要")
    if groups_missing_teacher_reason or groups_missing_primary_strategy:
        content_issues.append("任务组缺少教师理由或主要策略")
    if lessons_without_source:
        content_issues.append(f"{lessons_without_source} 份微课堂没有教材来源引用")
    if lessons_with_unresolved_citation:
        content_issues.append(
            f"{lessons_with_unresolved_citation} 份微课堂引用索引无法解析"
        )
    if unpublished_analysis:
        content_issues.append(f"{unpublished_analysis} 份学情版本没有发布记录")
    content_status = "passed" if not content_issues else "passed_with_limitations"

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
        "generation_method": generation_method,
        "generation_sources": generation_sources,
        "data_origin": data_origin,
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
        "publication": publication_counts,
        "evidence_index": {"file": "evidence_records.json", "record_count": len(evidence)},
        "content_checks": {
            "analysis_records": len(student_records) + len(class_records),
            "analysis_summary_missing": summary_missing,
            "analysis_summary_missing_current": summary_missing_current,
            "analysis_unpublished": unpublished_analysis,
            "materials": len(materials),
            "materials_with_source_refs": len(materials) - materials_without_source,
            "materials_without_source_refs": materials_without_source,
            "materials_with_kp_refs": len(materials) - materials_without_kp,
            "materials_without_kp_refs": materials_without_kp,
            "materials_difficulty_unknown": materials_difficulty_unknown,
            "groups": groups_total,
            "groups_current": groups_current,
            "groups_missing_teacher_reason": groups_missing_teacher_reason,
            "groups_missing_teacher_reason_current": groups_missing_teacher_reason_current,
            "groups_missing_primary_strategy": groups_missing_primary_strategy,
            "groups_missing_primary_strategy_current": groups_missing_primary_strategy_current,
            "activities": sum(len(plan.get("activities") or []) for plan in plan_records),
            "activities_material_not_in_index": activity_material_missing,
            "activity_kp_refs_not_in_graph": activity_kp_missing,
            "lessons": len(micro_lessons),
            "lessons_without_source_refs": lessons_without_source,
            "lessons_with_unresolved_citation": lessons_with_unresolved_citation,
        },
        "coverage": {
            "students": len(versions["student"]),
            "classes": len(versions["class"]),
            "cases": len(case_ids),
            "limitations": data_origin_limitations,
        },
        "files": [],
        "validation": {
            "preflight": "passed",
            "payload": "pending",
            "content": content_status,
            "content_issues": content_issues,
            "check_report_id": None,
            "checked_at": None,
        },
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
    items.extend(
        [
            {
                "key": "student_analysis.json:result.publication_status",
                "file": "student_analysis.json",
                "field_path": "result.publication_status",
                "type": "str",
                "required": True,
                "nullable": False,
                "meaning": "取自正式发布记录 publications 的真实状态，不是载荷自述；published 表示该版本已有发布记录",
                "example": "published",
            },
            {
                "key": "student_analysis.json:result.summary",
                "file": "student_analysis.json",
                "field_path": "result.summary",
                "type": "str",
                "required": False,
                "nullable": True,
                "meaning": "B 结果没有正式摘要字段时为 null；null 表示无摘要，不代表无结论。用 summary_status 与 metrics/findings 核对",
                "example": "null",
            },
            {
                "key": "student_analysis.json:summary_status",
                "file": "student_analysis.json",
                "field_path": "summary_status",
                "type": "str",
                "required": True,
                "nullable": False,
                "meaning": "not_provided 表示正式结果未提供摘要；provided 表示载荷自带摘要",
                "example": "not_provided",
            },
            {
                "key": "materials_index.json:source_refs",
                "file": "materials_index.json",
                "field_path": "source_refs",
                "type": "list",
                "required": True,
                "nullable": False,
                "meaning": "材料出处投影；空数组表示目录未登记出处，用 source_status=unverified 标明，不补造页码",
                "example": '[{"kind": "material_source", "source_file": "..."}]',
            },
            {
                "key": "materials_index.json:kp_refs",
                "file": "materials_index.json",
                "field_path": "kp_refs",
                "type": "list",
                "required": True,
                "nullable": False,
                "meaning": "材料到知识点的可解析关联；source=annotation 为正式知识标注，source=indicator_mapping 为目录指标归属，无证据时 kp_status=unknown",
                "example": '[{"kp_id": "kp-T01I1", "role": "primary"}]',
            },
            {
                "key": "materials_index.json:difficulty_level",
                "file": "materials_index.json",
                "field_path": "difficulty_level",
                "type": "str",
                "required": True,
                "nullable": False,
                "meaning": "目录 material.difficulty 的等级；没有评估时固定为 unknown，不按得分或题干猜测",
                "example": "unknown",
            },
            {
                "key": "micro_lessons.json:evidence_refs",
                "file": "micro_lessons.json",
                "field_path": "evidence_refs",
                "type": "list",
                "required": True,
                "nullable": False,
                "meaning": "微课堂正文 evidence_refs 的安全投影（仅保留定位字段），引用组件的 evidence_index 按此数组解析",
                "example": '[{"kind": "knowledge_point", "id": "kp-T01I1"}]',
            },
            {
                "key": "micro_lessons.json:source_refs",
                "file": "micro_lessons.json",
                "field_path": "source_refs",
                "type": "list",
                "required": True,
                "nullable": False,
                "meaning": "微课堂正文 source_refs 的原样投影；空数组表示未引用教材原文，source_status=unverified/evidence_only",
                "example": "[]",
            },
            {
                "key": "micro_lessons.json:citation_checks",
                "file": "micro_lessons.json",
                "field_path": "citation_checks",
                "type": "list",
                "required": True,
                "nullable": False,
                "meaning": "逐条检查引用组件的 source_index/evidence_index 能否在 source_refs/evidence_refs 内解析；resolved=false 表示索引无效",
                "example": '[{"component_id": "source", "labels": [{"resolved": true}]}]',
            },
            {
                "key": "recommendations.json:recommendations.reason",
                "file": "recommendations.json",
                "field_path": "recommendations[].reason",
                "type": "str",
                "required": True,
                "nullable": False,
                "meaning": "正式方案建议自带的教师可读理由；任务组 teacher_reason 为空时，用它核对为什么这样安排，导出不把它改写成任务组字段",
                "example": "存在具体错误证据，安排纠错；依据 ...",
            },
            {
                "key": "recommendations.json:groups.teacher_reason",
                "file": "recommendations.json",
                "field_path": "groups[].teacher_reason",
                "type": "str",
                "required": False,
                "nullable": False,
                "meaning": "正式方案任务组的教师理由；导出不补写，空值表示版本里没有该字段内容",
                "example": "",
            },
            {
                "key": "recommendations.json:groups.primary_strategy",
                "file": "recommendations.json",
                "field_path": "groups[].primary_strategy",
                "type": "str",
                "required": False,
                "nullable": True,
                "meaning": "正式方案任务组的主要策略；null 表示版本未填，不从 strategy_types 推断",
                "example": "null",
            },
            {
                "key": "manifest.json:generation_sources",
                "file": "manifest.json",
                "field_path": "generation_sources",
                "type": "dict",
                "required": True,
                "nullable": False,
                "meaning": "按模块记录版本载荷里的生成/复核来源；知识或微课堂的 model 标记可能来自历史版本创建，不代表本次导出调用模型",
                "example": '{"export": {"method": "rule"}, "knowledge": {"method": "rule"}}',
            },
            {
                "key": "manifest.json:data_origin",
                "file": "manifest.json",
                "field_path": "data_origin",
                "type": "dict",
                "required": True,
                "nullable": False,
                "meaning": "数据来源类型、数据集名称、是否合成与导入登记的表行数；不使用 ready 等状态词代替来源",
                "example": '{"kind": "official", "synthetic": false}',
            },
            {
                "key": "manifest.json:validation",
                "file": "manifest.json",
                "field_path": "validation",
                "type": "dict",
                "required": True,
                "nullable": False,
                "meaning": "打包与回读校验结论；payload=passed 时同时给出 check_report_id 与 checked_at，pending 表示尚未完成",
                "example": '{"preflight": "passed", "payload": "passed"}',
            },
        ]
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
    origin = dict(manifest.get("data_origin") or {})
    sources = dict(manifest.get("generation_sources") or {})
    validation = dict(manifest.get("validation") or {})
    method_names = {
        "rule": "规则",
        "model": "模型",
        "hybrid": "规则+模型",
        "human": "教师修订/确认",
        "rule+human": "规则+教师确认",
        "unknown": "未知",
    }
    module_names = {
        "export": "导出打包",
        "knowledge": "A 知识体系",
        "student_analysis": "B 学情分析",
        "plan": "C 学习方案",
        "micro_lesson": "C 微课堂",
    }
    rows = []
    for module in ("export", "knowledge", "student_analysis", "plan", "micro_lesson"):
        item = dict(sources.get(module) or {})
        method = str(item.get("method") or "unknown")
        rows.append(
            f"| {module_names[module]} | {method_names.get(method, method)}（{method}） "
            f"| {item.get('note') or ''} |"
        )
    limitations = [str(item) for item in manifest.get("limitations") or []]
    limitations_text = (
        "\n".join(f"- {item}" for item in limitations) if limitations else "- 无额外限制"
    )
    row_counts = origin.get("table_row_counts") or {}
    row_counts_text = "、".join(
        f"{name} {count}" for name, count in sorted(row_counts.items())
    ) or "未登记"
    validation_status = str(validation.get("payload") or "pending")
    report_id = validation.get("check_report_id") or "未生成"
    checked_at = validation.get("checked_at") or "未生成"
    return f"""# 湛知正式成果包

- 数据集：{dataset.id}（系列 {dataset.family_id}）
- 数据集名称：{origin.get('dataset_name') or '未登记'}
- 运行：{manifest['run_id']}
- 目录版本：{manifest['catalog_version']}
- 节点：{'、'.join(manifest['node_ids'])}
- 生成时间：{manifest['created_at']}
- 快照：{manifest['snapshot_id']}

## 生成方式（按模块记录）

| 模块 | 实际来源 | 说明 |
| --- | --- | --- |
{chr(10).join(rows)}

模块来源取自正式版本载荷里的 `generation_method`、`creation_method` 与 `review_info`；
知识或微课堂中的 `model` 标记可能来自版本创建或独立复核，不代表本次导出调用模型。
导出层只读冻结版本，不调用模型，也不把历史模型标记改写成规则。

## 数据来源

- 类型：{origin.get('kind') or 'unknown'}
- 是否合成：{'是' if origin.get('synthetic') else '否'}
- 数据集：{origin.get('dataset_id')}（系列 {origin.get('dataset_family_id')}）
- 登记行数：{row_counts_text}
- 说明：{origin.get('note') or '未登记来源说明'}

数据来源类型取自导入登记（{origin.get('declared_by') or '未登记'}），
未与外部数据包原件逐项核对；不使用 `ready` 等状态词代替来源。

## 三项任务对应文件

1. 课程要求与知识体系：`curriculum.json`、`knowledge_graph.json`、`materials_index.json`
2. 学情分析：`student_analysis.json`、`class_analysis.json`、`metrics.csv`、`data_quality.json`
3. 个性化方案：`recommendations.json`、`micro_lessons.json`、`learning_history.json`

样例核查入口：`examples/input.json` 与 `examples/output.json` 按 `case_id` 一一对应。

## 核对方式

- 每个文件在 `manifest.json` 的 `files` 中都有 `sha256` 与 `bytes`，可直接校验；
- `manifest.json` 的 `publication` 记录每类版本的发布状态，`content_checks` 记录来源、
  摘要、难度与任务组字段的缺失数量；
- `evidence_records.json` 提供可解析的证据引用，指向冻结时的正式记录；
- `field_dictionary.json` 逐文件列出字段含义与空值语义。

## 回读校验

- preflight：{validation.get('preflight') or 'pending'}
- payload：{validation_status}
- 内容检查：{validation.get('content') or 'pending'}
- 检查报告：{report_id}
- 检查时间：{checked_at}

payload 表示回读校验（文件存在、哈希、JSON/CSV 可解析）；内容检查单独记录
来源、摘要、难度与任务组字段的缺失项，不把文件校验通过当成业务内容合格。
只有 payload 为 passed 时，manifest 才写入报告 ID 与时间；未执行或失败保持 pending/failed。
内容检查问题：{'；'.join(validation.get('content_issues') or []) or '无'}

## 适用边界

- 本包冻结自提交时的正式版本，之后的新作答与新版方案不会改变本包；
- 学情结论保留证据充分性与限制，未把"无记录"当作 0 分；
- 学生/班级 `result.publication_status` 取自正式发布记录；`summary=null` 表示正式结果
  没有提供摘要字段，不代表没有结论；
- 材料来源与知识点关联只投影目录和知识标注里已有的证据；缺失时标 unverified/unknown，
  不补造教材页码；
- 微课堂引用组件按 `source_refs`/`evidence_refs` 的索引解析；没有教材来源时标
  evidence_only/unverified，不显示成已引用教材；
- 任务组 `teacher_reason`/`primary_strategy` 为空时如实保留，导出层不补写；
- 以下限制来自本包内容检查：
{limitations_text}
"""


# --------------------------------------------------------------------------- #
# 打包与回读
# --------------------------------------------------------------------------- #


def _write_archive(path: str | Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name)
            info.date_time = (2026, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, files[name])


def build_archive(db: Session, export: models.Export, output_root: Path) -> dict[str, Any]:
    files = build_files(db, export)
    selection = dict(export.selection_json or {})
    dataset = db.get(models.Dataset, selection["dataset_id"])
    if dataset is None:
        raise DomainError("EXPORT_INCOMPLETE", "冻结范围引用的数据集已不可读")
    target_dir = output_root / "exports"
    target_dir.mkdir(parents=True, exist_ok=True)
    final_path = target_dir / f"{export.export_id}.zip"
    handle, temp_name = tempfile.mkstemp(dir=target_dir, prefix=".tmp-", suffix=".zip")
    os.close(handle)
    report_id = f"check-{export.export_id}"
    try:
        # 第一遍按 pending 写包并回读；通过后才把真实校验结论写回包内 manifest。
        _write_archive(temp_name, files)
        first = verify_archive(Path(temp_name), files)
        if not first["passed"]:
            raise DomainError(
                "EXPORT_ARTIFACT_INVALID",
                "导出包回读校验失败：" + "；".join(first["issues"][:3]),
            )
        checked_at = _stamp()
        manifest = json.loads(files["manifest.json"].decode("utf-8"))
        existing_validation = dict(manifest.get("validation") or {})
        manifest["validation"] = {
            **existing_validation,
            "preflight": "passed",
            "payload": "passed",
            "check_report_id": report_id,
            "checked_at": checked_at,
            "checks": [
                "file_presence",
                "sha256",
                "json_parse",
                "csv_encoding",
            ],
            "issues": [],
        }
        files["manifest.json"] = _json_bytes(manifest)
        files["README.md"] = _readme(manifest, dataset).encode("utf-8")
        # 第二遍带上最终 manifest/README 再回读；manifest 的文件清单不包含这两个文件，
        # 所以业务文件哈希不变，断言仍可覆盖整包。
        _write_archive(temp_name, files)
        report = verify_archive(Path(temp_name), files)
        if not report["passed"]:
            raise DomainError(
                "EXPORT_ARTIFACT_INVALID",
                "导出包回读校验失败：" + "；".join(report["issues"][:3]),
            )
        report["report_id"] = report_id
        report["checked_at"] = checked_at
        report["manifest_sha256"] = _sha(files["manifest.json"])
        os.replace(temp_name, final_path)
    finally:
        Path(temp_name).unlink(missing_ok=True)
    archive_hash = _sha(final_path.read_bytes())
    case_mode = selection.get("case_selection", "explicit")
    report["case_selection"] = {
        "mode": case_mode,
        "note": _case_selection_note(case_mode),
    }
    if selection.get("warnings"):
        report["warnings"] = list(selection["warnings"])
    report["dependency_edges"] = len(manifest.get("dependency_edges") or [])
    report["content_checks"] = manifest.get("content_checks") or {}
    report["content_status"] = (manifest.get("validation") or {}).get("content")
    report["content_issues"] = (manifest.get("validation") or {}).get(
        "content_issues"
    ) or []
    report["publication"] = manifest.get("publication") or {}
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
