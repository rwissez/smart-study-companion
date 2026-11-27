"""数据集快照读写：原始行、名册、节点、目录与作答事件。

一个 ``dataset_id`` 是**不可变输入快照**；``dataset_family_id`` 是持续身份空间。
历史按快照保留，绝不用当前名册覆盖旧快照（06 §2）。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.contracts import (
    Catalog,
    DatasetSummary,
    DomainError,
    PreparedDataset,
    RawTables,
)
from study_companion.db import models


def parse_node_questions(raw: str | None) -> list[str] | None:
    """节点"可用题目编号"按顿号/逗号分隔；空值表示未知而不是无题。"""

    if not raw or not raw.strip():
        return None
    return [part.strip() for part in re.split(r"[、,，;；\s]+", raw) if part.strip()]


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:20]}"


def content_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


# --------------------------------------------------------------------------- #
# 系列与身份
# --------------------------------------------------------------------------- #


def ensure_family(db: Session, *, label: str = "", data_origin: str = "official", family_id: str | None = None) -> models.DatasetFamily:
    if family_id is not None:
        family = db.get(models.DatasetFamily, family_id)
        if family is None:
            raise DomainError("NOT_FOUND", "数据集系列不存在")
        return family
    family = models.DatasetFamily(
        family_id=new_id("family"),
        label=label,
        data_origin=data_origin,
        created_at=now_iso(),
        active_dataset_id=None,
        generation=0,
    )
    db.add(family)
    db.flush()
    return family


def ensure_student_identity(
    db: Session, family_id: str, student_id: str, created_at: str
) -> models.StudentIdentity:
    row = db.scalar(
        select(models.StudentIdentity).where(
            models.StudentIdentity.family_id == family_id,
            models.StudentIdentity.external_id == student_id,
        )
    )
    if row is not None:
        return row
    row = models.StudentIdentity(
        student_uid=new_id("su"),
        family_id=family_id,
        external_id=student_id,
        created_at=created_at,
    )
    db.add(row)
    db.flush()
    return row


def ensure_class_identity(
    db: Session, family_id: str, class_id: str, created_at: str
) -> models.ClassIdentity:
    row = db.scalar(
        select(models.ClassIdentity).where(
            models.ClassIdentity.family_id == family_id,
            models.ClassIdentity.external_id == class_id,
        )
    )
    if row is not None:
        return row
    row = models.ClassIdentity(
        class_uid=new_id("cu"),
        family_id=family_id,
        external_id=class_id,
        created_at=created_at,
    )
    db.add(row)
    db.flush()
    return row


# --------------------------------------------------------------------------- #
# 写入快照
# --------------------------------------------------------------------------- #


def save_dataset(
    db: Session,
    tables: RawTables,
    prepared: PreparedDataset,
    catalog: Catalog,
    *,
    family: models.DatasetFamily,
    import_id: str | None = None,
    parent_id: str | None = None,
    source_files: dict[str, str] | None = None,
    display_name: str | None = None,
) -> str:
    dataset_id = prepared.source_manifest.dataset_id
    students = {student.student_id for student in prepared.students}
    nodes = {node.node_id for node in prepared.nodes}
    materials = {material.material_id for material in catalog.materials}
    if any(
        event.student_id not in students
        or event.node_id not in nodes
        or event.material_id not in materials
        for event in prepared.selected_events
    ):
        raise DomainError("UNPROCESSABLE", "作答记录引用了不存在的学生、测验或材料")

    stamp = now_iso()
    snapshot_hash = content_hash(
        {
            "tables": {name: [row for row in rows] for name, rows in tables.model_dump().items()},
            "catalog": catalog.model_dump(mode="json"),
        }
    )
    duplicate = db.scalar(
        select(models.Dataset).where(
            models.Dataset.family_id == family.family_id,
            models.Dataset.snapshot_hash == snapshot_hash,
        )
    )
    if duplicate is not None:
        # 同系列同逻辑快照：返回已有快照，不重复计算、不重复落原始行。
        return duplicate.id

    db.add(
        models.Dataset(
            id=dataset_id,
            created_at=stamp,
            parent_id=parent_id,
            status="ready_with_issues" if prepared.quality_issues else "ready",
            manifest_json=prepared.source_manifest.model_dump(),
            family_id=family.family_id,
            snapshot_hash=snapshot_hash,
            state="ready",
            display_name=display_name or family.label or f"数据集 {dataset_id[-6:]}",
        )
    )
    db.flush()

    row_ids: dict[tuple[str, int], str] = {}
    for table_name, rows in tables.model_dump().items():
        role_file = (source_files or {}).get(table_name)
        for row_number, row in enumerate(rows, 1):
            row_id = f"{dataset_id}:{table_name}:{row_number}"
            row_ids[(table_name, row_number)] = row_id
            db.add(
                models.SourceRow(
                    id=row_id,
                    dataset_id=dataset_id,
                    table_name=table_name,
                    row_number=row_number,
                    payload_json=row,
                    file_id=role_file,
                    sheet_key="",
                    physical_row=row_number + 1,
                    row_hash=content_hash(row),
                )
            )

    for student in prepared.students:
        student_identity = ensure_student_identity(db, family.family_id, student.student_id, stamp)
        class_identity = ensure_class_identity(db, family.family_id, student.class_id, stamp)
        db.add(
            models.Student(
                dataset_id=dataset_id,
                student_id=student.student_id,
                class_id=student.class_id,
                grade=student.grade,
                student_uid=student_identity.student_uid,
                class_uid=class_identity.class_uid,
            )
        )
    for node in prepared.nodes:
        payload = node.model_dump()
        db.add(
            models.Node(
                dataset_id=dataset_id,
                node_id=node.node_id,
                payload_json=payload,
                scope_kind=node.scope_kind,
                scope_json={
                    "topic_ids": node.topic_ids,
                    "available_question_ids": node.available_question_ids,
                    "assessment_date": node.assessment_date,
                },
            )
        )

    catalog_data = catalog.model_dump(mode="json")
    db.add(
        models.Catalog(
            version=catalog.version,
            dataset_id=dataset_id,
            content_hash=content_hash(catalog_data),
            created_at=stamp,
            payload_json={
                "topics": catalog_data["topics"],
                "links": catalog_data["links"],
            },
        )
    )
    db.flush()
    for indicator in catalog.indicators:
        db.add(
            models.Indicator(
                catalog_version=catalog.version,
                indicator_id=indicator.indicator_id,
                payload_json=indicator.model_dump(mode="json"),
            )
        )
    for material in catalog.materials:
        db.add(
            models.Material(
                catalog_version=catalog.version,
                material_id=material.material_id,
                payload_json=material.model_dump(mode="json"),
            )
        )

    record_rows = {
        record.record_id: record
        for record in prepared.raw_records
        if record.table_name == "records"
    }
    students_by_id = {student.student_id: student for student in prepared.students}
    for event in prepared.selected_events:
        record = record_rows.get(event.selected_record_id or "")
        source_id = (
            row_ids.get((record.table_name, record.source_row_number)) if record else None
        )
        student_uid = students_by_id[event.student_id].student_uid if event.student_id in students_by_id else None
        db.add(
            models.AnswerEvent(
                dataset_id=dataset_id,
                event_id=event.event_id,
                student_id=event.student_id,
                node_id=event.node_id,
                material_id=event.material_id,
                selected_source_row_id=source_id,
                disposition=event.disposition,
                payload_json=event.model_dump(),
                raw_row_id=source_id,
                student_uid=student_uid,
                source_event_key=event.source_event_key or event.event_id,
                source_revision=event.source_revision or 1,
                normalization_hash=content_hash(event.model_dump(mode="json")),
            )
        )
    for issue in prepared.quality_issues:
        db.add(
            models.QualityIssue(
                id=f"{dataset_id}:{issue.issue_id}",
                dataset_id=dataset_id,
                event_id=issue.event_id,
                payload_json=issue.model_dump(mode="json"),
            )
        )

    if import_id is not None and source_files:
        for role, file_id in source_files.items():
            db.add(
                models.DatasetSource(
                    dataset_id=dataset_id,
                    source_key=role,
                    file_id=file_id,
                    origin_import_id=import_id,
                    source_meta_json={"role": role},
                )
            )

    family.active_dataset_id = dataset_id
    family.generation = (family.generation or 0) + 1
    db.flush()
    return dataset_id


# --------------------------------------------------------------------------- #
# 读取
# --------------------------------------------------------------------------- #


def load_prepared_dataset(db: Session, dataset_id: str) -> PreparedDataset:
    dataset = db.get(models.Dataset, dataset_id)
    if dataset is None:
        raise DomainError("NOT_FOUND", "数据集不存在")
    students = db.scalars(
        select(models.Student).where(models.Student.dataset_id == dataset_id)
    ).all()
    nodes = db.scalars(select(models.Node).where(models.Node.dataset_id == dataset_id)).all()
    sources = db.scalars(
        select(models.SourceRow)
        .where(models.SourceRow.dataset_id == dataset_id)
        .order_by(models.SourceRow.table_name, models.SourceRow.row_number)
    ).all()
    events = db.scalars(
        select(models.AnswerEvent)
        .where(models.AnswerEvent.dataset_id == dataset_id)
        .order_by(models.AnswerEvent.event_id)
    ).all()
    issues = db.scalars(
        select(models.QualityIssue).where(models.QualityIssue.dataset_id == dataset_id)
    ).all()
    records: list[dict] = []
    node_sources = {
        row.payload_json.get("节点编号"): row.payload_json
        for row in sources
        if row.table_name == "nodes"
    }
    for row in sources:
        record_id = row.payload_json.get("记录编号") if row.table_name == "records" else None
        records.append(
            {
                "record_id": record_id or f"{row.table_name}:{row.row_number}",
                "table_name": row.table_name,
                "source_row_number": row.row_number,
                "payload": row.payload_json,
            }
        )
    manifest = dict(dataset.manifest_json or {})
    manifest.setdefault("dataset_id", dataset_id)
    manifest.setdefault("dataset_family_id", dataset.family_id)
    return PreparedDataset.model_validate(
        {
            "students": [
                {
                    "student_id": row.student_id,
                    "class_id": row.class_id,
                    "grade": row.grade,
                    "student_uid": row.student_uid,
                }
                for row in students
            ],
            "nodes": [
                {
                    **{
                        key: value
                        for key, value in row.payload_json.items()
                        if key in {"node_id", "label", "assessment_date", "topic_ids", "scope_kind"}
                    },
                    "node_id": row.node_id,
                    "scope_kind": row.scope_kind or "assessment",
                    "available_question_ids": row.payload_json.get("available_question_ids")
                    or parse_node_questions(
                        node_sources.get(row.node_id, {}).get("可用题目编号")
                    ),
                }
                for row in nodes
            ],
            "raw_records": records,
            "selected_events": [row.payload_json for row in events],
            "quality_issues": [row.payload_json for row in issues],
            "source_manifest": manifest,
        }
    )


def load_catalog(db: Session, version: str, dataset_id: str | None = None) -> Catalog:
    row = db.get(models.Catalog, version)
    if row is None:
        raise DomainError("NOT_FOUND", "课程目录不存在")
    if dataset_id is not None and row.dataset_id != dataset_id:
        raise DomainError("FORBIDDEN", "课程目录不属于当前数据集")
    indicators = db.scalars(
        select(models.Indicator)
        .where(models.Indicator.catalog_version == version)
        .order_by(models.Indicator.indicator_id)
    ).all()
    materials = db.scalars(
        select(models.Material)
        .where(models.Material.catalog_version == version)
        .order_by(models.Material.material_id)
    ).all()
    return Catalog.model_validate(
        {
            "version": version,
            "topics": row.payload_json.get("topics", []),
            "links": row.payload_json.get("links", []),
            "indicators": [item.payload_json for item in indicators],
            "materials": [item.payload_json for item in materials],
        }
    )


def latest_catalog_version(db: Session, dataset_id: str) -> str | None:
    return db.scalar(
        select(models.Catalog.version)
        .where(models.Catalog.dataset_id == dataset_id)
        .order_by(models.Catalog.created_at.desc())
        .limit(1)
    )


def dataset_summary(db: Session, dataset: models.Dataset) -> DatasetSummary:
    student_count = len(
        db.scalars(select(models.Student.student_id).where(models.Student.dataset_id == dataset.id)).all()
    )
    class_count = len(
        set(
            db.scalars(
                select(models.Student.class_id).where(models.Student.dataset_id == dataset.id)
            ).all()
        )
    )
    node_ids = list(
        db.scalars(select(models.Node.node_id).where(models.Node.dataset_id == dataset.id)).all()
    )
    return DatasetSummary(
        dataset_id=dataset.id,
        dataset_family_id=dataset.family_id,
        parent_id=dataset.parent_id,
        status=dataset.status,
        created_at=dataset.created_at,
        synthetic=bool((dataset.manifest_json or {}).get("synthetic", False)),
        display_name=dataset.display_name,
        catalog_version=latest_catalog_version(db, dataset.id),
        node_ids=node_ids,
        student_count=student_count,
        class_count=class_count,
    )
