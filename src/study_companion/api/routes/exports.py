"""导出接口：冻结请求、进度、下载与旧兼容读取。

* ``POST /runs/{run_id}/exports`` 只做冻结与入队，立即返回 202；
* ``GET /exports/{export_id}/download`` 流式读取已冻结的 ready 文件，不在 GET 里打包；
* 旧 ``GET /runs/{run_id}/exports/download`` 只查最近一份同范围 ready 包，不临时重算；
* ``published_only=false`` 明确拒绝：正式包永远只含正式成果。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, Query, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from study_companion.api.deps import AppSettings, DbSession, Scope
from study_companion.api.envelope import success
from study_companion.api.idempotency import find_receipt, request_digest, store_receipt
from study_companion.contracts import DomainError
from study_companion.db import models
from study_companion.exports import service

router = APIRouter(tags=["exports"])

ENDPOINT = "exports.create"


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: str = Field(default="zip", max_length=16)
    node_ids: list[str] | None = None
    case_student_ids: list[str] | None = None


def _authorized_dataset(db: DbSession, scope: Scope, run_id: str) -> models.Dataset:
    scope.require("export_run")
    scope.require_teacher()
    run = db.get(models.AnalysisRun, run_id)
    if run is None:
        raise DomainError("NOT_FOUND", "分析运行不存在")
    dataset = db.get(models.Dataset, run.dataset_id)
    if dataset is None:
        raise DomainError("NOT_FOUND", "数据集不存在")
    if scope.dataset_id is not None and scope.dataset_id != dataset.id:
        raise DomainError("FORBIDDEN", "当前会话无权导出该数据集")
    return dataset


@router.post("/runs/{run_id}/exports", status_code=status.HTTP_202_ACCEPTED)
def create_export(
    run_id: str,
    payload: ExportRequest,
    db: DbSession,
    scope: Scope,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=128)] = None,
    published_only: bool = Query(default=True),
) -> dict:
    if not published_only:
        raise DomainError("EXPORT_SCOPE_INVALID", "正式导出只允许已发布成果")
    if payload.format != "zip":
        raise DomainError("EXPORT_SCOPE_INVALID", "当前只支持 zip 格式")
    dataset = _authorized_dataset(db, scope, run_id)
    token = (idempotency_key or "").strip() or None
    digest = request_digest(
        {
            "run_id": run_id,
            "node_ids": payload.node_ids,
            "case_student_ids": payload.case_student_ids,
        }
    )
    if token:
        cached = find_receipt(
            db, session_id=scope.session_id, endpoint=ENDPOINT, key=token, digest=digest
        )
        if cached:
            return success(cached)
    export = service.freeze_export(
        db,
        run_id=run_id,
        dataset=dataset,
        node_ids=payload.node_ids,
        case_student_ids=payload.case_student_ids,
        actor=scope.session_id,
    )
    receipt = {
        "export_id": export.export_id,
        "snapshot_id": export.snapshot_id,
        "job_id": export.job_id,
        "status": export.status,
    }
    if token:
        store_receipt(
            db,
            session_id=scope.session_id,
            endpoint=ENDPOINT,
            key=token,
            digest=digest,
            receipt=receipt,
            result_ref=export.export_id,
        )
    from study_companion.jobs.worker import ensure_worker_started

    ensure_worker_started()
    return success(receipt)


@router.get("/runs/{run_id}/exports")
def list_exports(run_id: str, db: DbSession, scope: Scope) -> dict:
    _authorized_dataset(db, scope, run_id)
    rows = db.scalars(
        select(models.Export)
        .where(models.Export.run_id == run_id)
        .order_by(models.Export.created_at.desc())
    ).all()
    items = [service.export_view(row) for row in rows]
    return success({"items": items, "total": len(items)})


@router.get("/exports/{export_id}")
def get_export(export_id: str, db: DbSession, scope: Scope) -> dict:
    row = db.get(models.Export, export_id)
    if row is None:
        raise DomainError("NOT_FOUND", "导出记录不存在")
    _authorized_dataset(db, scope, row.run_id)
    return success(service.export_view(row))


@router.get("/exports/{export_id}/download")
def download_export(export_id: str, db: DbSession, scope: Scope, settings: AppSettings) -> Response:
    row = db.get(models.Export, export_id)
    if row is None:
        raise DomainError("NOT_FOUND", "导出记录不存在")
    _authorized_dataset(db, scope, row.run_id)
    path = service.download_path(row, Path(settings.output_root))
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"zhanzhi-export-{row.export_id}.zip",
    )


@router.get("/runs/{run_id}/exports/download")
def download_latest(run_id: str, db: DbSession, scope: Scope, settings: AppSettings) -> Response:
    _authorized_dataset(db, scope, run_id)
    row = db.scalar(
        select(models.Export)
        .where(models.Export.run_id == run_id, models.Export.status == "ready")
        .order_by(models.Export.created_at.desc())
        .limit(1)
    )
    if row is None:
        raise DomainError("RESULT_NOT_READY", "该运行尚无可下载的正式导出，请先提交导出请求")
    path = service.download_path(row, Path(settings.output_root))
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"zhanzhi-export-{row.export_id}.zip",
    )
