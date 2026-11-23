"""Download a self-contained snapshot of one analysis run."""

import csv
from hashlib import sha256
from io import BytesIO, StringIO
import json
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Response
from sqlalchemy import select

from study_companion.api.deps import DbSession, Scope
from study_companion.api.envelope import success
from study_companion.api.routes.runs import get_run
from study_companion.contracts import DomainError
from study_companion.db import models
from study_companion.library.datasets import load_catalog, load_prepared_dataset

router = APIRouter(tags=["exports"])


def json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def build_files(run_id: str, db: DbSession, scope: Scope, published_only: bool = False) -> tuple[dict, dict[str, bytes]]:
    scope.require("export_run")
    run = get_run(db, scope, run_id)
    if run.status != "succeeded":
        raise DomainError("RESULT_NOT_READY", "分析完成后才能导出成果")
    rows = db.scalars(select(models.AnalysisResult).where(
        models.AnalysisResult.run_id == run_id,
    ).order_by(models.AnalysisResult.subject_type, models.AnalysisResult.subject_id, models.AnalysisResult.node_id)).all()
    saved = db.scalars(select(models.RecommendationResult).where(
        models.RecommendationResult.run_id == run_id,
    ).order_by(models.RecommendationResult.student_id, models.RecommendationResult.node_id)).all()
    students = [row.payload_json for row in rows if row.subject_type == "student"]
    classes = [row.payload_json for row in rows if row.subject_type == "class"]
    recommendations = []
    for row in saved:
        current = row.payload_json
        bundle = {key: value for key, value in current.items() if key not in {"revisions", "published"}}
        if published_only:
            snapshots = current.get("published", {})
            visible = [snapshots.get(item["recommendation_id"], item) for item in current["recommendations"]]
            bundle["recommendations"] = [item for item in visible if item["review_status"] == "validated"]
            bundle["validation_issues"] = []
        if bundle["recommendations"]:
            recommendations.append(bundle)
    prepared = load_prepared_dataset(db, run.dataset_id)
    catalog = load_catalog(db, run.catalog_version, run.dataset_id)
    sample = next((result for result in students if result["metrics"]), None)
    sample_student = sample["student_id"] if sample else None
    sample_node = sample["node_id"] if sample else None
    sample_events = [
        item for item in prepared.selected_events
        if item.student_id == sample_student and item.node_id == sample_node
    ]
    record_ids = {
        ref["id"] for row in rows
        for key in ("metrics", "findings") for item in row.payload_json[key]
        for ref in item["evidence_refs"] if ref["kind"] == "record"
    }
    record_ids.update(record_id for issue in prepared.quality_issues for record_id in issue.record_ids)
    record_ids.update(item.selected_record_id for item in sample_events)
    metrics = StringIO(newline="")
    writer = csv.writer(metrics)
    metric_fields = ["numerator", "denominator", "value", "unit"]
    writer.writerow(["subject_type", "subject_id", "node_id", "indicator_id", "metric_id", "name", *metric_fields])
    for row in rows:
        for metric in row.payload_json["metrics"]:
            writer.writerow([
                row.subject_type, row.subject_id, row.node_id, metric["scope"].get("indicator_id"),
                metric["metric_id"], metric["name"], *(metric[key] for key in metric_fields),
            ])
    payloads = {
        "field_dictionary.json": {
            "source_fields": [item.payload for item in prepared.raw_records if item.table_name == "dictionary"],
            "result_fields": {
                "scope": "统计所属学生或班级、测验及学习要求",
                "numerator": "分子", "denominator": "分母", "value": "统计值；空表示缺少可用结果",
                "evidence_refs": "按编号查找统计项或作答记录", "review_status": "建议审核状态",
            },
        },
        "curriculum.json": catalog.model_dump(mode="json", exclude={"materials"}),
        "materials_index.json": [
            item.model_dump(mode="json", exclude={"reference_answer", "reference_explanation"})
            for item in catalog.materials
        ],
        "data_quality.json": [item.model_dump(mode="json") for item in prepared.quality_issues],
        "student_analysis.json": students,
        "class_analysis.json": classes,
        "recommendations.json": recommendations,
        "evidence_records.json": [
            item.model_dump(mode="json") for item in prepared.raw_records
            if item.table_name == "records" and item.record_id in record_ids
        ],
        "examples/input.json": {
            "student": next((item.model_dump(mode="json") for item in prepared.students if item.student_id == sample_student), None),
            "node": next((item.model_dump(mode="json") for item in prepared.nodes if item.node_id == sample_node), None),
            "selected_events": [item.model_dump(mode="json") for item in sample_events],
            "source_records": [
                item.model_dump(mode="json") for item in prepared.raw_records
                if item.table_name == "records" and sample and item.payload.get("节点编号") == sample_node
                and (item.payload.get("学生编号") or item.payload.get("学号")) == sample_student
            ],
            "analysis_config": run.config_json,
        },
        "examples/output.json": {
            "analysis": sample,
            "recommendations": next((item for item in recommendations if
                item["student_id"] == sample_student and item["node_id"] == sample_node), None),
        },
    }
    files = {name: json_bytes(value) for name, value in payloads.items()}
    files["metrics.csv"] = metrics.getvalue().encode("utf-8-sig")
    files["README.md"] = (
        "# 智慧学伴成果包\n\n按课程、班级、学生、建议查看 JSON；metrics.csv 是相同统计的平铺版本。"
        "证据引用可在分析 JSON 和 evidence_records.json 按编号核对。题目的评分点定义在 materials_index.json。"
        "examples 提供一位学生、一次测验的原始作答、选中事件和对应输出。\n"
        "本成果包含当前数据集的作答证据，仅供获授权的教师或评审使用。\n"
    ).encode("utf-8")
    manifest = {
        "schema_version": "1.0", "run_id": run_id, "dataset_id": run.dataset_id,
        "catalog_version": run.catalog_version, "rule_version": run.rule_version, "created_at": run.created_at,
        "generation_methods": sorted({
            rec["generation_method"] for item in recommendations for rec in item["recommendations"]
        }),
        "recommendation_scope": "published" if published_only else "teacher_current",
        "limitations": [
            "建议只包含学生可见的已发布版本。" if published_only else "建议含教师当前草稿，审核状态见建议文件。",
            *([] if recommendations else ["当前范围没有可导出的学习建议。"]),
            "数据质量报告对应导入数据集。样例包含一位学生的原始作答、选中事件与对应输出。",
            "未包含模型凭据、日志和教材原文件。",
        ],
        "files": [{"path": name, "sha256": sha256(data).hexdigest()} for name, data in sorted(files.items())],
    }
    return manifest, files


@router.get("/runs/{run_id}/exports")
def preview_export(run_id: str, db: DbSession, scope: Scope, published_only: bool = False) -> dict:
    manifest, _ = build_files(run_id, db, scope, published_only)
    return success(manifest)


@router.get("/runs/{run_id}/exports/download")
def download_export(run_id: str, db: DbSession, scope: Scope, published_only: bool = False) -> Response:
    manifest, files = build_files(run_id, db, scope, published_only)
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json_bytes(manifest))
        for name, data in files.items():
            archive.writestr(name, data)
    return Response(buffer.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="study-companion-results.zip"',
                             "Cache-Control": "no-store"})
