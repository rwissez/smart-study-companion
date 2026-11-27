"""材料与题库读取、检索。

学生投影走显式 allowlist：答案、解析、评分细则、变式标签只在教师视图出现。
即使同一材料另有进行中的同题活动，也不能因参数而解锁答案（01 §10.3）。
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import select

from study_companion.api.deps import DbSession, Scope
from study_companion.api.envelope import success
from study_companion.contracts import DomainError, Material, MaterialView
from study_companion.db import models
from study_companion.library.datasets import latest_catalog_version, load_catalog

router = APIRouter(tags=["materials"])

TEACHER_ONLY_FIELDS = (
    "reference_answer",
    "reference_explanation",
    "rubric_points",
    "reviewed_variant_tag",
    "answer_math",
    "explanation_math",
)


def material_view(
    material: Material, catalog_version: str, *, teacher: bool
) -> MaterialView:
    payload = {
        "material_id": material.material_id,
        "material_type": material.material_type,
        "catalog_version": catalog_version,
        "topic_ids": material.topic_ids,
        "indicator_ids": material.indicator_ids,
        "content": material.content,
        "options": material.options,
        "question_type": material.question_type,
        "source_label": material.source_label,
        "source_locator": material.source_locator,
        "max_score": material.max_score,
        "difficulty_level": material.difficulty.level if material.difficulty else None,
        "difficulty_status": material.difficulty.status if material.difficulty else None,
        "display_order": material.display_order,
    }
    if teacher:
        payload.update(
            {
                "reference_answer": material.reference_answer,
                "reference_explanation": material.reference_explanation,
                "rubric_points": material.rubric_points,
                "reviewed_variant_tag": material.reviewed_variant_tag,
            }
        )
    return MaterialView.model_validate(payload)


def dump_material(view: MaterialView, *, teacher: bool) -> dict:
    """学生投影必须**删除**教师字段，而不是置空。"""

    if teacher:
        return view.model_dump(mode="json")
    return view.model_dump(mode="json", exclude=set(TEACHER_ONLY_FIELDS))


def _resolve_version(db: DbSession, scope, requested: str | None) -> str:
    if requested:
        row = db.get(models.Catalog, requested)
        if row is None:
            raise DomainError("NOT_FOUND", "课程目录版本不存在")
        if scope.dataset_id is not None and row.dataset_id != scope.dataset_id:
            raise DomainError("FORBIDDEN", "当前会话无权访问该课程目录")
        return requested
    if scope.dataset_id is None:
        raise DomainError("VALIDATION_ERROR", "请提供 catalog_version 或先选择数据集")
    version = latest_catalog_version(db, scope.dataset_id)
    if version is None:
        raise DomainError("NOT_FOUND", "该数据集尚无课程目录")
    return version


@router.get("/materials/{material_id}")
def get_material(
    material_id: str,
    db: DbSession,
    scope: Scope,
    catalog_version: str | None = Query(default=None, max_length=64),
    activity_id: str | None = Query(default=None, max_length=128),
    plan_version: str | None = Query(default=None, max_length=128),
) -> dict:
    version = _resolve_version(db, scope, catalog_version)
    row = db.get(models.Material, (version, material_id))
    if row is None:
        raise DomainError("NOT_FOUND", "材料不存在")
    material = Material.model_validate(dict(row.payload_json))
    teacher = scope.role == "teacher"
    if not teacher and activity_id:
        # 学生读取某个活动相关材料时，答案保护按活动/尝试状态决定；此处保守裁剪。
        teacher = False
    return success(dump_material(material_view(material, version, teacher=teacher), teacher=teacher))


@router.get("/datasets/{dataset_id}/materials/search")
def search_materials(
    dataset_id: str,
    db: DbSession,
    scope: Scope,
    catalog_version: str | None = Query(default=None, max_length=64),
    indicator_id: str | None = Query(default=None, max_length=64),
    kp_id: str | None = Query(default=None, max_length=64),
    question_type: str | None = Query(default=None, max_length=32),
    difficulty: str | None = Query(default=None, max_length=32),
    q: str | None = Query(default=None, max_length=120),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> dict:
    scope.require("view_curriculum")
    if scope.dataset_id is not None:
        scope.require_dataset(dataset_id)
    version = _resolve_version(db, scope, catalog_version or None)
    catalog = load_catalog(db, version, dataset_id)
    teacher = scope.role == "teacher"
    matches = []
    for material in catalog.materials:
        if indicator_id and indicator_id not in material.indicator_ids:
            continue
        if question_type and material.question_type != question_type:
            continue
        if difficulty:
            level = material.difficulty.level if material.difficulty else None
            if level != difficulty:
                continue
        if q and q not in material.content:
            continue
        matches.append(material)
    start = (page - 1) * page_size
    items = [
        dump_material(material_view(material, version, teacher=teacher), teacher=teacher)
        for material in matches[start : start + page_size]
    ]
    return success({"items": items, "total": len(matches), "page": page, "page_size": page_size})


@router.get("/catalog")
def get_catalog(
    db: DbSession,
    scope: Scope,
    catalog_version: str | None = Query(default=None, max_length=64),
) -> dict:
    version = _resolve_version(db, scope, catalog_version)
    catalog = load_catalog(db, version, scope.dataset_id)
    teacher = scope.role == "teacher"
    return success(
        {
            "catalog_version": version,
            "knowledge_version": None,
            "topics": [topic.model_dump(mode="json") for topic in catalog.topics],
            "indicators": [item.model_dump(mode="json") for item in catalog.indicators],
            # 学生不批量下发题库清单：按活动读取单条材料，服务端逐条裁剪答案。
            "materials": [
                dump_material(material_view(material, version, teacher=teacher), teacher=teacher)
                for material in catalog.materials
            ]
            if teacher
            else [],
            "links": [link.model_dump(mode="json") for link in catalog.links],
        }
    )
