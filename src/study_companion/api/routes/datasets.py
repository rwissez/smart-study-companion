"""数据集读取与旧六 CSV 兼容导入。

旧 ``POST /datasets`` 保留签名与返回字段，但内部走同一个导入服务：原始文件、
身份、系列与质量检查都不会被绕过。旧调用者可以只上传六张 CSV；缺少正式 A
时明确报告"仅基础目录"，不把未构建的知识体系标为完成。
"""

from __future__ import annotations

import hashlib
from typing import Annotated

from fastapi import APIRouter, File, Header, Query, Response, UploadFile, status
from sqlalchemy import func, select

from study_companion.api.deps import AppSettings, DbSession, Scope
from study_companion.api.envelope import success
from study_companion.api.idempotency import find_receipt, request_digest, store_receipt
from study_companion.api.uploads import read_upload
from study_companion.contracts import DomainError, ImportRequest
from study_companion.db import models
from study_companion.library import datasets as dataset_library
from study_companion.library import imports as import_service
from study_companion.library.datasets import dataset_summary

router = APIRouter(tags=["datasets"])

ENDPOINT = "datasets.legacy_upload"
LEGACY_FIELDS = (
    "requirements",
    "materials",
    "students",
    "nodes",
    "records",
    "dictionary",
)


@router.post("/datasets", status_code=status.HTTP_201_CREATED)
async def import_dataset_legacy(
    db: DbSession,
    scope: Scope,
    settings: AppSettings,
    response: Response,
    requirements: Annotated[UploadFile, File()],
    materials: Annotated[UploadFile, File()],
    students: Annotated[UploadFile, File()],
    nodes: Annotated[UploadFile, File()],
    records: Annotated[UploadFile, File()],
    dictionary: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=128)] = None,
) -> dict:
    scope.require("import_dataset")
    scope.require_teacher()
    uploads = {
        "requirements": requirements,
        "materials": materials,
        "students": students,
        "nodes": nodes,
        "records": records,
        "dictionary": dictionary,
    }
    payloads: list[tuple[str, bytes]] = []
    for name, upload in uploads.items():
        data = await read_upload(upload, settings.max_upload_bytes)
        payloads.append((name, data))

    token = (idempotency_key or "").strip() or None
    digest = request_digest(
        {"files": [[name, hashlib.sha256(data).hexdigest()] for name, data in payloads]}
    )
    if token:
        cached = find_receipt(
            db, session_id=scope.session_id, endpoint=ENDPOINT, key=token, digest=digest
        )
        if cached:
            response.status_code = status.HTTP_200_OK
            return success(cached)

    receipt = import_service.create_import(
        db,
        settings,
        request=ImportRequest(intent="new_family"),
        files=payloads,
        actor=scope.session_id,
    )
    if not receipt.dataset_id:
        raise DomainError(
            "UNPROCESSABLE",
            "缺少必要数据表，无法建立快照",
            [{"field": item, "reason": "未提供或未识别"} for item in receipt.missing_inputs],
        )
    dataset = db.get(models.Dataset, receipt.dataset_id)
    node_ids: list[str] = []
    catalog_version = None
    if dataset is not None:
        summary = dataset_summary(db, dataset)
        node_ids = summary.node_ids
        catalog_version = summary.catalog_version
    result = {
        "dataset_id": receipt.dataset_id,
        "dataset_family_id": receipt.dataset_family_id,
        "catalog_version": catalog_version,
        "node_ids": node_ids,
        "status": "ready_with_issues" if receipt.quality_summary else "ready",
        "quality_summary": receipt.quality_summary,
        "job_id": receipt.job_id,
        "knowledge_built": False,
    }
    if token:
        store_receipt(
            db,
            session_id=scope.session_id,
            endpoint=ENDPOINT,
            key=token,
            digest=digest,
            receipt=result,
            result_ref=receipt.dataset_id,
        )
    if receipt.job_id:
        from study_companion.jobs.worker import ensure_worker_started

        ensure_worker_started()
    return success(result)


@router.get("/datasets/{dataset_id}/classes")
def list_classes(dataset_id: str, db: DbSession, scope: Scope) -> dict:
    scope.require_teacher()
    if scope.dataset_id is not None:
        scope.require_dataset(dataset_id)
    if db.get(models.Dataset, dataset_id) is None:
        raise DomainError("NOT_FOUND", "数据集不存在")
    rows = db.execute(
        select(models.Student.class_id, func.count())
        .where(models.Student.dataset_id == dataset_id)
        .group_by(models.Student.class_id)
        .order_by(models.Student.class_id)
    ).all()
    items = []
    for class_id, count in rows:
        if not scope.can_read_class(class_id):
            continue
        items.append({"class_id": class_id, "display_name": class_id, "roster_count": count})
    return success({"items": items, "total": len(items)})


@router.get("/datasets/{dataset_id}/students")
def list_students(
    dataset_id: str,
    db: DbSession,
    scope: Scope,
    class_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict:
    scope.require_teacher()
    if scope.dataset_id is not None:
        scope.require_dataset(dataset_id)
    if class_id is not None:
        scope.require_class(class_id)
    query = select(models.Student).where(models.Student.dataset_id == dataset_id)
    if class_id:
        query = query.where(models.Student.class_id == class_id)
    rows = db.scalars(query.order_by(models.Student.student_id)).all()
    visible = [row for row in rows if scope.can_read_class(row.class_id)]
    counts: dict[str, dict[str, int]] = {}
    events = db.scalars(
        select(models.AnswerEvent).where(models.AnswerEvent.dataset_id == dataset_id)
    ).all()
    for event in events:
        bucket = counts.setdefault(event.student_id, {"observed": 0, "graded": 0})
        bucket["observed"] += 1
        if event.disposition == "include" and (event.payload_json or {}).get("status") == "graded":
            bucket["graded"] += 1
    start = (page - 1) * page_size
    items = [
        {
            "student_id": row.student_id,
            "class_id": row.class_id,
            "grade": row.grade,
            "observed_event_count": counts.get(row.student_id, {}).get("observed", 0),
            "graded_event_count": counts.get(row.student_id, {}).get("graded", 0),
        }
        for row in visible[start : start + page_size]
    ]
    return success({"items": items, "total": len(visible), "page": page, "page_size": page_size})


@router.get("/datasets/{dataset_id}/quality")
def list_quality(
    dataset_id: str,
    db: DbSession,
    scope: Scope,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict:
    scope.require("view_quality")
    if scope.dataset_id is not None:
        scope.require_dataset(dataset_id)
    rows = db.scalars(
        select(models.QualityIssue)
        .where(models.QualityIssue.dataset_id == dataset_id)
        .order_by(models.QualityIssue.id)
    ).all()
    start = (page - 1) * page_size
    items = [row.payload_json for row in rows[start : start + page_size]]
    return success({"items": items, "total": len(rows), "page": page, "page_size": page_size})


@router.get("/datasets/{dataset_id}")
def get_dataset(dataset_id: str, db: DbSession, scope: Scope) -> dict:
    scope.require("list_datasets")
    if scope.dataset_id is not None:
        scope.require_dataset(dataset_id)
    row = db.get(models.Dataset, dataset_id)
    if row is None:
        raise DomainError("NOT_FOUND", "数据集不存在")
    return success(dataset_summary(db, row).model_dump(mode="json"))
