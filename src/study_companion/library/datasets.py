"""Store each import as an independent dataset snapshot."""

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session
from study_companion.analysis.prepare import parse_node_questions

from study_companion.contracts import Catalog, DomainError, PreparedDataset, RawTables
from study_companion.db import models


def save_dataset(
    db: Session, tables: RawTables, prepared: PreparedDataset, catalog: Catalog
) -> None:
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

    now = datetime.now(UTC).isoformat()
    db.add(
        models.Dataset(
            id=dataset_id,
            created_at=now,
            parent_id=None,
            status="ready_with_issues" if prepared.quality_issues else "ready",
            manifest_json=prepared.source_manifest.model_dump(),
        )
    )
    db.flush()
    row_ids: dict[tuple[str, int], str] = {}
    for table_name, rows in tables.model_dump().items():
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
                )
            )
    for student in prepared.students:
        db.add(
            models.Student(
                dataset_id=dataset_id,
                student_id=student.student_id,
                class_id=student.class_id,
                grade=student.grade,
            )
        )
    for node in prepared.nodes:
        db.add(
            models.Node(
                dataset_id=dataset_id,
                node_id=node.node_id,
                payload_json=node.model_dump(),
            )
        )
    catalog_data = catalog.model_dump()
    digest = hashlib.sha256(
        json.dumps(catalog_data, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    db.add(
        models.Catalog(
            version=catalog.version,
            dataset_id=dataset_id,
            content_hash=digest,
            created_at=now,
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
                payload_json=indicator.model_dump(),
            )
        )
    for material in catalog.materials:
        db.add(
            models.Material(
                catalog_version=catalog.version,
                material_id=material.material_id,
                payload_json=material.model_dump(),
            )
        )
    record_rows = {
        record.record_id: record
        for record in prepared.raw_records
        if record.table_name == "records"
    }
    for event in prepared.selected_events:
        record = record_rows.get(event.selected_record_id)
        source_id = (
            row_ids.get((record.table_name, record.source_row_number))
            if record
            else None
        )
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
            )
        )
    for issue in prepared.quality_issues:
        db.add(
            models.QualityIssue(
                id=f"{dataset_id}:{issue.issue_id}",
                dataset_id=dataset_id,
                event_id=issue.event_id,
                payload_json=issue.model_dump(),
            )
        )
    db.flush()


def load_prepared_dataset(db: Session, dataset_id: str) -> PreparedDataset:
    dataset = db.get(models.Dataset, dataset_id)
    if dataset is None:
        raise DomainError("NOT_FOUND", "数据集不存在")
    students = db.scalars(
        select(models.Student).where(models.Student.dataset_id == dataset_id)
    ).all()
    nodes = db.scalars(
        select(models.Node).where(models.Node.dataset_id == dataset_id)
    ).all()
    sources = db.scalars(
        select(models.SourceRow)
        .where(models.SourceRow.dataset_id == dataset_id)
        .order_by(models.SourceRow.table_name, models.SourceRow.row_number)
    ).all()
    events = db.scalars(
        select(models.AnswerEvent).where(models.AnswerEvent.dataset_id == dataset_id)
    ).all()
    issues = db.scalars(
        select(models.QualityIssue).where(models.QualityIssue.dataset_id == dataset_id)
    ).all()
    records = []
    node_sources = {row.payload_json.get("节点编号"): row.payload_json for row in sources if row.table_name == "nodes"}
    for row in sources:
        record_id = (
            row.payload_json.get("记录编号") if row.table_name == "records" else None
        )
        records.append(
            {
                "record_id": record_id or f"{row.table_name}:{row.row_number}",
                "table_name": row.table_name,
                "source_row_number": row.row_number,
                "payload": row.payload_json,
            }
        )
    return PreparedDataset.model_validate(
        {
            "students": [
                {
                    "student_id": row.student_id,
                    "class_id": row.class_id,
                    "grade": row.grade,
                }
                for row in students
            ],
            "nodes": [
                {
                    **row.payload_json,
                    "available_question_ids": row.payload_json.get("available_question_ids")
                    or parse_node_questions(node_sources.get(row.node_id, {}).get("可用题目编号")),
                }
                for row in nodes
            ],
            "raw_records": records,
            "selected_events": [row.payload_json for row in events],
            "quality_issues": [row.payload_json for row in issues],
            "source_manifest": dataset.manifest_json,
        }
    )


def load_catalog(db: Session, version: str, dataset_id: str) -> Catalog:
    row = db.get(models.Catalog, version)
    if row is None:
        raise DomainError("NOT_FOUND", "课程目录不存在")
    if row.dataset_id != dataset_id:
        raise DomainError("FORBIDDEN", "课程目录不属于当前数据集")
    indicators = db.scalars(
        select(models.Indicator).where(models.Indicator.catalog_version == version)
    ).all()
    materials = db.scalars(
        select(models.Material).where(models.Material.catalog_version == version)
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
