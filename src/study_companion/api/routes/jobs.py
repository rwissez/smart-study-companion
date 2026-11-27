"""任务读取、取消与一次教师手动重试的 HTTP 入口。

任务只能由授权范围相同的会话查询：教师按数据集/班级授权，学生只看本人
相关任务（如评分、受助事件），越权一律 403 且不暴露对象存在性。
"""

from __future__ import annotations

import hashlib
from typing import Annotated

from fastapi import APIRouter, Header, Query
from sqlalchemy import select

from study_companion.api.deps import DbSession, Scope
from study_companion.api.envelope import success
from study_companion.api.idempotency import find_receipt, request_digest, store_receipt
from study_companion.contracts import DomainError
from study_companion.db import models
from study_companion.jobs import service

router = APIRouter(tags=["jobs"])

ENDPOINT_RETRY = "jobs.retry"


def load_job(db: DbSession, scope, job_id: str) -> models.Job:
    job = db.get(models.Job, job_id)
    if job is None:
        raise DomainError("NOT_FOUND", "任务不存在")
    job_scope = job.scope_json or {}
    if scope.role == "student":
        if job.student_id and job.student_id != scope.student_id:
            raise DomainError("FORBIDDEN", "当前会话无权访问该任务")
        if job.student_id is None:
            raise DomainError("FORBIDDEN", "当前会话无权访问该任务")
    else:
        dataset_id = job_scope.get("dataset_id") or job.dataset_id
        if scope.dataset_id is not None and dataset_id and dataset_id != scope.dataset_id:
            raise DomainError("FORBIDDEN", "当前会话无权访问该任务")
        class_id = job_scope.get("class_id")
        if class_id is not None and not scope.can_read_class(class_id):
            raise DomainError("FORBIDDEN", "当前会话无权访问该任务")
    return job


@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: DbSession, scope: Scope) -> dict:
    job = load_job(db, scope, job_id)
    return success(service.to_detail(db, job).model_dump())


@router.get("/jobs/{job_id}/events")
def get_job_events(
    job_id: str,
    db: DbSession,
    scope: Scope,
    after_sequence: int = Query(default=0, ge=0),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict:
    job = load_job(db, scope, job_id)
    return success(service.events_page(db, job, after=after_sequence, page_size=page_size))


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, db: DbSession, scope: Scope) -> dict:
    scope.require("manage_jobs")
    job = load_job(db, scope, job_id)
    service.request_cancel(db, job)
    db.flush()
    return success(service.to_detail(db, job).model_dump())


@router.post("/jobs/{job_id}/retry", status_code=202)
def retry_job(
    job_id: str,
    db: DbSession,
    scope: Scope,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=128)] = None,
) -> dict:
    """授权教师对**最终失败且可重试**的任务执行一次手动重试。

    原任务保持 failed 与原始原因；子任务带 ``retry_of_job_id`` / ``root_job_id``。
    同一 Idempotency-Key 返回同一子任务，不重复创建。
    """

    scope.require_teacher()
    scope.require("manage_jobs")
    job = load_job(db, scope, job_id)
    token = (idempotency_key or "").strip() or None
    digest = request_digest({"job_id": job_id})
    if token:
        cached = find_receipt(
            db, session_id=scope.session_id, endpoint=ENDPOINT_RETRY, key=token, digest=digest
        )
        if cached:
            return success(cached)
    child = service.manual_retry(db, job, actor=scope.session_id)
    db.flush()
    receipt = {
        "job_id": child.id,
        "retry_of_job_id": job.id,
        "root_job_id": job.root_job_id or job.id,
        "status": child.status,
    }
    if token:
        store_receipt(
            db,
            session_id=scope.session_id,
            endpoint=ENDPOINT_RETRY,
            key=token,
            digest=digest,
            receipt=receipt,
            result_ref=child.id,
        )
    from study_companion.jobs.worker import ensure_worker_started

    ensure_worker_started()
    return success(receipt)


@router.get("/jobs")
def list_jobs(
    db: DbSession,
    scope: Scope,
    dataset_id: str | None = Query(default=None, max_length=64),
    job_kind: str | None = Query(default=None, alias="kind", max_length=32),
    status_filter: str | None = Query(default=None, alias="status", max_length=32),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> dict:
    scope.require_teacher()
    target_dataset = dataset_id or scope.dataset_id
    if scope.dataset_id is not None and target_dataset and target_dataset != scope.dataset_id:
        raise DomainError("FORBIDDEN", "当前会话无权访问该数据集的任务")
    query = select(models.Job)
    if target_dataset:
        query = query.where(models.Job.dataset_id == target_dataset)
    if job_kind:
        query = query.where(models.Job.kind == job_kind)
    if status_filter:
        query = query.where(models.Job.status == status_filter)
    rows = db.scalars(query.order_by(models.Job.created_at.desc())).all()
    start = (page - 1) * page_size
    items = [service.to_detail(db, row).model_dump() for row in rows[start : start + page_size]]
    return success(
        {"items": items, "total": len(rows), "page": page, "page_size": page_size}
    )
