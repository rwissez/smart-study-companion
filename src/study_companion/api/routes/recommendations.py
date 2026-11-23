"""Generate and retrieve validated rule-based recommendations."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Header
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from study_companion.api.deps import DbSession, Scope
from study_companion.api.envelope import success
from study_companion.api.routes.catalog import material_summary
from study_companion.api.idempotency import (
    existing_result,
    fingerprint,
    remember_result,
)
from study_companion.api.routes.runs import get_run
from study_companion.analysis.prepare import parse_node_questions
from study_companion.contracts import (
    Activity,
    AdjustmentCondition,
    DomainError,
    RecommendationConfig,
    RecommendationBundle,
    RunContext,
    StudentResult,
    ValidationIssue,
)
from study_companion.db import models
from study_companion.library.datasets import load_catalog
from study_companion.recommendation.service import (
    recommend_rules,
    validate_recommendations,
)

router = APIRouter(tags=["recommendations"])
SUGGESTION_FORMAT = 3


class RecommendationRequest(BaseModel):
    node_id: str
    mode: str = "rule"


class RecommendationChanges(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    goal: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=4000)
    activities: list[Activity] = Field(min_length=1, max_length=20)
    adjustment_conditions: list[AdjustmentCondition] = Field(min_length=1, max_length=20)


class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    node_id: str
    recommendation_id: str
    base_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)
    changes: RecommendationChanges
    publish: bool = False


def update_payload(db: DbSession, row: models.RecommendationResult, payload: dict) -> None:
    updated = db.execute(
        update(models.RecommendationResult)
        .where(
            models.RecommendationResult.run_id == row.run_id,
            models.RecommendationResult.student_id == row.student_id,
            models.RecommendationResult.node_id == row.node_id,
            models.RecommendationResult.payload_json == row.payload_json,
        )
        .values(payload_json=payload)
    )
    if updated.rowcount != 1:
        raise DomainError("CONFLICT", "建议已更新，请重新打开后再保存")


def attempted_materials(db: DbSession, dataset_id: str, student_id: str, node_id: str) -> set[str]:
    rows = db.scalars(select(models.AnswerEvent).where(
        models.AnswerEvent.dataset_id == dataset_id,
        models.AnswerEvent.student_id == student_id,
        models.AnswerEvent.node_id == node_id,
    ))
    return {
        row.material_id for row in rows
        if row.payload_json.get("status") in {"graded", "pending"}
    }


def catalog_for_node(db: DbSession, run: models.AnalysisRun, node_id: str):
    catalog = load_catalog(db, run.catalog_version, run.dataset_id)
    node = db.get(models.Node, (run.dataset_id, node_id))
    allowed = node.payload_json.get("available_question_ids") if node else None
    if allowed is None:
        sources = db.scalars(select(models.SourceRow).where(
            models.SourceRow.dataset_id == run.dataset_id, models.SourceRow.table_name == "nodes",
        ))
        source = next((row.payload_json for row in sources if row.payload_json.get("节点编号") == node_id), {})
        allowed = parse_node_questions(source.get("可用题目编号"))
    if allowed is None:
        return catalog
    question_ids = set(allowed)
    return catalog.model_copy(update={"materials": [
        material for material in catalog.materials
        if material.material_type != "question" or material.material_id in question_ids
    ]})


@router.post("/runs/{run_id}/students/{student_id}/recommendations", status_code=202)
def create_recommendations(
    run_id: str,
    student_id: str,
    payload: RecommendationRequest,
    db: DbSession,
    scope: Scope,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", max_length=128
    ),
) -> dict:
    run = get_run(db, scope, run_id)
    scope.require("edit_recommendations")
    digest = fingerprint(
        {"run_id": run_id, "student_id": student_id, **payload.model_dump()}
    )
    previous = existing_result(db, "recommendation", idempotency_key, digest)
    if previous is not None:
        return success(previous)
    if payload.mode != "rule":
        raise DomainError("MODEL_NOT_CONFIGURED", "模型服务尚未配置")
    row = db.get(
        models.AnalysisResult, (run_id, "student", student_id, payload.node_id)
    )
    if row is None:
        raise DomainError("NOT_FOUND", "学生结果不存在")
    analysis = StudentResult.model_validate(row.payload_json)
    catalog = catalog_for_node(db, run, payload.node_id)
    context = RunContext(
        run_id=run.id,
        dataset_id=run.dataset_id,
        catalog_version=run.catalog_version,
        rule_version=run.rule_version,
    )
    attempted = attempted_materials(db, run.dataset_id, student_id, payload.node_id)
    bundle = recommend_rules(
        analysis, catalog, context, RecommendationConfig(), attempted_material_ids=attempted
    )
    validation = validate_recommendations(bundle, analysis, catalog)
    bundle.validation_issues.extend(validation.issues)
    materials = {material.material_id: material for material in catalog.materials}
    for rec in bundle.recommendations:
        sources = [
            materials[activity.material_id]
            for activity in rec.activities
            if activity.material_id in materials
        ]
        if sources and any(
            material.source_locator.verification_status == "unverified"
            for material in sources
        ):
            bundle.validation_issues.append(
                ValidationIssue(
                    code="SOURCE_UNVERIFIED",
                    message="建议所引用的材料尚未核对来源",
                    recommendation_id=rec.recommendation_id,
                )
            )
        if any(
            issue.recommendation_id == rec.recommendation_id
            or issue.recommendation_id is None
            for issue in bundle.validation_issues
        ):
            rec.review_status = "needs_review"
    key = (run_id, student_id, payload.node_id)
    existing = db.get(models.RecommendationResult, key)
    saved_payload = {**bundle.model_dump(), "suggestion_format": SUGGESTION_FORMAT}
    if existing is None:
        db.add(
            models.RecommendationResult(
                run_id=run_id,
                student_id=student_id,
                node_id=payload.node_id,
                payload_json=saved_payload,
            )
        )
    else:
        if existing.payload_json.get("revisions"):
            raise DomainError("CONFLICT", "已有教师修改或发布记录，请继续在现有建议上调整")
        update_payload(db, existing, saved_payload)
    job_id = f"job-{uuid.uuid4().hex}"
    db.add(
        models.Job(
            id=job_id,
            dataset_id=run.dataset_id,
            student_id=student_id,
            status="succeeded",
            result_json={
                "run_id": run_id,
                "student_id": student_id,
                "node_id": payload.node_id,
            },
            error_json=None,
        )
    )
    result = {"job_id": job_id, "status": "succeeded"}
    remember_result(db, "recommendation", idempotency_key, digest, result)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        previous = existing_result(db, "recommendation", idempotency_key, digest)
        if previous is None:
            raise
        return success(previous)
    return success(result)


@router.get("/runs/{run_id}/students/{student_id}/recommendations")
def read_recommendations(
    run_id: str, student_id: str, node_id: str, db: DbSession, scope: Scope
) -> dict:
    run = get_run(db, scope, run_id)
    scope.require_student(student_id)
    scope.require(
        "view_own_plan" if scope.role == "student" else "view_student_analysis"
    )
    row = db.get(models.RecommendationResult, (run_id, student_id, node_id))
    if row is None:
        raise DomainError("RESULT_NOT_READY", "建议尚未生成")
    payload = dict(row.payload_json)
    current_format = payload.pop("suggestion_format", None) == SUGGESTION_FORMAT
    if scope.role == "student":
        published = payload.pop("published", {})
        payload.pop("revisions", None)
        visible = [
            published.get(item["recommendation_id"], item)
            for item in payload["recommendations"]
        ]
        payload["recommendations"] = [item for item in visible if item["review_status"] == "validated"]
        payload["pending_review"] = len(payload["recommendations"]) < len(visible)
        payload["validation_issues"] = []
        material_ids = {
            activity["material_id"] for item in payload["recommendations"] for activity in item["activities"]
        }
        material_rows = db.scalars(select(models.Material).where(
            models.Material.catalog_version == run.catalog_version,
            models.Material.material_id.in_(material_ids),
        ))
        payload["materials"] = [material_summary(item.payload_json) for item in material_rows]
    else:
        payload.pop("published", None)
        payload["available_material_ids"] = [material.material_id for material in catalog_for_node(db, run, node_id).materials]
        if not current_format and not payload.get("revisions"):
            payload.update(recommendations=[], validation_issues=[], needs_regeneration=True)
    return success(payload)


@router.get("/runs/{run_id}/students/{student_id}/recommendation-revisions")
def read_revisions(run_id: str, student_id: str, node_id: str, db: DbSession, scope: Scope) -> dict:
    get_run(db, scope, run_id)
    scope.require("edit_recommendations")
    row = db.get(models.RecommendationResult, (run_id, student_id, node_id))
    return success(row.payload_json.get("revisions", {}) if row else {})


@router.post("/runs/{run_id}/students/{student_id}/recommendation-revisions")
def revise_recommendation(
    run_id: str, student_id: str, payload: RevisionRequest, db: DbSession, scope: Scope,
) -> dict:
    run = get_run(db, scope, run_id)
    scope.require("edit_recommendations")
    row = db.get(models.RecommendationResult, (run_id, student_id, payload.node_id))
    analysis_row = db.get(models.AnalysisResult, (run_id, "student", student_id, payload.node_id))
    if row is None or analysis_row is None:
        raise DomainError("NOT_FOUND", "当前范围没有可修改的建议")
    current = row.payload_json
    revisions = dict(current.get("revisions", {}))
    history = revisions.get(payload.recommendation_id, [])
    if len(history) + 1 != payload.base_revision:
        raise DomainError("CONFLICT", "建议已更新，请重新打开后再保存")
    bundle = RecommendationBundle.model_validate({
        key: value for key, value in current.items() if key not in {"revisions", "published", "suggestion_format"}
    })
    rec = next((item for item in bundle.recommendations if item.recommendation_id == payload.recommendation_id), None)
    if rec is None:
        raise DomainError("NOT_FOUND", "找不到这条建议")
    previous = rec.model_dump()
    changes = payload.changes
    rec.goal, rec.reason = changes.goal, changes.reason
    attempted = attempted_materials(db, run.dataset_id, student_id, payload.node_id)
    rec.activities = [
        activity.model_copy(update={"previously_attempted": activity.material_id in attempted})
        for activity in changes.activities
    ]
    rec.adjustment_conditions = changes.adjustment_conditions
    rec.review_status = "validated" if payload.publish else "needs_review"
    analysis = StudentResult.model_validate(analysis_row.payload_json)
    catalog = catalog_for_node(db, run, payload.node_id)
    issues = validate_recommendations(
        bundle.model_copy(update={"recommendations": [rec]}), analysis, catalog,
    ).issues
    if issues:
        raise DomainError("VALIDATION_ERROR", issues[0].message)
    bundle.validation_issues = [
        issue for issue in bundle.validation_issues
        if issue.recommendation_id != rec.recommendation_id
        or (issue.code == "SOURCE_UNVERIFIED" and not payload.publish)
    ]
    entry = {
        "revision": payload.base_revision + 1,
        "reason": payload.reason,
        "recommendation": previous,
        "created_at": datetime.now(UTC).isoformat(),
        "published": payload.publish,
    }
    revisions[rec.recommendation_id] = [*history, entry]
    published = dict(current.get("published", {}))
    if payload.publish:
        published[rec.recommendation_id] = rec.model_dump()
    elif previous["review_status"] == "validated":
        published.setdefault(rec.recommendation_id, previous)
    update_payload(db, row, {
        **bundle.model_dump(), "suggestion_format": SUGGESTION_FORMAT,
        "revisions": revisions, "published": published,
    })
    db.commit()
    return success(entry)


@router.get("/jobs/{job_id}")
def read_job(job_id: str, db: DbSession, scope: Scope) -> dict:
    row = db.get(models.Job, job_id)
    if row is None:
        raise DomainError("NOT_FOUND", "任务不存在")
    scope.require_dataset(row.dataset_id)
    if row.student_id:
        scope.require_student(row.student_id)
    return success(
        {
            "job_id": row.id,
            "status": row.status,
            "result": row.result_json,
            "error": row.error_json,
        }
    )
