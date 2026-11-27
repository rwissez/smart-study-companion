"""A 知识体系正式读取、审核与质量报告。

P1 只提供**诚实**的读取骨架：没有构建过正式知识版本时返回
``RESULT_NOT_READY``，绝不把目录投影冒充知识体系。P2 接入真实构建、审核与质量
报告后由同一路由返回正式成果。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from study_companion.api.deps import DbSession, Scope
from study_companion.api.envelope import success
from study_companion.contracts import DomainError, DomainError as _DomainError  # noqa: F401
from study_companion.db import models
from study_companion.db import version_repository as repo
from study_companion.knowledge import review_service

router = APIRouter(tags=["knowledge"])


class ReviewDecisionRequest(BaseModel):
    """01 §9.2 的教师审核请求；``changes`` 只在 edit 时使用且按对象类型收紧。"""

    model_config = ConfigDict(extra="forbid")

    base_candidate_version: str = Field(min_length=1, max_length=128)
    decision: Literal["approve", "reject", "edit"]
    changes: dict[str, Any] | None = None
    reason: str = Field(min_length=1, max_length=500)


def _dataset_family(db: DbSession, dataset_id: str) -> str:
    dataset = db.get(models.Dataset, dataset_id)
    if dataset is None:
        raise DomainError("NOT_FOUND", "数据集不存在")
    if not dataset.family_id:
        raise DomainError("UNPROCESSABLE", "数据集缺少系列身份")
    return dataset.family_id


@router.get("/datasets/{dataset_id}/knowledge")
def get_knowledge(
    dataset_id: str,
    db: DbSession,
    scope: Scope,
    knowledge_version: str | None = Query(default=None, max_length=128),
) -> dict:
    scope.require("view_curriculum")
    if scope.dataset_id is not None:
        scope.require_dataset(dataset_id)
    family_id = _dataset_family(db, dataset_id)

    if knowledge_version:
        version = db.get(models.Version, knowledge_version)
        if version is None or version.kind != "knowledge" or version.dataset_id != dataset_id:
            raise DomainError("KB_NOT_FOUND", "知识版本不存在")
    else:
        key = repo.FormalKey(family_id=family_id, dataset_id=dataset_id, kind="knowledge")
        version = repo.read_formal(db, key)
        if version is None:
            raise DomainError("RESULT_NOT_READY", "尚未构建正式知识体系，请先完成 A 构建")
    payload = version.payload_json or {}
    return success(
        {
            "dataset_id": dataset_id,
            "catalog_version": payload.get("catalog_version") or "",
            "knowledge_version": version.version_id,
            "parent_version": payload.get("parent_version"),
            "status": "published",
            "points": payload.get("points", []),
            "relations": payload.get("relations", []),
            "annotations": payload.get("annotations", []),
            "created_at": version.created_at,
            "review_summary": payload.get("review_summary", {}),
            "limitations": payload.get("limitations", []),
        }
    )


@router.get("/datasets/{dataset_id}/knowledge/reviews")
def list_knowledge_reviews(
    dataset_id: str,
    db: DbSession,
    scope: Scope,
    status: str | None = Query(default=None, max_length=32),
    object_type: str | None = Query(default=None, max_length=32),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> dict:
    scope.require("review_knowledge")
    if scope.dataset_id is not None:
        scope.require_dataset(dataset_id)
    query = select(models.Review).where(models.Review.subject_key.startswith(dataset_id))
    if status:
        query = query.where(models.Review.status == status)
    rows = db.scalars(query.order_by(models.Review.created_at.desc())).all()
    start = (page - 1) * page_size
    items = []
    for row in rows[start : start + page_size]:
        if object_type and row.subject_kind != object_type:
            continue
        items.append(
            {
                "review_id": row.review_id,
                "object_type": row.subject_kind,
                "object_id": row.subject_key,
                "candidate_version": "",
                "review_info": {
                    "status": row.status,
                    "method": row.method,
                    "checks": row.checks_json,
                    "issues": row.issues_json,
                },
                "summary": "",
                "source_locators": [],
            }
        )
    return success({"items": items, "total": len(rows), "page": page, "page_size": page_size})


@router.post("/datasets/{dataset_id}/knowledge/reviews/{review_id}")
def decide_knowledge_review(
    dataset_id: str,
    review_id: str,
    payload: ReviewDecisionRequest,
    db: DbSession,
    scope: Scope,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", max_length=128)
    ] = None,
) -> dict:
    """教师对单个待审对象做 approve/reject/edit（01 §9.2、02 §9）。

    * approve：生成新的不可变知识版本并经 CAS 切换正式指针；
    * reject：只记录决定，候选留在历史，正式指针不动；
    * edit：按对象白名单生成新候选并重新校验，不发布。
    """

    scope.require("review_knowledge")
    scope.require_teacher()
    if scope.dataset_id is not None:
        scope.require_dataset(dataset_id)
    dataset = db.get(models.Dataset, dataset_id)
    if dataset is None:
        raise DomainError("NOT_FOUND", "数据集不存在")
    review = db.get(models.Review, review_id)
    if review is None:
        raise DomainError("NOT_FOUND", "审核对象不存在")

    idempotency = review_service.idempotency_context(
        scope,
        review_service.ENDPOINT,
        idempotency_key,
        {
            "dataset_id": dataset_id,
            "review_id": review_id,
            "base_candidate_version": payload.base_candidate_version,
            "decision": payload.decision,
            "changes": payload.changes,
            "reason": payload.reason,
        },
    )
    result = review_service.decide(
        db,
        dataset=dataset,
        review=review,
        decision=payload.decision,
        changes=payload.changes,
        reason=payload.reason,
        actor=scope.session_id,
        idempotency=idempotency,
        base_candidate_version=payload.base_candidate_version,
    )
    return success(result)


@router.get("/datasets/{dataset_id}/knowledge/quality")
def get_knowledge_quality(
    dataset_id: str,
    db: DbSession,
    scope: Scope,
    knowledge_version: str | None = Query(default=None, max_length=128),
) -> dict:
    scope.require("review_knowledge")
    if scope.dataset_id is not None:
        scope.require_dataset(dataset_id)
    family_id = _dataset_family(db, dataset_id)
    key = repo.FormalKey(family_id=family_id, dataset_id=dataset_id, kind="knowledge")
    version = (
        db.get(models.Version, knowledge_version)
        if knowledge_version
        else repo.read_formal(db, key)
    )
    if version is None or version.payload_json is None:
        raise DomainError("RESULT_NOT_READY", "尚未构建正式知识体系，暂无质量报告")
    report = (version.payload_json or {}).get("review_summary") or {}
    if not report:
        raise DomainError("RESULT_NOT_READY", "该知识版本没有质量报告")
    return success(report)
