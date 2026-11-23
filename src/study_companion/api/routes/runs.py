"""Run existing analysis rules and expose scoped results."""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Header, Query
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from study_companion.analysis.service import analyze_dataset
from study_companion.api.deps import DbSession, Scope
from study_companion.api.envelope import success
from study_companion.api.idempotency import (
    existing_result,
    fingerprint,
    remember_result,
)
from study_companion.contracts import AnalysisConfig, DomainError, Material, RunContext
from study_companion.curriculum.builder import parse_rubric_scores
from study_companion.db import models
from study_companion.library.datasets import load_catalog, load_prepared_dataset

router = APIRouter(tags=["runs"])
logger = logging.getLogger(__name__)


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: str
    catalog_version: str
    node_ids: list[str] = Field(min_length=1)
    rule_version: str = "rules-2"
    config: AnalysisConfig = Field(default_factory=AnalysisConfig)


def get_run(db, scope, run_id: str):
    run = db.get(models.AnalysisRun, run_id)
    if run is None:
        raise DomainError("NOT_FOUND", "分析运行不存在")
    scope.require_dataset(run.dataset_id)
    return run


@router.post("/runs", status_code=202)
def create_run(
    payload: RunRequest,
    db: DbSession,
    scope: Scope,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", max_length=128
    ),
) -> dict:
    scope.require("view_class_analysis")
    scope.require_dataset(payload.dataset_id)
    digest = fingerprint(payload.model_dump())
    previous = existing_result(db, "run-create", idempotency_key, digest)
    if previous is not None:
        return success(previous)
    data = load_prepared_dataset(db, payload.dataset_id)
    catalog = load_catalog(db, payload.catalog_version, payload.dataset_id)
    existing_nodes = {node.node_id for node in data.nodes}
    if not set(payload.node_ids).issubset(existing_nodes):
        raise DomainError("UNPROCESSABLE", "所选测验不属于此数据集")
    context = RunContext(
        run_id=f"run-{uuid.uuid4().hex}",
        dataset_id=payload.dataset_id,
        catalog_version=payload.catalog_version,
        rule_version=payload.rule_version,
    )
    try:
        bundle = analyze_dataset(data, catalog, context, payload.config)
        error = None
    except Exception:
        logger.exception("Analysis run failed")
        bundle = None
        error = {
            "code": "ANALYSIS_FAILED",
            "message": "分析失败，请检查数据或查看本地日志后重试",
        }
    status = "succeeded" if bundle is not None else "failed"
    db.add(
        models.AnalysisRun(
            id=context.run_id,
            dataset_id=context.dataset_id,
            catalog_version=context.catalog_version,
            rule_version=context.rule_version,
            config_json={**payload.config.model_dump(), "node_ids": payload.node_ids},
            status=status,
            created_at=datetime.now(UTC).isoformat(),
            error_json=error,
        )
    )
    db.flush()
    result_groups = (
        (("student", bundle.student_results), ("class", bundle.class_results))
        if bundle
        else ()
    )
    for kind, results in result_groups:
        for result in results:
            if result.node_id not in payload.node_ids:
                continue
            subject_id = result.student_id if kind == "student" else result.class_id
            db.add(
                models.AnalysisResult(
                    run_id=context.run_id,
                    subject_type=kind,
                    subject_id=subject_id,
                    node_id=result.node_id,
                    payload_json=result.model_dump(),
                )
            )
    db.flush()
    result = {
        "run_id": context.run_id,
        "dataset_id": context.dataset_id,
        "status": status,
        "error": error,
    }
    remember_result(db, "run-create", idempotency_key, digest, result)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        previous = existing_result(db, "run-create", idempotency_key, digest)
        if previous is None:
            raise
        return success(previous)
    return success(result)


@router.get("/runs/{run_id}")
def read_run(run_id: str, db: DbSession, scope: Scope) -> dict:
    run = get_run(db, scope, run_id)
    node_ids = run.config_json.get("node_ids")
    if node_ids is None:
        node_ids = list(
            db.scalars(
                select(models.AnalysisResult.node_id)
                .where(models.AnalysisResult.run_id == run_id)
                .distinct()
            ).all()
        )
    return success(
        {
            "run_id": run.id,
            "dataset_id": run.dataset_id,
            "catalog_version": run.catalog_version,
            "status": run.status,
            "created_at": run.created_at,
            "node_ids": node_ids,
            "error": run.error_json,
        }
    )


@router.get("/runs/{run_id}/students/{student_id}")
def student_result(
    run_id: str, student_id: str, db: DbSession, scope: Scope, node_id: str = Query(...)
) -> dict:
    get_run(db, scope, run_id)
    scope.require_student(student_id)
    scope.require(
        "view_own_analysis" if scope.role == "student" else "view_student_analysis"
    )
    row = db.get(models.AnalysisResult, (run_id, "student", student_id, node_id))
    if row is None:
        raise DomainError("NOT_FOUND", "学生结果不存在")
    return success(row.payload_json)


@router.get("/runs/{run_id}/classes/{class_id}")
def class_result(
    run_id: str, class_id: str, db: DbSession, scope: Scope, node_id: str = Query(...)
) -> dict:
    get_run(db, scope, run_id)
    scope.require("view_class_analysis")
    row = db.get(models.AnalysisResult, (run_id, "class", class_id, node_id))
    if row is None:
        raise DomainError("NOT_FOUND", "班级结果不存在")
    return success(row.payload_json)


@router.get("/runs/{run_id}/evidence/{kind}/{evidence_id}")
def read_evidence(
    run_id: str, kind: str, evidence_id: str, db: DbSession, scope: Scope
) -> dict:
    run = get_run(db, scope, run_id)
    if kind == "metric":
        query = select(models.AnalysisResult).where(
            models.AnalysisResult.run_id == run_id
        )
        if scope.role == "student":
            query = query.where(
                models.AnalysisResult.subject_type == "student",
                models.AnalysisResult.subject_id == scope.student_id,
            )
        for result in db.scalars(query):
            for metric in result.payload_json.get("metrics", []):
                if metric.get("metric_id") == evidence_id:
                    return success(metric)
        raise DomainError("NOT_FOUND", "统计依据不存在")
    if kind != "record":
        raise DomainError("VALIDATION_ERROR", "证据类型无效")
    rows = db.scalars(
        select(models.SourceRow).where(
            models.SourceRow.dataset_id == run.dataset_id,
            models.SourceRow.table_name == "records",
        )
    ).all()
    source = next(
        (row for row in rows if row.payload_json.get("记录编号") == evidence_id), None
    )
    if source is None:
        raise DomainError("NOT_FOUND", "作答记录不存在")
    raw = source.payload_json
    student_id = raw.get("学生编号") or raw.get("学号")
    if scope.role == "student":
        scope.require_student(student_id)
    else:
        scope.require("view_student_analysis")
    material = db.get(models.Material, (run.catalog_version, raw.get("材料编号")))
    event = db.scalar(
        select(models.AnswerEvent).where(
            models.AnswerEvent.dataset_id == run.dataset_id,
            models.AnswerEvent.selected_source_row_id == source.id,
        )
    )

    rubric = Material.model_validate(material.payload_json).rubric_points if material else []
    scores = (
        parse_rubric_scores(raw.get("评分点得分") or "", rubric)
        if scope.role == "teacher" else {}
    )

    def integer(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    return success(
        {
            "record_id": evidence_id,
            "student_id": student_id,
            "material_id": raw.get("材料编号"),
            "node_id": raw.get("节点编号"),
            "indicator_id": (material.payload_json.get("indicator_ids") or [None])[0]
            if material
            else None,
            "grading_status": raw.get("评分状态") or "待评分",
            "score": integer(raw.get("得分")),
            "max_score": integer(raw.get("满分")),
            "final_answer": raw.get("学生最终作答"),
            "solution_steps": (raw.get("解答过程") or "").splitlines() or None,
            "rubric_scores": [{"point_id": key, "score": value} for key, value in scores.items()] or None,
            "submitted_at": raw.get("模拟提交时间"),
            "disposition": "quarantined"
            if event and event.disposition == "quarantine_event"
            else "selected"
            if event
            else "history",
            "disposition_note": "评分点明细未能与当前题目评分规则对应"
            if scope.role == "teacher" and raw.get("评分点得分") and not scores
            else None,
        }
    )
