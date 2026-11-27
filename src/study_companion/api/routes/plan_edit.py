"""教师学习方案编辑：预览 → 确认 → 发布（C-30 teacher_direct），修订历史与恢复。

旧 ``publish=true`` 兼容字段只会被当作"请求完整审核"，绝不映射成教师直发。
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from study_companion.api.deps import DbSession, Scope
from study_companion.api.envelope import success
from study_companion.contracts import (
    DomainError,
    RevisionChanges,
    RevisionRequest,
    RevisionSummary,
    TeacherRevisionPreviewRequest,
)
from study_companion.db import models
from study_companion.recommendation import publication

router = APIRouter(tags=["plan-edit"])


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    warning_digest: str = Field(min_length=1, max_length=128)


class UndoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_plan_version: str
    restore_plan_version: str
    reason: str = Field(min_length=1, max_length=500)


def _plan_row(db: DbSession, run_id: str, student_id: str, node_id: str) -> models.Version:
    run = db.get(models.AnalysisRun, run_id)
    if run is None:
        raise DomainError("NOT_FOUND", "分析运行不存在")
    dataset = db.get(models.Dataset, run.dataset_id)
    if dataset is None or not dataset.family_id:
        raise DomainError("NOT_FOUND", "数据集不存在")
    from study_companion.db import version_repository as repo

    key = repo.FormalKey(
        family_id=dataset.family_id,
        dataset_id=dataset.id,
        kind="plan",
        node_key=node_id,
        target_key=student_id,
    )
    version = repo.read_formal(db, key)
    if version is None:
        raise DomainError("RESULT_NOT_READY", "该学生在此节点尚无正式学习方案")
    return version


def _dataset_for_student(db: DbSession, scope: Scope, dataset_id: str, student_id: str) -> models.Dataset:
    dataset = db.get(models.Dataset, dataset_id)
    if dataset is None:
        raise DomainError("NOT_FOUND", "数据集不存在")
    if scope.dataset_id is not None and scope.dataset_id != dataset_id:
        raise DomainError("FORBIDDEN", "当前会话无权访问该数据集")
    return dataset


@router.post("/runs/{run_id}/students/{student_id}/recommendation-revisions/preview")
def preview_revision(
    run_id: str,
    student_id: str,
    payload: TeacherRevisionPreviewRequest,
    db: DbSession,
    scope: Scope,
) -> dict:
    scope.require("edit_recommendations")
    scope.require_teacher()
    run = db.get(models.AnalysisRun, run_id)
    if run is None:
        raise DomainError("NOT_FOUND", "分析运行不存在")
    dataset = _dataset_for_student(db, scope, run.dataset_id, student_id)
    base_version = _plan_row(db, run_id, student_id, payload.node_id)
    base_plan_version = payload.base_plan_version or base_version.version_id
    preview = publication.preview_teacher_revision(
        db,
        dataset=dataset,
        student_id=student_id,
        node_id=payload.node_id,
        base_plan_version=base_plan_version,
        changes=payload.changes,
        reason=payload.reason,
        actor=scope.session_id,
    )
    return success(
        {
            "preview_id": preview.preview_id,
            "draft_hash": preview.draft_hash,
            "warning_digest": preview.warning_digest,
            "warnings": [item.model_dump(mode="json") for item in preview.warnings],
            "expires_at": preview.expires_at,
        }
    )


@router.post("/runs/{run_id}/students/{student_id}/recommendation-revisions/{preview_id}/publish")
def publish_revision(
    run_id: str,
    student_id: str,
    preview_id: str,
    payload: PublishRequest,
    db: DbSession,
    scope: Scope,
    node_id: str = Query(min_length=1, max_length=64),
) -> dict:
    scope.require("edit_recommendations")
    scope.require_teacher()
    run = db.get(models.AnalysisRun, run_id)
    if run is None:
        raise DomainError("NOT_FOUND", "分析运行不存在")
    dataset = _dataset_for_student(db, scope, run.dataset_id, student_id)
    result = publication.publish_teacher_revision(
        db,
        dataset=dataset,
        student_id=student_id,
        node_id=node_id,
        preview_id=preview_id,
        warning_digest=payload.warning_digest,
        actor=scope.session_id,
    )
    return success(result)


@router.get("/runs/{run_id}/students/{student_id}/recommendation-revisions")
def list_revisions(
    run_id: str,
    student_id: str,
    db: DbSession,
    scope: Scope,
    node_id: str = Query(min_length=1, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> dict:
    scope.require("view_student_analysis")
    run = db.get(models.AnalysisRun, run_id)
    if run is None:
        raise DomainError("NOT_FOUND", "分析运行不存在")
    scope_key = publication.scope_key_for(run.dataset_id, student_id, node_id)
    rows = db.scalars(
        select(models.PlanRevision)
        .where(models.PlanRevision.scope_key == scope_key)
        .order_by(models.PlanRevision.created_at.desc())
    ).all()
    start = (page - 1) * page_size
    items = []
    for index, row in enumerate(rows[start : start + page_size], start=1):
        version = db.get(models.Version, row.result_version) if row.result_version else None
        review_status = "draft"
        publication_status = "unpublished"
        if version is not None:
            payload = version.payload_json or {}
            review_status = payload.get("review_status", "draft")
            publication_status = payload.get("publication_status", "unpublished")
        items.append(
            RevisionSummary(
                revision_id=row.revision_id,
                revision=max(len(rows) - (start + index - 1), 1),
                plan_version=row.result_version or "",
                parent_plan_version=row.base_plan_version,
                reason=row.reason,
                created_at=row.created_at,
                review_status=review_status,  # type: ignore[arg-type]
                publication_status=publication_status,  # type: ignore[arg-type]
                author_role="teacher",
            ).model_dump(mode="json")
        )
    return success({"items": items, "total": len(rows), "page": page, "page_size": page_size})


@router.post("/runs/{run_id}/students/{student_id}/recommendation-revisions", status_code=202)
def submit_revision_for_review(
    run_id: str,
    student_id: str,
    payload: RevisionRequest,
    db: DbSession,
    scope: Scope,
) -> dict:
    """旧兼容入口：``publish=true`` 只表示"请求完整审核"，不会直发。"""

    scope.require("edit_recommendations")
    scope.require_teacher()
    run = db.get(models.AnalysisRun, run_id)
    if run is None:
        raise DomainError("NOT_FOUND", "分析运行不存在")
    dataset = _dataset_for_student(db, scope, run.dataset_id, student_id)
    base_version = _plan_row(db, run_id, student_id, payload.node_id)
    preview = publication.preview_teacher_revision(
        db,
        dataset=dataset,
        student_id=student_id,
        node_id=payload.node_id,
        base_plan_version=payload.base_plan_version or base_version.version_id,
        changes=payload.changes,
        reason=payload.reason,
        actor=scope.session_id,
    )
    return success(
        {
            "revision_id": preview.preview_id,
            "review_status": "draft",
            "publication_status": "unpublished",
            "job_id": None,
            "preview_id": preview.preview_id,
            "warning_digest": preview.warning_digest,
            "warnings": [item.model_dump(mode="json") for item in preview.warnings],
            "note": "已生成教师编辑预览；旧 publish 字段不构成发布授权，仍需确认接口。",
        }
    )


@router.get("/datasets/{dataset_id}/plan-reviews")
def list_plan_reviews(
    dataset_id: str,
    db: DbSession,
    scope: Scope,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> dict:
    scope.require("view_class_analysis")
    _dataset_for_student(db, scope, dataset_id, "")
    rows = db.scalars(
        select(models.Proposal)
        .where(models.Proposal.origin == "agent", models.Proposal.state == "ready")
        .order_by(models.Proposal.created_at.desc())
    ).all()
    start = (page - 1) * page_size
    items = [
        {
            "review_id": row.proposal_id,
            "student_id": (row.snapshot_json or {}).get("student_id", ""),
            "plan_version": row.base_plan_version or "",
            "review_info": {"status": "needs_review", "method": "model"},
            "summary": "Agent 候选修改等待教师确认",
        }
        for row in rows[start : start + page_size]
    ]
    return success({"items": items, "total": len(rows), "page": page, "page_size": page_size})


@router.post("/datasets/{dataset_id}/students/{student_id}/plan/undo", status_code=202)
def undo_plan(
    dataset_id: str,
    student_id: str,
    payload: UndoRequest,
    db: DbSession,
    scope: Scope,
) -> dict:
    """恢复历史方案：新建修订指向历史版本，历史与已完成活动都不删除。"""

    scope.require("edit_recommendations")
    scope.require_teacher()
    dataset = _dataset_for_student(db, scope, dataset_id, student_id)
    restore = db.get(models.Version, payload.restore_plan_version)
    if restore is None or restore.kind != "plan" or restore.target_key != student_id:
        raise DomainError("NOT_FOUND", "待恢复的方案版本不存在")
    from study_companion.db import version_repository as repo

    key = repo.FormalKey(
        family_id=dataset.family_id or "",
        dataset_id=dataset_id,
        kind="plan",
        node_key=restore.node_key,
        target_key=student_id,
    )
    head = repo.read_head(db, key)
    if head is None or head.version_id != payload.base_plan_version:
        raise DomainError("REVISION_CONFLICT", "当前方案已被更新，请重新读取后再恢复")
    revision = models.PlanRevision(
        revision_id=repo.new_id("rev"),
        proposal_id=None,
        proposal_revision=None,
        scope_key=publication.scope_key_for(dataset_id, student_id, restore.node_key),
        base_plan_version=payload.base_plan_version,
        restore_version=payload.restore_plan_version,
        result_version=None,
        reason=payload.reason,
        origin="manual_undo",
        job_id=None,
        created_at=repo.now_iso(),
    )
    db.add(revision)
    db.flush()
    return success(
        {
            "revision_id": revision.revision_id,
            "status": "queued",
            "note": "恢复请求已记录；正式切换仍走教师预览/确认接口。",
        }
    )


@router.get("/runs/{run_id}/quality/recommendations")
def c_quality_report(
    run_id: str,
    db: DbSession,
    scope: Scope,
    node_id: str = Query(min_length=1, max_length=64),
    date_from: str | None = Query(default=None, max_length=32),
    date_to: str | None = Query(default=None, max_length=32),
    class_id: str | None = Query(default=None, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> dict:
    """C 的质量报告：只统计已发生的发布、审核、顺延与介入事实。"""

    scope.require("view_quality")
    run = db.get(models.AnalysisRun, run_id)
    if run is None:
        raise DomainError("NOT_FOUND", "分析运行不存在")
    dataset = _dataset_for_student(db, scope, run.dataset_id, "")
    if class_id is not None and not scope.can_read_class(class_id):
        raise DomainError("FORBIDDEN", "当前会话未获授权该班级")
    from study_companion.recommendation.quality import QualityWindow, build_report

    try:
        window = QualityWindow.from_dates(date_from, date_to)
    except ValueError as exc:
        raise DomainError("VALIDATION_ERROR", str(exc)) from exc
    report = build_report(
        db, dataset_id=dataset.id, node_id=node_id, window=window, class_id=class_id
    )
    issues = report.issues or {}
    items = list(issues.get("items", []))
    start = (page - 1) * page_size
    report.issues = {
        "items": items[start : start + page_size],
        "total": len(items),
        "page": page,
        "page_size": page_size,
    }
    return success(report.model_dump(mode="json"))
