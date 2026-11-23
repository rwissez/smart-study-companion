"""Import datasets and read their rosters and quality reports."""

import hashlib
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Header, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from study_companion.analysis.prepare import prepare_dataset
from study_companion.api.deps import AppSettings, DbSession, Scope
from study_companion.api.envelope import success
from study_companion.api.uploads import raw_tables_from_uploads
from study_companion.contracts import DomainError, ImportConfig
from study_companion.curriculum.builder import build_base_catalog
from study_companion.curriculum.course_sources import read_course_review
from study_companion.db import models
from study_companion.library.datasets import save_dataset

router = APIRouter(tags=["datasets"])


@router.post("/datasets", status_code=201)
async def import_dataset(
    db: DbSession,
    scope: Scope,
    settings: AppSettings,
    requirements: Annotated[UploadFile, File()],
    materials: Annotated[UploadFile, File()],
    students: Annotated[UploadFile, File()],
    nodes: Annotated[UploadFile, File()],
    records: Annotated[UploadFile, File()],
    dictionary: Annotated[UploadFile, File()],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", max_length=128)
    ] = None,
) -> dict:
    scope.require("import_dataset")
    fingerprint = hashlib.sha256()
    for upload in (requirements, materials, students, nodes, records, dictionary):
        content = await upload.read()
        fingerprint.update(len(content).to_bytes(8, "big"))
        fingerprint.update(content)
        await upload.seek(0)
    digest = fingerprint.hexdigest()
    if idempotency_key:
        existing = db.get(models.IdempotencyRecord, ("dataset-upload", idempotency_key))
        if existing:
            if existing.fingerprint != digest:
                raise DomainError("CONFLICT", "同一个请求标识不能用于不同文件")
            return success(existing.result_json)
    tables = await raw_tables_from_uploads(
        requirements, materials, students, nodes, records, dictionary
    )
    try:
        prepared = prepare_dataset(tables, ImportConfig(rule_version="import-v1"))
    except DomainError as exc:
        if exc.code == "STRUCTURE_INVALID":
            raise DomainError("UNPROCESSABLE", exc.message, exc.details) from exc
        raise
    dataset_id = f"ds-{uuid.uuid4().hex}"
    prepared.source_manifest.dataset_id = dataset_id
    review = read_course_review(settings.data_root / "course-review.json")
    catalog = build_base_catalog(tables, f"catalog-{uuid.uuid4().hex}", review)
    save_dataset(db, tables, prepared, catalog)
    result = {
        "dataset_id": dataset_id,
        "catalog_version": catalog.version,
        "node_ids": [node.node_id for node in prepared.nodes],
        "status": "ready_with_issues" if prepared.quality_issues else "ready",
        "quality_summary": {"total": len(prepared.quality_issues)},
    }
    if idempotency_key:
        db.add(
            models.IdempotencyRecord(
                operation="dataset-upload",
                key=idempotency_key,
                fingerprint=digest,
                result_json=result,
            )
        )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = (
            db.get(models.IdempotencyRecord, ("dataset-upload", idempotency_key))
            if idempotency_key
            else None
        )
        if not existing:
            raise
        if existing.fingerprint != digest:
            raise DomainError("CONFLICT", "同一个请求标识不能用于不同文件")
        return success(existing.result_json)
    return success(result)


def _require_teacher(scope, dataset_id: str) -> None:
    if scope.role != "teacher":
        raise DomainError("FORBIDDEN", "只有教师可以查看数据集名单")
    if not scope.is_bootstrap:
        scope.require_dataset(dataset_id)


@router.get("/datasets/{dataset_id}/classes")
def list_classes(dataset_id: str, db: DbSession, scope: Scope) -> dict:
    _require_teacher(scope, dataset_id)
    if db.get(models.Dataset, dataset_id) is None:
        raise DomainError("NOT_FOUND", "数据集不存在")
    rows = db.execute(
        select(models.Student.class_id, func.count())
        .where(models.Student.dataset_id == dataset_id)
        .group_by(models.Student.class_id)
        .order_by(models.Student.class_id)
    ).all()
    return success(
        {
            "items": [
                {"class_id": class_id, "roster_count": count}
                for class_id, count in rows
            ]
        }
    )


@router.get("/datasets/{dataset_id}/students")
def list_students(
    dataset_id: str,
    db: DbSession,
    scope: Scope,
    class_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict:
    _require_teacher(scope, dataset_id)
    query = select(models.Student).where(models.Student.dataset_id == dataset_id)
    if class_id:
        query = query.where(models.Student.class_id == class_id)
    rows = db.scalars(query.order_by(models.Student.student_id)).all()
    counts: dict[str, int] = {}
    events = db.scalars(
        select(models.AnswerEvent).where(models.AnswerEvent.dataset_id == dataset_id)
    ).all()
    for event in events:
        if event.disposition == "include" and event.payload_json.get("status") in {
            "graded",
            "pending",
        }:
            counts[event.student_id] = counts.get(event.student_id, 0) + 1
    items = [
        {
            "student_id": row.student_id,
            "class_id": row.class_id,
            "grade": row.grade,
            "observed_event_count": counts.get(row.student_id, 0),
        }
        for row in rows[(page - 1) * page_size : page * page_size]
    ]
    return success(
        {"items": items, "total": len(rows), "page": page, "page_size": page_size}
    )


@router.get("/datasets/{dataset_id}/quality")
def list_quality(
    dataset_id: str,
    db: DbSession,
    scope: Scope,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict:
    scope.require("view_quality")
    scope.require_dataset(dataset_id)
    rows = db.scalars(
        select(models.QualityIssue)
        .where(models.QualityIssue.dataset_id == dataset_id)
        .order_by(models.QualityIssue.id)
    ).all()
    return success(
        {
            "items": [
                row.payload_json
                for row in rows[(page - 1) * page_size : page * page_size]
            ],
            "total": len(rows),
            "page": page,
            "page_size": page_size,
        }
    )
