"""正式知识体系的读取与证据解析（02 §12）。

只读已发布快照；候选与审核日志不进入正式读取路径。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.contracts import (
    CurriculumDetail,
    DomainError,
    EvidenceDetail,
    EvidenceRef,
    SourceLocator,
)
from study_companion.db import models
from study_companion.db import version_repository as repo


def formal_snapshot(db: Session, dataset_id: str, knowledge_version: str | None = None) -> dict[str, Any]:
    dataset = db.get(models.Dataset, dataset_id)
    if dataset is None:
        raise DomainError("NOT_FOUND", "数据集不存在")
    if knowledge_version:
        version = db.get(models.Version, knowledge_version)
        if version is None or version.kind != "knowledge" or version.dataset_id != dataset_id:
            raise DomainError("KB_NOT_FOUND", "知识版本不存在")
    else:
        key = repo.FormalKey(
            family_id=dataset.family_id or "", dataset_id=dataset_id, kind="knowledge"
        )
        version = repo.read_formal(db, key)
        if version is None:
            raise DomainError("RESULT_NOT_READY", "尚未构建正式知识体系")
    return {"version": version, "payload": dict(version.payload_json or {})}


def find_point(payload: dict[str, Any], kp_id: str) -> dict[str, Any] | None:
    for point in payload.get("points", []):
        if point.get("kp_id") == kp_id:
            return point
    return None


def resolve_knowledge_evidence(
    db: Session, ref: EvidenceRef, scope
) -> EvidenceDetail:
    if not ref.knowledge_version:
        raise DomainError("VALIDATION_ERROR", "知识引用缺少 knowledge_version")
    version = db.get(models.Version, ref.knowledge_version)
    if version is None or version.kind != "knowledge":
        raise DomainError("KB_NOT_FOUND", "知识版本不存在")
    if scope.dataset_id is not None and version.dataset_id != scope.dataset_id:
        raise DomainError("FORBIDDEN", "当前会话无权访问该知识版本")
    payload = dict(version.payload_json or {})

    if ref.kind == "knowledge_point":
        point = find_point(payload, ref.id)
        if point is None:
            raise DomainError("KB_NOT_FOUND", "知识点不存在")
        detail = CurriculumDetail(
            title=point.get("name", ref.id),
            description=point.get("definition", ""),
            source_locators=[SourceLocator.model_validate(item) for item in point.get("source_locators", [])],
            snippet=point.get("snippet"),
            basis="课程指标与已登记来源",
        )
        return EvidenceDetail(
            ref=ref,
            title=detail.title,
            detail_type="curriculum",
            summary=detail.description[:120],
            detail=detail.model_dump(mode="json"),
            limitations=list(payload.get("limitations", [])),
        )

    if ref.kind == "relation":
        for relation in payload.get("relations", []):
            if relation.get("relation_id") == ref.id:
                detail = CurriculumDetail(
                    title=f"知识关系 {relation.get('type')}",
                    description=relation.get("note", ""),
                    basis=relation.get("basis"),
                )
                return EvidenceDetail(
                    ref=ref,
                    title=detail.title,
                    detail_type="curriculum",
                    summary=detail.description[:120],
                    detail={**detail.model_dump(mode="json"), "relation": relation},
                )
        raise DomainError("KB_NOT_FOUND", "知识关系不存在")

    if ref.kind == "annotation":
        for annotation in payload.get("annotations", []):
            if annotation.get("annotation_id") == ref.id:
                detail = CurriculumDetail(
                    title=f"知识关联 {annotation.get('source_id')}",
                    description=annotation.get("evidence", ""),
                    basis=annotation.get("basis"),
                )
                return EvidenceDetail(
                    ref=ref,
                    title=detail.title,
                    detail_type="curriculum",
                    summary=detail.description[:120],
                    detail={**detail.model_dump(mode="json"), "annotation": annotation},
                )
        raise DomainError("KB_NOT_FOUND", "知识关联不存在")

    raise DomainError("RESULT_NOT_READY", "微课堂内容尚未发布")


def points_for_indicator(payload: dict[str, Any], indicator_id: str) -> list[dict[str, Any]]:
    return [
        point
        for point in payload.get("points", [])
        if indicator_id in (point.get("indicator_ids") or [])
    ]


def primary_points_for_material(payload: dict[str, Any], material_id: str) -> list[str]:
    targets: list[str] = []
    for annotation in payload.get("annotations", []):
        if annotation.get("source_id") == material_id and annotation.get("knowledge_role") == "primary":
            targets.extend(annotation.get("target_ids", []))
    return sorted(set(targets))


def prerequisite_sources(payload: dict[str, Any], kp_id: str) -> list[str]:
    """只沿 prerequisite 反查前置，逻辑关系不得冒充学习顺序。"""

    sources: list[str] = []
    for relation in payload.get("relations", []):
        if relation.get("type") != "prerequisite":
            continue
        if relation.get("target_id") == kp_id:
            sources.extend(relation.get("source_ids", []))
    return sorted(set(sources))


def knowledge_version_for(db: Session, dataset_id: str) -> str | None:
    dataset = db.get(models.Dataset, dataset_id)
    if dataset is None or not dataset.family_id:
        return None
    key = repo.FormalKey(family_id=dataset.family_id, dataset_id=dataset_id, kind="knowledge")
    version = repo.read_formal(db, key)
    return version.version_id if version else None
