"""统一证据解析（01 §6）。

所有引用都按"类型 + 精确版本"定位，服务端补足可确定版本；无法唯一解析时报错，
不做模糊匹配。学生投影按显式 allowlist 裁剪，答案与评分细则不外泄。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.contracts import (
    CurriculumDetail,
    DerivedDetail,
    DomainError,
    EvidenceContext,
    EvidenceDetail,
    EvidenceRef,
    Finding,
    LearningDetail,
    Material,
    Metric,
    MetricDetail,
    ProgressComparison,
    RecordDetail,
    RubricScoreView,
)
from study_companion.db import models


def _material_view(db: Session, catalog_version: str, material_id: str, *, teacher: bool) -> dict[str, Any]:
    row = db.get(models.Material, (catalog_version, material_id))
    if row is None:
        raise DomainError("NOT_FOUND", "引用的材料不存在于该目录版本")
    material = Material.model_validate(dict(row.payload_json))
    payload: dict[str, Any] = {
        "material_id": material.material_id,
        "material_type": material.material_type,
        "catalog_version": catalog_version,
        "topic_ids": material.topic_ids,
        "indicator_ids": material.indicator_ids,
        "content": material.content,
        "options": material.options,
        "question_type": material.question_type,
        "source_label": material.source_label,
        "source_locator": material.source_locator,
        "max_score": material.max_score,
        "difficulty_level": material.difficulty.level if material.difficulty else None,
        "difficulty_status": material.difficulty.status if material.difficulty else None,
        "display_order": material.display_order,
    }
    if teacher:
        payload.update(
            {
                "reference_answer": material.reference_answer,
                "reference_explanation": material.reference_explanation,
                "rubric_points": material.rubric_points,
                "reviewed_variant_tag": material.reviewed_variant_tag,
            }
        )
    return payload


def _find_event(db: Session, dataset_id: str, record_id: str) -> models.AnswerEvent | None:
    events = db.scalars(
        select(models.AnswerEvent).where(models.AnswerEvent.dataset_id == dataset_id)
    ).all()
    for event in events:
        payload = event.payload_json or {}
        if payload.get("selected_record_id") == record_id or event.event_id == record_id:
            return event
    return None



def _raw_record_row(db: Session, dataset_id: str, record_id: str | None) -> dict:
    """取官方原始行，用于补齐事件里没有保存的作答原文。

    事件只保存选中记录与评分，原始作答（学生最终作答/解答过程/提交时间）
    仍在 source_rows；这里按记录编号精确匹配，找不到就返回空字典。
    """

    if not record_id:
        return {}
    rows = db.scalars(
        select(models.SourceRow).where(
            models.SourceRow.dataset_id == dataset_id,
            models.SourceRow.table_name == "records",
        )
    ).all()
    for row in rows:
        payload = row.payload_json or {}
        if str(payload.get("记录编号") or "") == str(record_id):
            return payload
    return {}

def _resolve_record(
    db: Session, ref: EvidenceRef, context: EvidenceContext | None, scope
) -> EvidenceDetail:
    dataset_id = ref.dataset_id
    run = db.get(models.AnalysisRun, ref.run_id) if ref.run_id else None
    if dataset_id is None and run is not None:
        dataset_id = run.dataset_id
    if dataset_id is None:
        raise DomainError("VALIDATION_ERROR", "作答引用缺少数据集定位")
    if scope.role == "student" and scope.dataset_id and dataset_id != scope.dataset_id:
        raise DomainError("FORBIDDEN", "当前会话无权访问该作答")
    if run is not None and scope.role == "teacher" and scope.dataset_id and run.dataset_id != scope.dataset_id:
        raise DomainError("FORBIDDEN", "当前会话无权访问该作答")

    event = _find_event(db, dataset_id, ref.id)
    catalog_version = run.catalog_version if run else (context and None) or ref.catalog_version
    if event is None:
        raise DomainError("NOT_FOUND", "作答记录不存在")
    payload = event.payload_json or {}
    if scope.role == "student" and scope.student_id and event.student_id != scope.student_id:
        raise DomainError("FORBIDDEN", "学生会话只能查看本人作答")
    if catalog_version is None:
        row = db.scalar(
            select(models.Catalog.version)
            .where(models.Catalog.dataset_id == dataset_id)
            .order_by(models.Catalog.created_at.desc())
            .limit(1)
        )
        catalog_version = row
    if catalog_version is None:
        raise DomainError("RESULT_NOT_READY", "该数据集暂无正式目录")
    teacher = scope.role == "teacher"
    rubric_points = payload.get("rubric_scores") or []
    scores: list[RubricScoreView] = []
    material_row = db.get(models.Material, (catalog_version, event.material_id))
    point_names: dict[str, str] = {}
    if material_row is not None and teacher:
        for point in (material_row.payload_json or {}).get("rubric_points", []):
            point_names[point["point_id"]] = point["description"]
    for item in rubric_points:
        point_id = item.get("point_id") if isinstance(item, dict) else None
        if not point_id:
            continue
        scores.append(
            RubricScoreView(
                point_id=point_id,
                description=point_names.get(point_id) if teacher else None,
                score=item.get("score"),
                max_score=item.get("max_score"),
            )
        )
    raw_record = _raw_record_row(
        db, dataset_id, payload.get("selected_record_id") or ref.id
    )
    detail = RecordDetail(
        question=_material_view(db, catalog_version, event.material_id, teacher=teacher),
        student_id=event.student_id,
        assessment_label=event.node_id,
        submitted_at=raw_record.get("模拟提交时间") or payload.get("submitted_at"),
        status=payload.get("status", "graded"),
        score=payload.get("score"),
        max_score=payload.get("max_score"),
        final_answer=raw_record.get("学生最终作答") or payload.get("final_answer"),
        solution_steps=raw_record.get("解答过程") or payload.get("solution_steps"),
        rubric_scores=scores,
        quality_issues=[],
        historical_revision=payload.get("revision"),
        related_findings=[],
    )
    detail_payload = detail.model_dump(mode="json")
    if not teacher:
        # 学生投影必须**删除**教师字段，而不是置空：null/[] 仍然暴露字段存在。
        for field in (
            "reference_answer",
            "reference_explanation",
            "rubric_points",
            "reviewed_variant_tag",
        ):
            detail_payload["question"].pop(field, None)
        for point in detail_payload.get("rubric_scores", []):
            point.pop("description", None)
    return EvidenceDetail(
        ref=ref,
        title=f"作答记录 {ref.id}",
        detail_type="record",
        summary=f"得分 {payload.get('score')}/{payload.get('max_score')}",
        detail=detail_payload,
        limitations=["原始记录不可改写，评分修订以最新有效版本为准"],
        visibility="full",
    )


def _resolve_metric(
    db: Session, ref: EvidenceRef, context: EvidenceContext | None, scope
) -> EvidenceDetail:
    if not ref.run_id:
        raise DomainError("VALIDATION_ERROR", "指标引用缺少 run_id")
    rows = db.scalars(
        select(models.AnalysisResult).where(models.AnalysisResult.run_id == ref.run_id)
    ).all()
    for row in rows:
        payload = row.payload_json or {}
        if scope.role == "student" and row.subject_type == "student" and scope.student_id != row.subject_id:
            continue
        for item in payload.get("metrics", []):
            if item.get("metric_id") != ref.id:
                continue
            metric = Metric.model_validate(item)
            detail = MetricDetail(
                metric=metric,
                calculation_description=f"{metric.numerator}/{metric.denominator}（{metric.unit}）",
                evidence_count=len(metric.evidence_refs),
                record_refs=metric.evidence_refs,
                excluded_count=metric.excluded_event_count,
                limitations=metric.limitations,
            )
            return EvidenceDetail(
                ref=ref,
                title=metric.name,
                detail_type="metric",
                summary=f"{metric.name}：{metric.value}",
                detail=detail.model_dump(mode="json"),
                related_refs=metric.evidence_refs,
                limitations=metric.limitations,
            )
    raise DomainError("NOT_FOUND", "该运行中没有此指标")


def _resolve_material(
    db: Session, ref: EvidenceRef, context: EvidenceContext | None, scope
) -> EvidenceDetail:
    if not ref.catalog_version:
        raise DomainError("VALIDATION_ERROR", "材料引用缺少 catalog_version")
    teacher = scope.role == "teacher"
    detail = CurriculumDetail(
        title=f"材料 {ref.id}",
        description="",
        source_locators=[],
        related_refs=[],
    )
    payload = _material_view(db, ref.catalog_version, ref.id, teacher=teacher)
    detail.description = str(payload.get("content", ""))[:400]
    locator = payload.get("source_locator")
    if locator:
        from study_companion.contracts import SourceLocator

        detail.source_locators = [SourceLocator.model_validate(locator)]
    return EvidenceDetail(
        ref=ref,
        title=f"材料 {ref.id}",
        detail_type="curriculum",
        summary=str(payload.get("content", ""))[:120],
        detail={**detail.model_dump(mode="json"), "material": payload},
        limitations=["材料出处必须可回溯到已登记来源"],
    )


def _resolve_curriculum(
    db: Session, ref: EvidenceRef, context: EvidenceContext | None, scope
) -> EvidenceDetail:
    if not ref.catalog_version:
        raise DomainError("VALIDATION_ERROR", "课程引用缺少 catalog_version")
    indicator = db.get(models.Indicator, (ref.catalog_version, ref.id))
    catalog = db.get(models.Catalog, ref.catalog_version)
    if catalog is None:
        raise DomainError("NOT_FOUND", "课程目录版本不存在")
    if indicator is not None:
        payload = dict(indicator.payload_json)
        detail = CurriculumDetail(
            title=payload.get("description", ref.id),
            description=payload.get("description", ""),
            basis=payload.get("curriculum_ref") or None,
        )
        return EvidenceDetail(
            ref=ref,
            title=payload.get("description", ref.id),
            detail_type="curriculum",
            summary=payload.get("description", ""),
            detail=detail.model_dump(mode="json"),
            limitations=["指标级要求不证明细粒度知识点已覆盖"],
        )
    for topic in (catalog.payload_json or {}).get("topics", []):
        if topic.get("topic_id") == ref.id:
            detail = CurriculumDetail(title=topic.get("title", ref.id), description=topic.get("goal", ""))
            return EvidenceDetail(
                ref=ref,
                title=topic.get("title", ref.id),
                detail_type="curriculum",
                summary=topic.get("goal", ""),
                detail=detail.model_dump(mode="json"),
            )
    raise DomainError("NOT_FOUND", "课程对象不存在")


def _resolve_derived(
    db: Session, ref: EvidenceRef, context: EvidenceContext | None, scope
) -> EvidenceDetail:
    if not ref.analysis_version:
        raise DomainError("VALIDATION_ERROR", "诊断引用缺少 analysis_version")
    version = db.get(models.Version, ref.analysis_version)
    if version is None or version.payload_json is None:
        raise DomainError("NOT_FOUND", "诊断版本不存在")
    payload = version.payload_json
    if scope.role == "student" and version.target_key != scope.student_id:
        raise DomainError("FORBIDDEN", "学生会话只能查看本人诊断")
    for item in payload.get("findings", []):
        if item.get("finding_id") == ref.id:
            finding = Finding.model_validate(item)
            detail = DerivedDetail(
                summary=finding.description,
                evidence_refs=finding.evidence_refs,
                limitations=finding.limitations,
                version_label=ref.analysis_version,
            )
            return EvidenceDetail(
                ref=ref,
                title="学情诊断",
                detail_type="derived",
                summary=finding.description,
                detail=detail.model_dump(mode="json"),
                related_refs=finding.evidence_refs,
                limitations=finding.limitations,
            )
    for item in payload.get("progress", []):
        if item.get("comparison_id") == ref.id:
            comparison = ProgressComparison.model_validate(item)
            detail = DerivedDetail(
                summary=comparison.description or comparison.classification,
                evidence_refs=comparison.evidence_refs,
                limitations=comparison.limitations,
                version_label=ref.analysis_version,
            )
            return EvidenceDetail(
                ref=ref,
                title="学习进展",
                detail_type="derived",
                summary=detail.summary,
                detail=detail.model_dump(mode="json"),
                related_refs=comparison.evidence_refs,
                limitations=comparison.limitations,
            )
    raise DomainError("NOT_FOUND", "该版本中没有此诊断或进展对象")


def _resolve_learning(
    db: Session, ref: EvidenceRef, context: EvidenceContext | None, scope
) -> EvidenceDetail:
    if ref.kind == "learning_event":
        row = db.scalar(
            select(models.LearningEventRow).where(models.LearningEventRow.event_id == ref.id)
        )
        if row is None:
            raise DomainError("NOT_FOUND", "学习事件不存在")
        detail = LearningDetail(
            event_type=row.event_type,
            occurred_at=row.occurred_at,
            summary=str((row.payload_json or {}).get("summary", "")),
            related_refs=[],
        )
        return EvidenceDetail(
            ref=ref,
            title="学习事件",
            detail_type="learning",
            summary=detail.summary,
            detail=detail.model_dump(mode="json"),
        )
    if ref.kind == "attempt":
        row = db.get(models.AttemptRow, ref.id)
        if row is None:
            raise DomainError("NOT_FOUND", "作答尝试不存在")
        if scope.role == "student" and scope.student_id:
            student = db.get(models.StudentIdentity, row.student_uid)
            if student is not None and student.external_id != scope.student_id:
                raise DomainError("FORBIDDEN", "学生会话只能查看本人作答")
        detail = LearningDetail(
            event_type="attempt",
            occurred_at=row.created_at,
            summary=f"尝试状态 {row.status}",
            score=None,
            max_score=None,
        )
        return EvidenceDetail(
            ref=ref,
            title="在线作答",
            detail_type="learning",
            summary=detail.summary,
            detail=detail.model_dump(mode="json"),
        )
    if ref.kind == "grade":
        row = db.get(models.Grade, ref.id)
        if row is None:
            raise DomainError("NOT_FOUND", "评分记录不存在")
        detail = LearningDetail(
            event_type="grade",
            occurred_at=row.created_at,
            summary=f"评分状态 {row.status}",
            score=row.score,
            max_score=row.max_score,
        )
        return EvidenceDetail(
            ref=ref,
            title="评分记录",
            detail_type="learning",
            summary=detail.summary,
            detail=detail.model_dump(mode="json"),
        )
    raise DomainError("NOT_FOUND", "未知的学习类引用")


def resolve(
    db: Session, ref: EvidenceRef, context: EvidenceContext | None, scope
) -> EvidenceDetail:
    kind = ref.kind
    if kind == "record":
        return _resolve_record(db, ref, context, scope)
    if kind == "metric":
        return _resolve_metric(db, ref, context, scope)
    if kind == "material":
        return _resolve_material(db, ref, context, scope)
    if kind == "curriculum":
        return _resolve_curriculum(db, ref, context, scope)
    if kind in {"finding", "progress"}:
        return _resolve_derived(db, ref, context, scope)
    if kind in {"learning_event", "attempt", "grade"}:
        return _resolve_learning(db, ref, context, scope)
    if kind in {"knowledge_point", "relation", "annotation", "lesson"}:
        try:
            from study_companion.knowledge.reader import resolve_knowledge_evidence
        except ImportError as exc:  # A 正式读取尚未接入
            raise DomainError("RESULT_NOT_READY", "知识体系正式版本尚未构建") from exc

        return resolve_knowledge_evidence(db, ref, scope)
    if kind == "rubric_point":
        if not (ref.catalog_version and ref.material_id):
            raise DomainError("VALIDATION_ERROR", "评分点引用缺少目录或材料定位")
        material_row = db.get(models.Material, (ref.catalog_version, ref.material_id))
        if material_row is None:
            raise DomainError("NOT_FOUND", "材料不存在")
        if scope.role != "teacher":
            raise DomainError("FORBIDDEN", "评分细则仅教师可见")
        for point in (material_row.payload_json or {}).get("rubric_points", []):
            if point.get("point_id") == ref.id:
                detail = CurriculumDetail(
                    title=f"评分点 {ref.id}",
                    description=point.get("description", ""),
                    basis=f"满分 {point.get('max_score')}",
                )
                return EvidenceDetail(
                    ref=ref,
                    title=detail.title,
                    detail_type="curriculum",
                    summary=detail.description,
                    detail=detail.model_dump(mode="json"),
                )
        raise DomainError("NOT_FOUND", "评分点不存在")
    if kind == "source_page":
        raise DomainError("RESULT_NOT_READY", "受控页面图像尚未启用")
    raise DomainError("NOT_FOUND", "不支持的证据类型")
