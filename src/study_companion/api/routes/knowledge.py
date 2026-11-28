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
from study_companion.knowledge.change_set import diff_payloads, impact_of

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
        context: dict = {}
        for check in row.checks_json or []:
            if check.get("code") == "REVIEW_CONTEXT":
                context = check.get("context") or {}
                break
        items.append(
            {
                "review_id": row.review_id,
                "object_type": row.subject_kind,
                "object_id": str(row.subject_key).split(":", 1)[-1],
                "candidate_version": context.get("candidate_version", ""),
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


@router.get("/datasets/{dataset_id}/knowledge/change-set")
def get_change_set(
    dataset_id: str,
    db: DbSession,
    scope: Scope,
    from_version: str = Query(default="", max_length=128),
    to_version: str = Query(default="", max_length=128),
) -> dict:
    """比较两版知识体系：新增/删除/改名/改内容，以及受影响的指标、题目与方案。"""

    scope.require("view_curriculum")
    family_id = _dataset_family(db, dataset_id)
    head = repo.read_formal(
        db, repo.FormalKey(family_id=family_id, dataset_id=dataset_id, kind="knowledge")
    )
    target_id = to_version or (head.version_id if head else "")
    if not target_id:
        raise DomainError("RESULT_NOT_READY", "尚未发布知识体系，无法比较")
    target = db.get(models.Version, target_id)
    if target is None or target.dataset_id != dataset_id or target.kind != "knowledge":
        raise DomainError("NOT_FOUND", "目标知识版本不存在")
    if from_version:
        source = db.get(models.Version, from_version)
        if source is None or source.dataset_id != dataset_id or source.kind != "knowledge":
            raise DomainError("NOT_FOUND", "起始知识版本不存在")
    else:
        source = (
            db.get(models.Version, target.parent_version)
            if getattr(target, "parent_version", None)
            else None
        )
        if source is None:
            raise DomainError("VALIDATION_ERROR", "请提供 from_version，或目标版本带有父版本")
    previous = dict(source.payload_json or {})
    current = dict(target.payload_json or {})
    changes = diff_payloads(previous, current)
    impact = impact_of(changes, current)
    affected = [
        row.version_id
        for row in db.scalars(
            select(models.Version).where(
                models.Version.dataset_id == dataset_id, models.Version.kind == "plan"
            )
        ).all()
        if (row.payload_json or {}).get("knowledge_version") == source.version_id
    ]
    return success(
        {
            "dataset_id": dataset_id,
            "from_version": source.version_id,
            "to_version": target.version_id,
            "changes": [
                {
                    "object_type": item.object_type,
                    "object_id": item.object_id,
                    "kind": item.kind,
                    "previous_id": item.previous_id,
                    "fields": item.fields,
                    "name": item.name,
                    "detail": item.detail,
                }
                for item in changes
            ],
            "counts": {
                "added": sum(1 for item in changes if item.kind == "added"),
                "removed": sum(1 for item in changes if item.kind == "removed"),
                "renamed": sum(1 for item in changes if item.kind == "renamed"),
                "changed": sum(1 for item in changes if item.kind == "changed"),
            },
            "impact": impact,
            "affected_plan_versions": sorted(set(affected)),
            "limitations": ["只比较正式知识版本的对象集合，不评价语义正确性"],
        }
    )

@router.post("/datasets/{dataset_id}/knowledge/rebuild", status_code=202)
def rebuild_knowledge(
    dataset_id: str,
    db: DbSession,
    scope: Scope,
    catalog_version: str | None = Query(default=None, max_length=128),
    reason: str = Query(default="", max_length=200),
) -> dict:
    """重建知识体系：重新构建知识点与模型关系，通过校验才发布。"""

    scope.require("review_knowledge")
    scope.require_teacher()
    _dataset_family(db, dataset_id)
    from study_companion.jobs.service import enqueue
    from study_companion.jobs.worker import ensure_worker_started

    job = enqueue(
        db,
        kind="knowledge_rebuild",
        scope={
            "dataset_id": dataset_id,
            "catalog_version": catalog_version or "",
            "reason": reason,
        },
        payload={"reason": reason},
        dataset_id=dataset_id,
        requested_by=scope.session_id,
    )
    db.commit()
    ensure_worker_started()
    return success({"job_id": job.id, "status": job.status})

