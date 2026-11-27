"""统一导入服务（01 §3.2）。

职责：
* 接收 CSV / XLSX（六张逻辑表）与 PDF（课标/教材）;
* 原始字节按内容寻址落盘，记录 SHA-256 与来源角色;
* 按表头/工作表识别角色，不确定的**等教师确认**，不猜;
* 只有六张逻辑表齐全且已确认时才生成不可变数据集快照;
* 同系列同逻辑内容重复提交返回已有快照，不重复计算。

不负责：正式知识体系、学情分析与方案生成（分别由 A/B/C 与任务调度负责）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.analysis.prepare import prepare_dataset
from study_companion.api.uploads import (
    TABLE_NAMES,
    detect_role,
    parse_csv,
    parse_xlsx,
    sniff_format,
)
from study_companion.config import Settings
from study_companion.contracts import (
    DomainError,
    ImportConfig,
    ImportFile,
    ImportReceipt,
    ImportRequest,
    RawRow,
    RawTables,
)
from study_companion.curriculum.builder import build_base_catalog
from study_companion.curriculum.course_sources import read_course_review
from study_companion.db import models
from study_companion.library import blobs
from study_companion.library.datasets import (
    ensure_family,
    new_id,
    now_iso,
    save_dataset,
)

TABLE_ROLES = TABLE_NAMES
PDF_ROLES = ("standard", "textbook")


@dataclass
class StagedSheet:
    logical_slot: str
    sheet_name: str | None
    role: str
    rows: list[RawRow]
    headers: list[str] = field(default_factory=list)


@dataclass
class StagedFile:
    file_id: str
    original_name: str
    storage_key: str
    sha256: str
    byte_size: int
    format: str
    sheets: list[StagedSheet] = field(default_factory=list)
    pdf_role: str | None = None
    status: str = "accepted"
    reason: str | None = None

    def receipt(self) -> ImportFile:
        primary = self.sheets[0] if self.sheets else None
        return ImportFile(
            file_id=self.file_id,
            original_name=self.original_name,
            sha256=self.sha256,
            byte_size=self.byte_size,
            format=self.format,  # type: ignore[arg-type]
            role=(primary.role if primary else (self.pdf_role or ("standard" if self.format == "pdf" else "unknown"))),  # type: ignore[arg-type]
            sheet_name=primary.sheet_name if primary else None,
            status=self.status,  # type: ignore[arg-type]
            reason=self.reason,
        )


# --------------------------------------------------------------------------- #
# 暂存
# --------------------------------------------------------------------------- #


def _store_file(db: Session, settings: Settings, name: str, data: bytes) -> models.FileObject:
    blob = blobs.store_bytes(data, settings)
    existing = db.scalar(
        select(models.FileObject).where(
            models.FileObject.sha256 == blob.sha256,
            models.FileObject.byte_size == blob.byte_size,
        )
    )
    if existing is not None:
        return existing
    media = {
        "csv": "text/csv",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "pdf": "application/pdf",
    }
    row = models.FileObject(
        file_id=new_id("file"),
        sha256=blob.sha256,
        byte_size=blob.byte_size,
        storage_key=blob.storage_key,
        media_type=media.get(sniff_format(name, data), "application/octet-stream"),
        created_at=now_iso(),
    )
    db.add(row)
    db.flush()
    return row


def stage_upload(
    db: Session, settings: Settings, *, name: str, data: bytes
) -> StagedFile:
    fmt = sniff_format(name, data)
    if fmt == "pdf":
        file_row = _store_file(db, settings, name, data)
        return StagedFile(
            file_id=file_row.file_id,
            original_name=name,
            storage_key=file_row.storage_key,
            sha256=file_row.sha256,
            byte_size=file_row.byte_size,
            format="pdf",
            sheets=[],
            status="needs_confirmation",
            reason="PDF 来源需要教师指定用途（课标或教材）",
        )
    if fmt == "xlsx":
        file_row = _store_file(db, settings, name, data)
        sheets = parse_xlsx(data, name)
        staged: list[StagedSheet] = []
        for index, sheet in enumerate(sheets):
            role = detect_role(sheet.headers)
            staged.append(
                StagedSheet(
                    logical_slot=f"{file_row.file_id}:{sheet.name}",
                    sheet_name=sheet.name,
                    role=role or "unknown",
                    rows=sheet.rows,
                    headers=sheet.headers,
                )
            )
        file = StagedFile(
            file_id=file_row.file_id,
            original_name=name,
            storage_key=file_row.storage_key,
            sha256=file_row.sha256,
            byte_size=file_row.byte_size,
            format="xlsx",
            sheets=staged,
        )
        if any(sheet.role == "unknown" for sheet in staged):
            file.status = "needs_confirmation"
            file.reason = "存在未能识别的工作表，请指定角色"
        return file

    file_row = _store_file(db, settings, name, data)
    parsed = parse_csv(data, name)
    role = detect_role(parsed.headers)
    file = StagedFile(
        file_id=file_row.file_id,
        original_name=name,
        storage_key=file_row.storage_key,
        sha256=file_row.sha256,
        byte_size=file_row.byte_size,
        format="csv",
        sheets=[
            StagedSheet(
                logical_slot=f"{file_row.file_id}:csv",
                sheet_name=None,
                role=role or "unknown",
                rows=parsed.rows,
                headers=parsed.headers,
            )
        ],
    )
    if role is None:
        file.status = "needs_confirmation"
        file.reason = "未能从表头识别该 CSV 的逻辑角色"
    return file


# --------------------------------------------------------------------------- #
# 导入记录
# --------------------------------------------------------------------------- #


def _coverage(files: list[StagedFile]) -> tuple[list[str], list[str]]:
    covered: list[str] = []
    duplicates: list[str] = []
    for file in files:
        for sheet in file.sheets:
            if sheet.role in TABLE_ROLES or sheet.role in PDF_ROLES:
                if sheet.role in covered:
                    duplicates.append(sheet.role)
                covered.append(sheet.role)
    missing = [role for role in TABLE_ROLES if role not in covered]
    return missing, duplicates


def create_import(
    db: Session,
    settings: Settings,
    *,
    request: ImportRequest,
    files: list[tuple[str, bytes]],
    actor: str,
) -> ImportReceipt:
    if not files:
        raise DomainError("VALIDATION_ERROR", "请至少上传一个文件")
    if len(files) > settings.max_upload_files:
        raise DomainError("VALIDATION_ERROR", f"一次最多上传 {settings.max_upload_files} 个文件")

    staged = [stage_upload(db, settings, name=name, data=data) for name, data in files]
    missing, duplicates = _coverage(staged)
    needs_confirmation = any(file.status == "needs_confirmation" for file in staged)
    input_status = "ready" if not missing else "incomplete"
    if duplicates:
        input_status = "blocked"
        raise DomainError(
            "UNPROCESSABLE",
            "同一逻辑角色被多个文件占用：" + "、".join(sorted(set(duplicates))),
        )

    import_id = new_id("import")
    stamp = now_iso()
    record = models.ImportRecord(
        import_id=import_id,
        family_id=request.dataset_family_id,
        dataset_id=None,
        expected_parent=request.expected_parent_dataset_id,
        actor_ref=actor,
        request_hash=json.dumps(
            sorted(file.sha256 for file in staged), ensure_ascii=False
        ),
        intent=request.intent,
        classification_status="needs_confirmation" if needs_confirmation else "confirmed",
        input_status=input_status,
        mapping_json={
            "files": [
                {
                    "file_id": file.file_id,
                    "role": sheet.role,
                    "sheet_name": sheet.sheet_name,
                    "logical_slot": sheet.logical_slot,
                }
                for file in staged
                for sheet in file.sheets
            ]
            + [
                {"file_id": file.file_id, "role": "standard", "sheet_name": None}
                for file in staged
                if file.format == "pdf"
            ],
            "request": request.model_dump(mode="json"),
        },
        issues_json={"missing_inputs": missing},
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(record)
    db.flush()

    for file in staged:
        slots = file.sheets or [None]
        for sheet in slots:
            slot_name = sheet.logical_slot if sheet else f"{file.file_id}:pdf"
            db.add(
                models.ImportFileRow(
                    import_id=import_id,
                    logical_slot=slot_name,
                    file_id=file.file_id,
                    original_name=file.original_name,
                    sheet_name=sheet.sheet_name if sheet else None,
                    role=sheet.role if sheet else "standard",
                    origin_ref=None,
                    license_scope="authorized_local",
                )
            )
    db.flush()

    receipt = ImportReceipt(
        import_id=import_id,
        dataset_family_id=request.dataset_family_id,
        dataset_id=None,
        classification_status=record.classification_status,  # type: ignore[arg-type]
        input_status=record.input_status,  # type: ignore[arg-type]
        files=[file.receipt() for file in staged],
        missing_inputs=missing,
        quality_summary={},
        job_id=None,
        warnings=[
            {"code": "IMPORT_NEEDS_CONFIRMATION", "message": file.reason or ""}
            for file in staged
            if file.status == "needs_confirmation"
        ],
    )

    if record.classification_status == "confirmed" and receipt.input_status == "ready":
        _materialize(db, settings, record, staged, receipt)
    return receipt


def _tables_from_files(files: list[StagedFile]) -> tuple[RawTables, dict[str, str]]:
    tables: dict[str, list[RawRow]] = {}
    file_by_role: dict[str, str] = {}
    for file in files:
        for sheet in file.sheets:
            if sheet.role in TABLE_ROLES:
                if sheet.role in tables:
                    raise DomainError("UNPROCESSABLE", f"逻辑表 {sheet.role} 重复提供")
                tables[sheet.role] = sheet.rows
                file_by_role[sheet.role] = file.file_id
    missing = [role for role in TABLE_ROLES if role not in tables]
    if missing:
        raise DomainError(
            "UNPROCESSABLE",
            "缺少必要数据表：" + "、".join(missing),
            [{"field": role, "reason": "未提供"} for role in missing],
        )
    return RawTables.model_validate(tables), file_by_role


def _materialize(
    db: Session,
    settings: Settings,
    record: models.ImportRecord,
    files: list[StagedFile],
    receipt: ImportReceipt,
) -> None:
    tables, file_by_role = _tables_from_files(files)
    try:
        prepared = prepare_dataset(tables, ImportConfig(rule_version="import-v1"))
    except DomainError as exc:
        if exc.code == "STRUCTURE_INVALID":
            raise DomainError("UNPROCESSABLE", exc.message, exc.details) from exc
        raise

    intent = record.intent
    family_id = record.family_id
    if intent == "append_to_family":
        if not family_id:
            raise DomainError("VALIDATION_ERROR", "追加导入必须指定数据集系列")
        if not record.expected_parent:
            raise DomainError("VALIDATION_ERROR", "追加导入必须指定期望的父快照")
        parent = db.get(models.Dataset, record.expected_parent)
        if parent is None:
            raise DomainError("NOT_FOUND", "父快照不存在")
        if parent.family_id != family_id:
            raise DomainError("CONFLICT", "父快照不属于该数据集系列")
        family = ensure_family(db, family_id=family_id)
        parent_id: str | None = parent.id
    else:
        if family_id:
            family = ensure_family(db, family_id=family_id)
        else:
            family = ensure_family(
                db,
                label=f"导入 {record.import_id[-6:]}",
                data_origin="official",
            )
        parent_id = None

    dataset_id = new_id("ds")
    prepared.source_manifest.dataset_id = dataset_id
    prepared.source_manifest.dataset_family_id = family.family_id
    prepared.source_manifest.parent_dataset_id = parent_id
    catalog = build_base_catalog(
        tables,
        new_id("catalog"),
        read_course_review(settings.data_root / "course-review.json"),
    )
    dataset_id = save_dataset(
        db,
        tables,
        prepared,
        catalog,
        family=family,
        import_id=record.import_id,
        parent_id=parent_id,
        source_files=file_by_role,
    )

    record.dataset_id = dataset_id
    record.family_id = family.family_id
    record.updated_at = now_iso()
    record.issues_json = {
        "missing_inputs": [],
        "quality_issues": len(prepared.quality_issues),
        "catalog_version": catalog.version,
    }
    db.flush()

    from study_companion import pipeline

    job = pipeline.enqueue_after_import(
        db,
        dataset_id=dataset_id,
        family_id=family.family_id,
        catalog_version=catalog.version,
        node_ids=[node.node_id for node in prepared.nodes],
        import_id=record.import_id,
    )
    receipt.dataset_id = dataset_id
    receipt.dataset_family_id = family.family_id
    receipt.quality_summary = {
        "quality_issue_count": len(prepared.quality_issues),
        "students": len(prepared.students),
        "nodes": len(prepared.nodes),
        "materials": len(catalog.materials),
        "records": len(prepared.raw_events_candidates) if hasattr(prepared, "raw_events_candidates") else len(prepared.selected_events),
    }
    receipt.job_id = job.id if job is not None else None


def confirm_import(
    db: Session,
    settings: Settings,
    *,
    import_id: str,
    mappings: list[dict[str, Any]],
    intent: str,
    dataset_family_id: str | None,
    expected_parent: str | None,
    actor: str,
) -> ImportReceipt:
    record = db.get(models.ImportRecord, import_id)
    if record is None:
        raise DomainError("NOT_FOUND", "导入批次不存在")
    if record.dataset_id:
        return _receipt_from_record(db, record)

    files: list[StagedFile] = []
    for row in db.scalars(
        select(models.ImportFileRow).where(models.ImportFileRow.import_id == import_id)
    ).all():
        file_row = db.get(models.FileObject, row.file_id)
        if file_row is None:
            continue
        data = blobs.read_bytes(file_row.storage_key, settings)
        staged = stage_upload(db, settings, name=row.original_name, data=data)
        for sheet in staged.sheets:
            if sheet.sheet_name == row.sheet_name:
                sheet.role = row.role
        files.append(staged)

    by_slot = {item["logical_slot"]: item for item in record.mapping_json.get("files", []) if item.get("logical_slot")}
    for mapping in mappings:
        file_id = mapping.get("file_id")
        sheet_name = mapping.get("sheet_name")
        role = mapping.get("role")
        if not file_id or not role:
            raise DomainError("VALIDATION_ERROR", "文件映射缺少 file_id 或 role")
        applied = False
        for file in files:
            if file.file_id != file_id:
                continue
            for sheet in file.sheets:
                if sheet.sheet_name == sheet_name or (sheet_name is None and len(file.sheets) == 1):
                    sheet.role = role
                    applied = True
            if not applied and file.format == "pdf" and sheet_name is None:
                file.pdf_role = role
                applied = True
        if not applied:
            raise DomainError("NOT_FOUND", "映射引用的文件或工作表不存在")
        for slot, item in by_slot.items():
            if item.get("file_id") == file_id and item.get("sheet_name") == sheet_name:
                item["role"] = role

    if intent:
        record.intent = intent
    if dataset_family_id:
        record.family_id = dataset_family_id
    if expected_parent:
        record.expected_parent = expected_parent
    record.actor_ref = actor

    missing, _ = _coverage(files)
    record.mapping_json = {**record.mapping_json, "files": list(by_slot.values())}
    record.classification_status = "confirmed"
    record.input_status = "ready" if not missing else "incomplete"
    record.issues_json = {"missing_inputs": missing}
    record.updated_at = now_iso()

    receipt = _receipt_from_record(db, record)
    if not missing:
        _materialize(db, settings, record, files, receipt)
        db.flush()
        receipt = _receipt_from_record(db, record)
    db.flush()
    return receipt


def _receipt_from_record(db: Session, record: models.ImportRecord) -> ImportReceipt:
    rows = db.scalars(
        select(models.ImportFileRow).where(models.ImportFileRow.import_id == record.import_id)
    ).all()
    files: list[ImportFile] = []
    for row in rows:
        file_row = db.get(models.FileObject, row.file_id)
        if file_row is None:
            continue
        files.append(
            ImportFile(
                file_id=row.file_id,
                original_name=row.original_name,
                sha256=file_row.sha256,
                byte_size=file_row.byte_size,
                format="pdf" if file_row.media_type == "application/pdf" else ("xlsx" if "sheet" in file_row.media_type else "csv"),
                role=row.role,  # type: ignore[arg-type]
                sheet_name=row.sheet_name,
                status="accepted" if row.role != "unknown" else "needs_confirmation",
            )
        )
    issues = record.issues_json or {}
    return ImportReceipt(
        import_id=record.import_id,
        dataset_family_id=record.family_id,
        dataset_id=record.dataset_id,
        classification_status=record.classification_status,  # type: ignore[arg-type]
        input_status=record.input_status,  # type: ignore[arg-type]
        files=files,
        missing_inputs=list(issues.get("missing_inputs") or []),
        quality_summary={
            key: value
            for key, value in issues.items()
            if key in {"quality_issue_count", "students", "nodes", "materials", "records"}
        },
        warnings=[],
    )
