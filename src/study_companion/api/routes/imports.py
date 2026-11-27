"""统一导入入口（01 §3.2）。

``POST /imports`` 接收 CSV / XLSX / PDF；分类不确定时返回待确认，不猜角色。
确认后用同一导入服务生成不可变快照并自动排入 A→B→C 任务。
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, File, Form, Header, Query, Response, UploadFile, status

from study_companion.api.deps import AppSettings, DbSession, Scope
from study_companion.api.envelope import success
from study_companion.api.idempotency import find_receipt, request_digest, store_receipt
from study_companion.api.uploads import read_upload
from study_companion.contracts import DomainError, ImportConfirmRequest, ImportRequest
from study_companion.library import imports as import_service

router = APIRouter(tags=["imports"])

ENDPOINT = "imports.create"


def _parse_metadata(raw: str | None) -> ImportRequest:
    if not raw:
        return ImportRequest(intent="auto_detect")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DomainError(
            "VALIDATION_ERROR",
            "metadata 必须是合法 JSON",
            [{"field": "metadata", "reason": "JSON 解析失败"}],
        ) from exc
    return ImportRequest.model_validate(payload)


@router.post("/imports", status_code=status.HTTP_202_ACCEPTED)
async def create_import(
    db: DbSession,
    scope: Scope,
    settings: AppSettings,
    response: Response,
    files: Annotated[list[UploadFile], File()],
    metadata: Annotated[str | None, Form()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=128)] = None,
) -> dict:
    scope.require("import_dataset")
    scope.require_teacher()
    request = _parse_metadata(metadata)
    payloads: list[tuple[str, bytes]] = []
    for upload in files:
        data = await read_upload(upload, settings.max_upload_bytes)
        payloads.append((upload.filename or "upload", data))
    if not payloads:
        raise DomainError("VALIDATION_ERROR", "请至少上传一个文件")

    token = (idempotency_key or "").strip() or None
    digest = request_digest(
        {
            "files": [{"name": name, "sha": __import__("hashlib").sha256(data).hexdigest()} for name, data in payloads],
            "metadata": request.model_dump(mode="json"),
        }
    )
    if token:
        cached = find_receipt(
            db, session_id=scope.session_id, endpoint=ENDPOINT, key=token, digest=digest
        )
        if cached:
            response.status_code = status.HTTP_200_OK
            return success(cached)

    receipt = import_service.create_import(
        db, settings, request=request, files=payloads, actor=scope.session_id
    )
    data = receipt.model_dump(mode="json")
    if token:
        store_receipt(
            db,
            session_id=scope.session_id,
            endpoint=ENDPOINT,
            key=token,
            digest=digest,
            receipt=data,
            result_ref=receipt.import_id,
        )
    if receipt.dataset_id:
        from study_companion.jobs.worker import ensure_worker_started

        ensure_worker_started()
    return success(data)


@router.get("/imports/{import_id}")
def get_import(import_id: str, db: DbSession, scope: Scope) -> dict:
    scope.require("import_dataset")
    scope.require_teacher()
    from study_companion.db import models

    record = db.get(models.ImportRecord, import_id)
    if record is None:
        raise DomainError("NOT_FOUND", "导入批次不存在")
    receipt = import_service._receipt_from_record(db, record)
    return success(receipt.model_dump(mode="json"))


@router.post("/imports/{import_id}/confirm", status_code=status.HTTP_202_ACCEPTED)
def confirm_import(
    import_id: str,
    payload: ImportConfirmRequest,
    db: DbSession,
    scope: Scope,
    settings: AppSettings,
    response: Response,
) -> dict:
    scope.require("import_dataset")
    scope.require_teacher()
    receipt = import_service.confirm_import(
        db,
        settings,
        import_id=import_id,
        mappings=[item.model_dump(mode="json") for item in payload.file_mappings],
        intent=payload.intent,
        dataset_family_id=payload.dataset_family_id,
        expected_parent=payload.expected_parent_dataset_id,
        actor=scope.session_id,
    )
    if receipt.dataset_id:
        from study_companion.jobs.worker import ensure_worker_started

        ensure_worker_started()
    return success(receipt.model_dump(mode="json"))
