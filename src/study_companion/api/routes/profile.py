"""正式读取：个人画像、班级画像、学习方案、进展、作答记录、比较与质量。

所有数字都来自 B/C 的正式版本，路由只做授权、裁剪与拼装，不重算统计。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select

from study_companion.api.deps import DbSession, Scope
from study_companion.api.envelope import success
from study_companion.contracts import (
    AnalysisBundle,
    AnalysisConfig,
    AnswerRecordListItem,
    ClassComparisonView,
    ClassResult,
    ClassResultView,
    ComparisonItem,
    DomainError,
    EvidenceRef,
    RecommendationBundle,
    StudentResult,
    StudentResultView,
)
from study_companion.db import models
from study_companion.db import version_repository as repo

router = APIRouter(tags=["profile"])


# --------------------------------------------------------------------------- #
# 解析助手
# --------------------------------------------------------------------------- #


def _dataset(db: DbSession, dataset_id: str) -> models.Dataset:
    row = db.get(models.Dataset, dataset_id)
    if row is None:
        raise DomainError("NOT_FOUND", "数据集不存在")
    return row


def resolve_run(
    db: DbSession, dataset_id: str, node_id: str | None, analysis_version: str | None = None
) -> tuple[str, str]:
    """返回 (run_id, node_id)；优先显式版本，其次当前运行指针。"""

    if analysis_version:
        version = db.get(models.Version, analysis_version)
        if version is None:
            raise DomainError("NOT_FOUND", "分析版本不存在")
        if version.dataset_id != dataset_id:
            raise DomainError("FORBIDDEN", "分析版本不属于该数据集")
        if not version.run_id:
            raise DomainError("NOT_FOUND", "分析版本缺少运行标识")
        return version.run_id, version.node_key
    if node_id:
        run_id = repo.active_run_id(db, dataset_id, node_id)
        if run_id is None:
            run_id = repo.latest_run_id(db, dataset_id)
        if run_id is None:
            raise DomainError("RESULT_NOT_READY", "该数据集尚无正式分析运行")
        return run_id, node_id
    run_id = repo.latest_run_id(db, dataset_id)
    if run_id is None:
        raise DomainError("RESULT_NOT_READY", "该数据集尚无正式分析运行")
    run = db.get(models.AnalysisRun, run_id)
    node = (run.node_ids_json or [None])[0] if run else None
    if node is None:
        raise DomainError("RESULT_NOT_READY", "该运行没有节点")
    return run_id, node


def _student_version(
    db: DbSession, dataset: models.Dataset, run_id: str, node_id: str, student_id: str
) -> models.Version:
    key = repo.FormalKey(
        family_id=dataset.family_id or "",
        dataset_id=dataset.id,
        kind="student_analysis",
        run_key=run_id,
        node_key=node_id,
        target_key=student_id,
    )
    version = repo.read_formal(db, key)
    if version is None:
        # 局部更新会推进 active run，但其他学生仍停留在上一次正式版本；
        # 这里回退到该学生该节点最近一次已发布版本，避免"别人更新后我看不到"。
        version = db.scalar(
            select(models.Version)
            .join(models.Publication, models.Publication.version_id == models.Version.version_id)
            .where(
                models.Version.dataset_id == dataset.id,
                models.Version.kind == "student_analysis",
                models.Version.node_key == node_id,
                models.Version.target_key == student_id,
            )
            .order_by(models.Version.created_at.desc())
            .limit(1)
        )
    if version is None:
        raise DomainError("RESULT_NOT_READY", "该学生在此节点尚无正式学情")
    return version


def _class_version(
    db: DbSession, dataset: models.Dataset, run_id: str, node_id: str, class_id: str
) -> models.Version:
    key = repo.FormalKey(
        family_id=dataset.family_id or "",
        dataset_id=dataset.id,
        kind="class_analysis",
        run_key=run_id,
        node_key=node_id,
        target_key=class_id,
    )
    version = repo.read_formal(db, key)
    if version is None:
        version = db.scalar(
            select(models.Version)
            .join(models.Publication, models.Publication.version_id == models.Version.version_id)
            .where(
                models.Version.dataset_id == dataset.id,
                models.Version.kind == "class_analysis",
                models.Version.node_key == node_id,
                models.Version.target_key == class_id,
            )
            .order_by(models.Version.created_at.desc())
            .limit(1)
        )
    if version is None:
        raise DomainError("RESULT_NOT_READY", "该班级在此节点尚无正式学情")
    return version


def _require_scope(db: DbSession, scope: Scope, dataset: models.Dataset, *, student: str | None = None, class_id: str | None = None) -> None:
    if scope.role == "student":
        if student is None or student != scope.student_id:
            raise DomainError("FORBIDDEN", "学生会话只能读取本人数据")
    if scope.dataset_id is not None and scope.dataset_id != dataset.id:
        raise DomainError("FORBIDDEN", "当前会话无权访问该数据集")
    if class_id is not None and not scope.can_read_class(class_id):
        raise DomainError("FORBIDDEN", "当前会话未获授权该班级")
    if class_id is None and student is not None and scope.role == "teacher":
        row = db.get(models.Student, (dataset.id, student))
        if row is not None and not scope.can_read_class(row.class_id):
            raise DomainError("FORBIDDEN", "当前会话未获授权该学生的班级")


def _student_view(payload: dict[str, Any]) -> dict[str, Any]:
    result = StudentResult.model_validate(payload)
    view = StudentResultView(
        student_id=result.student_id,
        class_id=result.class_id,
        node_id=result.node_id,
        analysis_version=result.analysis_version,
        run_id=result.run_id,
        dataset_id=result.dataset_id,
        dataset_family_id=result.dataset_family_id,
        catalog_version=result.catalog_version,
        knowledge_version=result.analysis_input.knowledge_version if result.analysis_input else None,
        metrics=result.metrics,
        findings=result.findings,
        progress=result.progress,
        strategy_signals=result.strategy_signals,
        summary=result.summary,
        limitations=result.limitations,
        scope_kind=result.scope_kind,
    )
    return view.model_dump(mode="json")


def _class_view(payload: dict[str, Any]) -> dict[str, Any]:
    result = ClassResult.model_validate(payload)
    view = ClassResultView(
        class_id=result.class_id,
        node_id=result.node_id,
        analysis_version=result.analysis_version,
        run_id=result.run_id,
        dataset_id=result.dataset_id,
        dataset_family_id=result.dataset_family_id,
        catalog_version=result.catalog_version,
        roster_count=result.roster_count,
        observed_student_count=result.observed_student_count,
        metrics=result.metrics,
        findings=result.findings,
        common_findings=result.common_findings,
        performance_distribution=result.performance_distribution,
        attention_students=result.attention_students,
        member_analysis_versions=result.member_analysis_versions,
        summary=result.summary,
        limitations=result.limitations,
    )
    return view.model_dump(mode="json")


# --------------------------------------------------------------------------- #
# 个人 / 班级画像
# --------------------------------------------------------------------------- #


@router.get("/datasets/{dataset_id}/students/{student_id}/profile")
def student_profile(
    dataset_id: str,
    student_id: str,
    db: DbSession,
    scope: Scope,
    node_id: str | None = Query(default=None, max_length=64),
    analysis_version: str | None = Query(default=None, max_length=128),
) -> dict:
    if scope.role == "student":
        scope.require("view_own_analysis")
    else:
        scope.require("view_student_analysis")
    dataset = _dataset(db, dataset_id)
    _require_scope(db, scope, dataset, student=student_id)
    run_id, node = resolve_run(db, dataset_id, node_id, analysis_version)
    version = _student_version(db, dataset, run_id, node, student_id)
    return success(
        {
            "result": _student_view(version.payload_json or {}),
            "freshness": "current",
            "limitations": (version.payload_json or {}).get("limitations", []),
        }
    )


@router.get("/datasets/{dataset_id}/classes/{class_id}/profile")
def class_profile(
    dataset_id: str,
    class_id: str,
    db: DbSession,
    scope: Scope,
    node_id: str | None = Query(default=None, max_length=64),
    analysis_version: str | None = Query(default=None, max_length=128),
) -> dict:
    scope.require("view_class_analysis")
    dataset = _dataset(db, dataset_id)
    _require_scope(db, scope, dataset, class_id=class_id)
    run_id, node = resolve_run(db, dataset_id, node_id, analysis_version)
    version = _class_version(db, dataset, run_id, node, class_id)
    return success(
        {
            "result": _class_view(version.payload_json or {}),
            "freshness": "current",
            "limitations": (version.payload_json or {}).get("limitations", []),
        }
    )


@router.get("/runs/{run_id}/students/{student_id}")
def legacy_student_result(
    run_id: str,
    student_id: str,
    db: DbSession,
    scope: Scope,
    node_id: str = Query(min_length=1, max_length=64),
    analysis_version: str | None = Query(default=None, max_length=128),
) -> dict:
    run = db.get(models.AnalysisRun, run_id)
    if run is None:
        raise DomainError("NOT_FOUND", "分析运行不存在")
    dataset = _dataset(db, run.dataset_id)
    _require_scope(db, scope, dataset, student=student_id)
    version = _student_version(db, dataset, run_id, node_id, student_id)
    return success(_student_view(version.payload_json or {}))


@router.get("/runs/{run_id}/classes/compare")
def class_compare(
    run_id: str,
    db: DbSession,
    scope: Scope,
    node_id: str = Query(min_length=1, max_length=64),
    left_class_id: str = Query(min_length=1, max_length=64),
    right_class_id: str = Query(min_length=1, max_length=64),
    indicator_id: str | None = Query(default=None, max_length=64),
    kp_id: str | None = Query(default=None, max_length=64),
) -> dict:
    scope.require("view_class_analysis")
    run = db.get(models.AnalysisRun, run_id)
    if run is None:
        raise DomainError("NOT_FOUND", "分析运行不存在")
    dataset = _dataset(db, run.dataset_id)
    if scope.dataset_id is not None and scope.dataset_id != dataset.id:
        raise DomainError("FORBIDDEN", "当前会话无权访问该数据集")
    if left_class_id == right_class_id:
        raise DomainError("UNPROCESSABLE", "请选择两个不同的班级")
    for class_id in (left_class_id, right_class_id):
        if not scope.can_read_class(class_id):
            raise DomainError("FORBIDDEN", "当前会话未获授权该班级")
    left = ClassResult.model_validate(_class_version(db, dataset, run_id, node_id, left_class_id).payload_json or {})
    right = ClassResult.model_validate(_class_version(db, dataset, run_id, node_id, right_class_id).payload_json or {})

    left_metrics = {(metric.scope.indicator_id, metric.scope.kp_id, metric.unit): metric for metric in left.metrics}
    right_metrics = {(metric.scope.indicator_id, metric.scope.kp_id, metric.unit): metric for metric in right.metrics}
    items: list[ComparisonItem] = []
    limitations: list[str] = []
    comparable = bool(left_metrics) and bool(right_metrics)
    for scope_key, metric in left_metrics.items():
        other = right_metrics.get(scope_key)
        indicator_key = scope_key[0]
        if indicator_id and indicator_key != indicator_id:
            continue
        if kp_id and scope_key[1] != kp_id:
            continue
        if other is None:
            comparable = False
            limitations.append(f"右班缺少同口径指标 {indicator_key}")
            continue
        delta = None
        if metric.value is not None and other.value is not None:
            delta = round(metric.value - other.value, 6)
        items.append(
            ComparisonItem(
                indicator_id=indicator_key or "",
                left_value=metric.value,
                right_value=other.value,
                delta=delta,
                left_numerator=metric.numerator,
                left_denominator=metric.denominator,
                right_numerator=other.numerator,
                right_denominator=other.denominator,
                limitations=metric.limitations + other.limitations,
            )
        )
    view = ClassComparisonView(
        class_ids=[left_class_id, right_class_id],
        node_id=node_id,
        member_analysis_versions=[
            {"class_id": left_class_id, "analysis_version": left.analysis_version},
            {"class_id": right_class_id, "analysis_version": right.analysis_version},
        ],
        comparable=comparable,
        items=items,
        limitations=limitations,
    )
    return success(view.model_dump(mode="json"))


@router.get("/runs/{run_id}/classes/{class_id}")
def legacy_class_result(
    run_id: str,
    class_id: str,
    db: DbSession,
    scope: Scope,
    node_id: str = Query(min_length=1, max_length=64),
) -> dict:
    scope.require("view_class_analysis")
    run = db.get(models.AnalysisRun, run_id)
    if run is None:
        raise DomainError("NOT_FOUND", "分析运行不存在")
    dataset = _dataset(db, run.dataset_id)
    _require_scope(db, scope, dataset, class_id=class_id)
    version = _class_version(db, dataset, run_id, node_id, class_id)
    return success(_class_view(version.payload_json or {}))


# --------------------------------------------------------------------------- #
# 学习方案 / 进展
# --------------------------------------------------------------------------- #


def plan_for_student(
    db: DbSession,
    dataset: models.Dataset,
    student_id: str,
    *,
    node_id: str | None,
    plan_version: str | None,
) -> models.Version:
    if plan_version:
        version = db.get(models.Version, plan_version)
        if version is None or version.kind != "plan" or version.dataset_id != dataset.id:
            raise DomainError("NOT_FOUND", "学习方案版本不存在")
        if version.target_key != student_id:
            raise DomainError("FORBIDDEN", "该方案不属于此学生")
        return version
    # 未指定节点时按当前运行指针所在节点解析，避免误取其它测验节点的方案。
    if node_id is None:
        try:
            _default_run, node_id = resolve_run(db, dataset.id, None)
        except DomainError:
            node_id = None
    if node_id:
        key = repo.FormalKey(
            family_id=dataset.family_id or "",
            dataset_id=dataset.id,
            kind="plan",
            node_key=node_id,
            target_key=student_id,
        )
        version = repo.read_formal(db, key)
        if version is not None:
            return version
    # 没指定节点时取该学生最近发布的方案指针（不猜节点，只按发布时间取最新）。
    # 没指定节点时优先取"有可执行活动"的最近方案，避免落到空方案节点。
    heads = db.scalars(
        select(models.FormalHead)
        .where(
            models.FormalHead.dataset_id == dataset.id,
            models.FormalHead.kind == "plan",
            models.FormalHead.target_key == student_id,
            models.FormalHead.version_id.is_not(None),
        )
        .order_by(models.FormalHead.updated_at.desc())
    ).all()
    fallback_version: models.Version | None = None
    for head in heads:
        if not head.version_id:
            continue
        candidate = db.get(models.Version, head.version_id)
        if candidate is None or not candidate.payload_json:
            continue
        if fallback_version is None:
            fallback_version = candidate
        payload = candidate.payload_json or {}
        if any(item.get("activities") for item in payload.get("recommendations", []) or []):
            return candidate
    if fallback_version is not None:
        return fallback_version
    raise DomainError("RESULT_NOT_READY", "该学生尚无正式学习方案")


def _overlay_live_state(
    db: DbSession, plan: RecommendationBundle, student_uid: str
) -> RecommendationBundle:
    """用真实活动状态覆盖方案快照里的 state。

    方案正文不可变（发布时的快照），但学生看到的"进行中/已完成"必须来自
    ``activity_state``，否则页面会一直显示未开始。
    """

    rows = db.scalars(
        select(models.ActivityStateRow).where(models.ActivityStateRow.student_uid == student_uid)
    ).all()
    states = {row.activity_id: row.state for row in rows}
    if not states:
        return plan
    recommendations = [
        rec.model_copy(
            update={
                "activities": [
                    activity.model_copy(
                        update={"state": states.get(activity.activity_id, activity.state)}
                    )
                    for activity in rec.activities
                ]
            }
        )
        for rec in plan.recommendations
    ]
    return plan.model_copy(update={"recommendations": recommendations})


@router.get("/datasets/{dataset_id}/students/{student_id}/plan")
def student_plan(
    dataset_id: str,
    student_id: str,
    db: DbSession,
    scope: Scope,
    node_id: str | None = Query(default=None, max_length=64),
    plan_version: str | None = Query(default=None, max_length=128),
) -> dict:
    if scope.role == "student":
        scope.require("view_own_plan")
    else:
        scope.require("view_student_analysis")
    dataset = _dataset(db, dataset_id)
    _require_scope(db, scope, dataset, student=student_id)
    version = plan_for_student(
        db, dataset, student_id, node_id=node_id, plan_version=plan_version
    )
    payload = version.payload_json or {}
    plan = RecommendationBundle.model_validate(payload)
    student_uid = _student_uid(dataset, db, student_id)
    if student_uid:
        plan = _overlay_live_state(db, plan, student_uid)
    applicability = db.scalar(
        select(models.PlanApplicability)
        .where(models.PlanApplicability.plan_version == version.version_id)
        .order_by(models.PlanApplicability.checked_at.desc())
        .limit(1)
    )
    return success(
        {
            "plan": plan.model_dump(mode="json"),
            "plan_version": version.version_id,
            "freshness": "current",
            "limitations": plan.limitations,
            "analysis_version": plan.analysis_version,
            "applicability": None
            if applicability is None
            else {
                "applicability_id": applicability.applicability_id,
                "plan_version": applicability.plan_version,
                "origin_analysis_version": plan.analysis_version,
                "current_analysis_version": applicability.target_analysis_version,
                "checked_at": applicability.checked_at,
                "event_cutoff": applicability.event_cutoff,
                "result": applicability.result,
                "activity_ids": applicability.activity_ids_json,
                "evidence_refs": applicability.evidence_refs_json,
                "limitations": applicability.limitations_json,
            },
        }
    )


@router.get("/runs/{run_id}/students/{student_id}/recommendations")
def legacy_recommendations(
    run_id: str,
    student_id: str,
    db: DbSession,
    scope: Scope,
    node_id: str = Query(min_length=1, max_length=64),
) -> dict:
    run = db.get(models.AnalysisRun, run_id)
    if run is None:
        raise DomainError("NOT_FOUND", "分析运行不存在")
    dataset = _dataset(db, run.dataset_id)
    _require_scope(db, scope, dataset, student=student_id)
    version = plan_for_student(db, dataset, student_id, node_id=node_id, plan_version=None)
    payload = version.payload_json or {}
    return success(payload)


@router.get("/datasets/{dataset_id}/students/{student_id}/progress")
def student_progress(
    dataset_id: str,
    student_id: str,
    db: DbSession,
    scope: Scope,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> dict:
    if scope.role == "student":
        scope.require("view_own_analysis")
    else:
        scope.require("view_student_analysis")
    dataset = _dataset(db, dataset_id)
    _require_scope(db, scope, dataset, student=student_id)
    rows = db.scalars(
        select(models.Version)
        .where(
            models.Version.dataset_id == dataset_id,
            models.Version.kind == "student_analysis",
            models.Version.target_key == student_id,
        )
        .order_by(models.Version.created_at)
    ).all()
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for version in reversed(rows):
        payload = version.payload_json or {}
        for item in payload.get("progress", []):
            key = item.get("comparison_id")
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
    start = (page - 1) * page_size
    return success(
        {"items": items[start : start + page_size], "total": len(items), "page": page, "page_size": page_size}
    )


# --------------------------------------------------------------------------- #
# 作答记录与质量
# --------------------------------------------------------------------------- #


@router.get("/runs/{run_id}/students/{student_id}/answer-records")
def answer_records(
    run_id: str,
    student_id: str,
    db: DbSession,
    scope: Scope,
    node_id: str = Query(min_length=1, max_length=64),
    analysis_version: str | None = Query(default=None, max_length=128),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> dict:
    scope.require("view_student_analysis")
    run = db.get(models.AnalysisRun, run_id)
    if run is None:
        raise DomainError("NOT_FOUND", "分析运行不存在")
    dataset = _dataset(db, run.dataset_id)
    _require_scope(db, scope, dataset, student=student_id)

    events = db.scalars(
        select(models.AnswerEvent).where(
            models.AnswerEvent.dataset_id == dataset.id,
            models.AnswerEvent.student_id == student_id,
            models.AnswerEvent.node_id == node_id,
        )
    ).all()
    items: list[AnswerRecordListItem] = []
    for event in events:
        payload = dict(event.payload_json or {})
        record_id = payload.get("selected_record_id")
        if record_id is None:
            continue
        status = payload.get("status", "pending")
        items.append(
            AnswerRecordListItem(
                record_key=f"official:{record_id}",
                origin="official",
                node_id=node_id,
                material_id=event.material_id,
                record_id=record_id,
                grading_status=status,
                score=payload.get("score"),
                max_score=payload.get("max_score"),
                occurred_at=payload.get("submitted_at"),
                evidence_ref=EvidenceRef(kind="record", id=record_id, run_id=run_id),
            )
        )
    attempts = db.scalars(
        select(models.AttemptRow).where(
            models.AttemptRow.student_uid == _student_uid(dataset, db, student_id),
            models.AttemptRow.status.in_(("submitted", "completed")),
        )
    ).all()
    for attempt in attempts:
        grade_row = db.get(models.Grade, attempt.latest_grade_id) if attempt.latest_grade_id else None
        items.append(
            AnswerRecordListItem(
                record_key=f"online:{attempt.attempt_id}",
                origin="online",
                node_id=node_id,
                material_id=attempt.material_id,
                attempt_id=attempt.attempt_id,
                grade_id=grade_row.grade_id if grade_row else None,
                grading_status=(grade_row.status if grade_row else "pending"),
                score=grade_row.score if grade_row else None,
                max_score=grade_row.max_score if grade_row else None,
                occurred_at=attempt.submitted_at,
                evidence_ref=EvidenceRef(
                    kind="attempt", id=attempt.attempt_id, dataset_id=dataset.id
                ),
            )
        )
    items.sort(key=lambda item: (item.occurred_at or "", item.record_key))
    start = (page - 1) * page_size
    return success(
        {
            "items": [item.model_dump(mode="json") for item in items[start : start + page_size]],
            "total": len(items),
            "page": page,
            "page_size": page_size,
        }
    )


def _student_uid(dataset: models.Dataset, db: DbSession, student_id: str) -> str:
    row = db.scalar(
        select(models.StudentIdentity).where(
            models.StudentIdentity.family_id == dataset.family_id,
            models.StudentIdentity.external_id == student_id,
        )
    )
    return row.student_uid if row else ""


@router.get("/runs/{run_id}/quality/analysis")
def analysis_quality(
    run_id: str,
    db: DbSession,
    scope: Scope,
    node_id: str = Query(min_length=1, max_length=64),
    class_id: str | None = Query(default=None, max_length=64),
) -> dict:
    scope.require("view_quality")
    run = db.get(models.AnalysisRun, run_id)
    if run is None:
        raise DomainError("NOT_FOUND", "分析运行不存在")
    dataset = _dataset(db, run.dataset_id)
    if scope.dataset_id is not None and scope.dataset_id != dataset.id:
        raise DomainError("FORBIDDEN", "当前会话无权访问该数据集")
    issues = db.scalars(
        select(models.QualityIssue).where(models.QualityIssue.dataset_id == dataset.id)
    ).all()
    payloads = [dict(row.payload_json or {}) for row in issues]
    counts: dict[str, int] = {}
    for item in payloads:
        code = str(item.get("code", "UNKNOWN"))
        counts[code] = counts.get(code, 0) + 1

    from study_companion.analysis.quality import build_quality_report
    from study_companion.library.datasets import load_catalog, load_prepared_dataset

    results = db.scalars(
        select(models.AnalysisResult).where(models.AnalysisResult.run_id == run_id)
    ).all()
    student_results = [
        StudentResult.model_validate(row.payload_json)
        for row in results
        if row.subject_type == "student" and row.node_id == node_id
    ]
    class_results = [
        ClassResult.model_validate(row.payload_json)
        for row in results
        if row.subject_type == "class" and row.node_id == node_id
    ]
    prepared = load_prepared_dataset(db, dataset.id)
    catalog = load_catalog(db, run.catalog_version, dataset.id)
    bundle = AnalysisBundle(
        run_id=run_id,
        dataset_id=dataset.id,
        catalog_version=run.catalog_version,
        rule_version=run.rule_version,
        config=AnalysisConfig(rule_version=run.rule_version),
        student_results=student_results,
        class_results=class_results,
        quality_summary=counts,
        knowledge_version=run.knowledge_version,
    )
    report = build_quality_report(
        dataset_id=dataset.id,
        node_id=node_id,
        prepared=prepared,
        bundle=bundle,
        catalog=catalog,
        rule_version=run.rule_version,
        generated_at=repo.now_iso(),
    )
    data = report.model_dump(mode="json")
    data["scope"] = {**data.get("scope", {}), "class_id": class_id}
    return success(data)


@router.get("/datasets/{dataset_id}/alerts")
def list_alerts(
    dataset_id: str,
    db: DbSession,
    scope: Scope,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> dict:
    scope.require("view_class_analysis")
    dataset = _dataset(db, dataset_id)
    if scope.dataset_id is not None and scope.dataset_id != dataset.id:
        raise DomainError("FORBIDDEN", "当前会话无权访问该数据集")
    items: list[dict[str, Any]] = []
    reviews = db.scalars(
        select(models.Review)
        .where(models.Review.status == "needs_review")
        .order_by(models.Review.created_at.desc())
        .limit(50)
    ).all()
    for row in reviews:
        items.append(
            {
                "alert_id": row.review_id,
                "kind": "plan_review" if row.subject_kind == "plan" else "knowledge_review",
                "status": "open",
                "summary": f"待审核对象：{row.subject_key}",
                "target_ref": {"review_id": row.review_id},
                "student_id": None,
                "evidence_refs": [],
                "created_at": row.created_at,
            }
        )
    cases = db.scalars(
        select(models.InterventionRow)
        .where(models.InterventionRow.status == "open")
        .order_by(models.InterventionRow.opened_at.desc())
        .limit(50)
    ).all()
    for row in cases:
        identity = db.get(models.StudentIdentity, row.student_uid)
        items.append(
            {
                "alert_id": row.case_id,
                "kind": "teacher_attention",
                "status": "open",
                "summary": str((row.payload_json or {}).get("reason", "需要教师关注")),
                "target_ref": {"case_id": row.case_id},
                "student_id": identity.external_id if identity else None,
                "evidence_refs": (row.payload_json or {}).get("evidence_refs", []),
                "created_at": row.opened_at,
            }
        )
    start = (page - 1) * page_size
    return success(
        {"items": items[start : start + page_size], "total": len(items), "page": page, "page_size": page_size}
    )

@router.post("/datasets/{dataset_id}/students/{student_id}/analysis/rerun", status_code=202)
def rerun_student_analysis(
    dataset_id: str,
    student_id: str,
    db: DbSession,
    scope: Scope,
    node_id: str = Query(default="", max_length=64),
) -> dict:
    """重新运行一个学生的 B→C，用于失败重试或数据修订后的重算。

    只创建新的任务，不删除上一次结果；正式版本仍由发布门控制。
    """

    scope.require("view_student_analysis")
    scope.require_teacher()
    from study_companion.jobs.service import enqueue
    from study_companion.jobs.worker import ensure_worker_started

    node_ids = [node_id] if node_id else []
    if not node_ids:
        run = db.scalar(
            select(models.AnalysisRun)
            .where(
                models.AnalysisRun.dataset_id == dataset_id,
                models.AnalysisRun.status == "succeeded",
            )
            .order_by(models.AnalysisRun.created_at.desc())
            .limit(1)
        )
        node_ids = list((run.node_ids_json if run else []) or [])
    if not node_ids:
        raise DomainError("RESULT_NOT_READY", "该数据集还没有可用的观察节点")
    job = enqueue(
        db,
        kind="student_update",
        scope={
            "dataset_id": dataset_id,
            "student_id": student_id,
            "node_id": node_ids[0],
        },
        payload={"reason": "manual_rerun", "nonce": repo.new_id("rerun")},
        dataset_id=dataset_id,
        student_id=student_id,
        requested_by=scope.session_id,
    )
    db.commit()
    ensure_worker_started()
    return success({"job_id": job.id, "status": job.status, "node_id": node_ids[0]})

@router.post("/datasets/{dataset_id}/students/{student_id}/lessons/regenerate", status_code=202)
def regenerate_student_lessons(
    dataset_id: str,
    student_id: str,
    db: DbSession,
    scope: Scope,
    node_id: str = Query(default="", max_length=64),
) -> dict:
    """按当前正式方案重新生成微课堂；旧微课堂被替换，不产生重复活动。"""

    scope.require("view_student_analysis")
    scope.require_teacher()
    from study_companion.jobs.service import enqueue
    from study_companion.jobs.worker import ensure_worker_started

    nodes = [node_id] if node_id else []
    if not nodes:
        head = db.scalar(
            select(models.Version)
            .where(
                models.Version.dataset_id == dataset_id,
                models.Version.kind == "plan",
                models.Version.target_key == student_id,
            )
            .order_by(models.Version.created_at.desc())
            .limit(1)
        )
        if head is not None and head.node_key:
            nodes = [head.node_key]
    if not nodes:
        raise DomainError("RESULT_NOT_READY", "该学生没有可用的观察节点")
    job = enqueue(
        db,
        kind="lesson_regenerate",
        scope={"dataset_id": dataset_id, "student_id": student_id, "node_id": nodes[0]},
        payload={"reason": "manual_regenerate"},
        dataset_id=dataset_id,
        student_id=student_id,
        requested_by=scope.session_id,
    )
    db.commit()
    ensure_worker_started()
    return success({"job_id": job.id, "status": job.status, "node_id": nodes[0]})

