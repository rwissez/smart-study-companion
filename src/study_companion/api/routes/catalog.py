"""Catalog and material reads, with role-based trimming.

Student sessions never receive ``reference_answer``, ``reference_explanation``,
``rubric_points`` or the review tag — the trimming happens here, server-side,
not in the frontend.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select

from study_companion.api.deps import DbSession, Scope
from study_companion.api.envelope import success
from study_companion.api.sessions import SessionScope
from study_companion.contracts import DomainError
from study_companion.db import models

router = APIRouter(tags=["catalog"])

TEACHER_ONLY_MATERIAL_FIELDS = (
    "reference_answer",
    "reference_explanation",
    "rubric_points",
    "reviewed_variant_tag",
)


def trim_material(payload: dict[str, Any], scope: SessionScope) -> dict[str, Any]:
    if scope.role != "student":
        return payload
    return {k: v for k, v in payload.items() if k not in TEACHER_ONLY_MATERIAL_FIELDS}


def _resolve_catalog_version(db: DbSession, scope: SessionScope, requested: str | None) -> str:
    if requested is not None:
        row = db.get(models.Catalog, requested)
        if row is None:
            raise DomainError("NOT_FOUND", "课程目录版本不存在")
        if scope.dataset_id is not None and row.dataset_id != scope.dataset_id:
            raise DomainError("FORBIDDEN", "当前会话无权访问该课程目录")
        return requested

    if scope.dataset_id is None:
        raise DomainError("VALIDATION_ERROR", "请提供 catalog_version 或先选择数据集")
    row = db.scalar(
        select(models.Catalog)
        .where(models.Catalog.dataset_id == scope.dataset_id)
        .order_by(models.Catalog.created_at.desc())
    )
    if row is None:
        raise DomainError("NOT_FOUND", "该数据集尚无课程目录")
    return row.version


@router.get("/catalog")
def get_catalog(
    db: DbSession,
    scope: Scope,
    catalog_version: str | None = Query(default=None, max_length=64),
) -> dict:
    version = _resolve_catalog_version(db, scope, catalog_version)
    indicators = db.scalars(
        select(models.Indicator)
        .where(models.Indicator.catalog_version == version)
        .order_by(models.Indicator.indicator_id)
    ).all()
    topics: dict[str, dict[str, Any]] = {}
    for row in indicators:
        topic_id = row.payload_json.get("topic_id")
        if topic_id and topic_id not in topics:
            topics[topic_id] = {"topic_id": topic_id}
    return success(
        {
            "catalog_version": version,
            "topics": list(topics.values()),
            "indicators": [row.payload_json for row in indicators],
        }
    )


@router.get("/materials/{material_id}")
def get_material(
    material_id: str,
    db: DbSession,
    scope: Scope,
    catalog_version: str | None = Query(default=None, max_length=64),
) -> dict:
    version = _resolve_catalog_version(db, scope, catalog_version)
    row = db.get(models.Material, (version, material_id))
    if row is None:
        raise DomainError("NOT_FOUND", "材料不存在")
    return success(trim_material(dict(row.payload_json), scope))
