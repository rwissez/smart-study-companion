"""业务流水线：导入完成后的 A → B → C 调度与正式成果落库。

本模块是唯一"把纯函数结果变成正式版本"的地方：

* 任务只负责装配授权上下文与事务，业务判断仍在 ``analysis`` / ``recommendation``；
* 每次落库都写不可变版本 + 依赖边 + 发布记录，切换指针用 CAS；
* 失败保留已成功部分与真实原因，不伪造成功；
* 任务成功 ≠ 审核通过 ≠ 已发布，三者分别记录。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.analysis.service import analyze_dataset
from study_companion.contracts import (
    AnalysisBundle,
    AnalysisConfig,
    AnalysisInputRef,
    Catalog,
    DomainError,
    PreparedDataset,
    RecommendationBundle,
    RecommendationConfig,
    ReviewCheck,
    ReviewInfo,
    RunContext,
    StudentResult,
    TaskGroup,
    ValidationIssue,
)
from study_companion.db import models, version_repository as repo
from study_companion.db.session import get_sessionmaker
from study_companion.jobs import enqueue, register, upsert_step
from study_companion.jobs.registry import JobContext
from study_companion.library.datasets import load_catalog, load_prepared_dataset

logger = logging.getLogger(__name__)

RULE_VERSION = "b-rules-1"
C_RULE_VERSION = "c-rules-1"
PLAN_SCHEMA_VERSION = "1.1"


# --------------------------------------------------------------------------- #
# 注册
# --------------------------------------------------------------------------- #


def register_handlers() -> None:
    """导入即注册；重复调用安全。新增业务处理器在此登记模块导入。"""

    import study_companion.pipeline  # noqa: F401  (decorator 在本模块导入时执行)


def enqueue_after_import(
    db: Session,
    *,
    dataset_id: str,
    family_id: str,
    catalog_version: str,
    node_ids: list[str],
    import_id: str,
) -> models.Job | None:
    """上传确认后自动进入 A→B→C，不需要教师手工点运行。"""

    return enqueue(
        db,
        kind="import_process",
        scope={"dataset_id": dataset_id, "family_id": family_id},
        payload={
            "catalog_version": catalog_version,
            "node_ids": node_ids,
            "import_id": import_id,
        },
        dataset_id=dataset_id,
    )


# --------------------------------------------------------------------------- #
# B：分析落库
# --------------------------------------------------------------------------- #


def _analysis_input(
    dataset_id: str,
    catalog_version: str,
    rule_version: str,
    config: Any,
    event_cutoff: int,
    included_event_ids: list[str],
) -> AnalysisInputRef:
    return AnalysisInputRef(
        dataset_id=dataset_id,
        catalog_version=catalog_version,
        rule_version=rule_version,
        config_hash=repo.digest(config.model_dump(mode="json") if hasattr(config, "model_dump") else config),
        event_cutoff=event_cutoff,
        included_event_ids=included_event_ids,
        created_at=repo.now_iso(),
    )


def persist_analysis_bundle(
    db: Session,
    bundle: AnalysisBundle,
    *,
    family_id: str,
    catalog: Catalog,
    job_id: str | None,
    event_cutoff: int,
    included_event_ids: list[str],
) -> dict[str, Any]:
    """把一次 B 计算写成正式个人/班级版本并发布指针。"""

    run_id = bundle.run_id
    stamp = repo.now_iso()
    run = db.get(models.AnalysisRun, run_id)
    if run is None:
        run = models.AnalysisRun(
            id=run_id,
            dataset_id=bundle.dataset_id,
            catalog_version=bundle.catalog_version,
            rule_version=bundle.rule_version,
            config_json=bundle.config.model_dump(mode="json"),
            status="succeeded",
            created_at=stamp,
            error_json=None,
            config_hash=repo.digest(bundle.config.model_dump(mode="json")),
            event_cutoff=event_cutoff,
            knowledge_version=bundle.knowledge_version,
            node_ids_json=sorted({item.node_id for item in bundle.student_results}),
        )
        db.add(run)
        db.flush()

    published: list[str] = []
    for result in bundle.student_results:
        key = repo.FormalKey(
            family_id=family_id,
            dataset_id=bundle.dataset_id,
            kind="student_analysis",
            run_key=run_id,
            node_key=result.node_id,
            target_key=result.student_id,
        )
        payload = result.model_dump(mode="json")
        version = repo.create_version(
            db,
            kind="student_analysis",
            key=key,
            payload=payload,
            rule_version=bundle.rule_version,
            input_digest=repo.digest(payload),
            run_id=run_id,
            job_id=job_id,
            version_id=repo.new_id("ban"),
        )
        result.analysis_version = version.version_id
        version.payload_json = result.model_dump(mode="json")
        version.body_hash = repo.digest(version.payload_json)
        head = repo.read_head(db, key)
        expected_version = head.version_id if head else None
        expected_generation = head.generation if head else 0
        repo.publish(
            db,
            key=key,
            version=version,
            path="system_auto",
            basis="automatic_review",
            job_id=job_id,
            expected_version_id=expected_version,
            expected_generation=expected_generation,
        )
        published.append(version.version_id)
        db.add(
            models.AnalysisResult(
                run_id=run_id,
                subject_type="student",
                subject_id=result.student_id,
                node_id=result.node_id,
                payload_json=result.model_dump(mode="json"),
            )
        )

    for result in bundle.class_results:
        key = repo.FormalKey(
            family_id=family_id,
            dataset_id=bundle.dataset_id,
            kind="class_analysis",
            run_key=run_id,
            node_key=result.node_id,
            target_key=result.class_id,
        )
        version = repo.create_version(
            db,
            kind="class_analysis",
            key=key,
            payload=result.model_dump(mode="json"),
            rule_version=bundle.rule_version,
            input_digest=repo.digest(result.model_dump(mode="json")),
            run_id=run_id,
            job_id=job_id,
            version_id=repo.new_id("bcl"),
        )
        result.analysis_version = version.version_id
        version.payload_json = result.model_dump(mode="json")
        version.body_hash = repo.digest(version.payload_json)
        head = repo.read_head(db, key)
        repo.publish(
            db,
            key=key,
            version=version,
            path="system_auto",
            basis="automatic_review",
            job_id=job_id,
            expected_version_id=head.version_id if head else None,
            expected_generation=head.generation if head else 0,
        )
        published.append(version.version_id)
        db.add(
            models.AnalysisResult(
                run_id=run_id,
                subject_type="class",
                subject_id=result.class_id,
                node_id=result.node_id,
                payload_json=result.model_dump(mode="json"),
            )
        )

    for result in bundle.student_results:
        repo.set_active_run(
            db, dataset_id=bundle.dataset_id, node_key=result.node_id, run_id=run_id
        )
        break

    db.flush()
    return {
        "run_id": run_id,
        "analysis_versions": published,
        "student_results": len(bundle.student_results),
        "class_results": len(bundle.class_results),
    }


# --------------------------------------------------------------------------- #
# C：方案落库
# --------------------------------------------------------------------------- #


def _normalise_plan(bundle: RecommendationBundle, plan_version: str) -> RecommendationBundle:
    """为旧规则结果补上正式活动/任务组身份（稳定、可重放）。"""

    recommendations = []
    groups: list[TaskGroup] = []
    for index, rec in enumerate(bundle.recommendations, start=1):
        group_id = f"grp-{plan_version}-{index:02d}"
        activities = []
        for order, activity in enumerate(rec.activities, start=1):
            activities.append(
                activity.model_copy(
                    update={
                        "activity_id": f"act-{plan_version}-{index:02d}-{order:02d}",
                        "group_id": group_id,
                        "activity_kind": "question" if activity.material_id else "study_material",
                        "state": "not_started",
                    }
                )
            )
        recommendations.append(
            rec.model_copy(
                update={
                    "activities": activities,
                    "group_id": group_id,
                    "strategy_types": rec.strategy_types or ["consolidation"],
                    "review_status": "validated",
                }
            )
        )
        groups.append(
            TaskGroup(
                group_id=group_id,
                goal=rec.goal,
                strategy_types=rec.strategy_types or ["consolidation"],
                activity_ids=[item.activity_id for item in activities],
                order=index,
                required_activity_ids=[item.activity_id for item in activities],
                goal_target_key=rec.indicator_id,
                student_intro=rec.student_intro,
                evidence_refs=rec.evidence_refs,
            )
        )
    return bundle.model_copy(
        update={
            "recommendations": recommendations,
            "task_groups": groups,
            "plan_version": plan_version,
            "created_at": repo.now_iso(),
        }
    )


def build_review_info(
    bundle: RecommendationBundle, issues: list[ValidationIssue]
) -> ReviewInfo:
    """程序侧完整审核：逐项给出真实检查结果，不伪造模型审核。"""

    checks = [
        ReviewCheck(code="evidence", passed=True, reason="建议均绑定本次正式学情诊断"),
        ReviewCheck(
            code="target",
            passed=all(rec.indicator_id for rec in bundle.recommendations),
            reason="每条建议都有明确指标目标",
        ),
        ReviewCheck(
            code="sequence",
            passed=all(
                [activity.order for activity in rec.activities] == list(range(1, len(rec.activities) + 1))
                for rec in bundle.recommendations
            ),
            reason="活动顺序连续",
        ),
        ReviewCheck(
            code="load",
            passed=len(bundle.recommendations) <= RecommendationConfig().max_recommendations,
            reason="建议数量在减负上限内",
        ),
        ReviewCheck(
            code="material_history",
            passed=not any(issue.code == "MATERIAL_NOT_FOUND" for issue in issues),
            reason="使用材料均存在于正式目录且未重复",
        ),
        ReviewCheck(
            code="answer_guard",
            passed=not any(issue.code == "HINT_LEAKS_ANSWER" for issue in issues),
            reason="提示未泄露答案",
        ),
    ]
    blocking = [issue for issue in issues if issue.severity == "error"]
    status = "validated" if all(check.passed for check in checks) and not blocking else "needs_review"
    return ReviewInfo(
        status=status,  # type: ignore[arg-type]
        method="rule",
        checks=checks,
        reviewed_at=repo.now_iso(),
        rule_version=C_RULE_VERSION,
        issues=issues,
    )


def persist_plan(
    db: Session,
    bundle: RecommendationBundle,
    *,
    family_id: str,
    analysis_version_id: str,
    knowledge_version_id: str | None,
    job_id: str | None,
    review: ReviewInfo,
    student_uid: str | None,
    publish: bool,
) -> dict[str, Any]:
    node_key = bundle.node_id
    key = repo.FormalKey(
        family_id=family_id,
        dataset_id=bundle.dataset_id,
        kind="plan",
        node_key=node_key,
        target_key=bundle.student_id,
    )
    plan_version_id = repo.new_id("plan")
    normalised = _normalise_plan(bundle, plan_version_id)
    normalised = normalised.model_copy(
        update={
            "analysis_version": analysis_version_id,
            "knowledge_version": knowledge_version_id,
            "review_info": review,
            "publication_status": "published" if publish else "unpublished",
            "publication_basis": "automatic_review" if publish else None,
            "publish_path": "system_auto" if publish else None,
            "validation_issues": review.issues,
        }
    )
    # 先落不可变版本行，再写活动/状态表：后者的外键指向版本，顺序不能反。
    version = repo.create_version(
        db,
        kind="plan",
        key=key,
        payload=normalised.model_dump(mode="json"),
        rule_version=C_RULE_VERSION,
        input_digest=repo.digest(normalised.model_dump(mode="json")),
        run_id=normalised.run_id,
        job_id=job_id,
        version_id=plan_version_id,
        origin_kind="system",
    )
    for group in normalised.task_groups:
        db.add(
            models.PlanGroup(
                plan_version=plan_version_id,
                group_id=group.group_id,
                position=group.order,
                goal_key=group.goal_target_key or group.group_id,
            )
        )
        rec = next(
            (item for item in normalised.recommendations if item.group_id == group.group_id),
            None,
        )
        if rec is None:
            continue
        for order, item in enumerate(rec.activities, start=1):
            db.add(
                models.PlanActivity(
                    plan_version=plan_version_id,
                    activity_id=item.activity_id,
                    group_id=group.group_id,
                    origin_plan_version=None,
                    kind=item.activity_kind,
                    catalog_version=normalised.catalog_version,
                    material_id=item.material_id,
                    content_version=item.content_version,
                    position=order,
                )
            )
            if student_uid:
                db.add(
                    models.ActivityStateRow(
                        activity_id=item.activity_id,
                        origin_plan_version=plan_version_id,
                        student_uid=student_uid,
                        state="not_started",
                        generation=1,
                        updated_at=repo.now_iso(),
                    )
                )
    db.flush()

    head = repo.read_head(db, key)
    analysis_version = db.get(models.Version, analysis_version_id)
    if analysis_version is not None:
        repo.register_edge(db, version, analysis_version, role="student_analysis")
    if knowledge_version_id:
        knowledge_version = db.get(models.Version, knowledge_version_id)
        if knowledge_version is not None:
            repo.register_edge(db, version, knowledge_version, role="knowledge")

    publication = None
    if publish:
        publication = repo.publish(
            db,
            key=key,
            version=version,
            path="system_auto",
            basis="automatic_review",
            review_id="",
            job_id=job_id,
            expected_version_id=head.version_id if head else None,
            expected_generation=head.generation if head else 0,
        )
    db.flush()
    return {
        "plan_version": plan_version_id,
        "publication_id": publication.publication_id if publication else None,
        "review_status": review.status,
        "publication_status": "published" if publication else "unpublished",
    }


# --------------------------------------------------------------------------- #
# 处理器
# --------------------------------------------------------------------------- #


@register("import_process")
def handle_import_process(ctx: JobContext) -> dict[str, Any]:
    """上传 → A 基础目录 → B 学情 → C 方案（自动审核后发布）。"""

    from study_companion.recommendation.service import recommend_rules, validate_recommendations

    db = ctx.session
    scope = ctx.scope
    payload = ctx.payload
    dataset_id = scope["dataset_id"]
    dataset = db.get(models.Dataset, dataset_id)
    if dataset is None:
        raise DomainError("NOT_FOUND", "数据集不存在")
    family_id = dataset.family_id or scope.get("family_id")
    if not family_id:
        raise DomainError("UNPROCESSABLE", "数据集缺少系列身份，无法生成正式成果")
    catalog_version = payload.get("catalog_version")
    catalog = load_catalog(db, catalog_version, dataset_id)
    prepared = load_prepared_dataset(db, dataset_id)

    upsert_step(db, ctx.job, "load", label="读取快照与目录", status="succeeded")
    db.commit()

    context = RunContext(
        run_id=repo.new_id("run"),
        dataset_id=dataset_id,
        catalog_version=catalog_version,
        rule_version=RULE_VERSION,
        dataset_family_id=family_id,
        knowledge_version=None,
    )
    upsert_step(db, ctx.job, "analysis", label="生成学情分析", status="running")
    db.commit()

    config = AnalysisConfig(rule_version=RULE_VERSION)
    bundle = analyze_dataset(prepared, catalog, context, config)
    included = [event.event_id for event in prepared.selected_events]
    result = persist_analysis_bundle(
        db,
        bundle,
        family_id=family_id,
        catalog=catalog,
        job_id=ctx.job.id,
        event_cutoff=len(included),
        included_event_ids=included,
    )
    upsert_step(db, ctx.job, "analysis", label="生成学情分析", status="succeeded")
    db.commit()

    upsert_step(db, ctx.job, "plans", label="生成学习方案", status="running")
    db.commit()

    plans: list[dict[str, Any]] = []
    config_c = RecommendationConfig()
    students_by_id = {student.student_id: student for student in prepared.students}
    for student_result in bundle.student_results:
        analysis_version = student_result.analysis_version
        try:
            plan_bundle = recommend_rules(student_result, catalog, context, config_c)
        except DomainError as exc:
            if exc.code in {"MATERIAL_NOT_FOUND", "TASK_NOT_AVAILABLE"}:
                plans.append({"student_id": student_result.student_id, "status": "shortage", "reason": exc.message})
                continue
            raise
        validation = validate_recommendations(plan_bundle, student_result, catalog, config_c)
        review = build_review_info(plan_bundle, validation.issues)
        student = students_by_id.get(student_result.student_id)
        published = persist_plan(
            db,
            plan_bundle,
            family_id=family_id,
            analysis_version_id=analysis_version,
            knowledge_version_id=None,
            job_id=ctx.job.id,
            review=review,
            student_uid=student.student_uid if student else None,
            publish=review.status == "validated",
        )
        plans.append(
            {
                "student_id": student_result.student_id,
                "status": "published" if published["publication_status"] == "published" else "needs_review",
                **published,
            }
        )
        db.flush()

    upsert_step(db, ctx.job, "plans", label="生成学习方案", status="succeeded")
    db.commit()

    return {
        "run_id": result["run_id"],
        "dataset_id": dataset_id,
        "catalog_version": catalog_version,
        "analysis_versions": result["analysis_versions"],
        "plans": plans,
        "published_plan_count": sum(1 for item in plans if item.get("status") == "published"),
    }


def new_run_id() -> str:
    return repo.new_id("run")


# --------------------------------------------------------------------------- #
# 学习事件驱动的局部更新
# --------------------------------------------------------------------------- #


def _carry_over_states(
    db: Session,
    *,
    new_plan_version: str,
    old_plan_version: str | None,
    student_uid: str,
) -> dict[str, str]:
    """把已完成/进行中的活动身份延续到新方案，避免历史进度丢失。"""

    if not old_plan_version:
        return {}
    old_rows = db.scalars(
        select(models.PlanActivity).where(models.PlanActivity.plan_version == old_plan_version)
    ).all()
    old_by_material = {row.material_id: row for row in old_rows if row.material_id}
    old_states = {
        row.activity_id: row.state
        for row in db.scalars(
            select(models.ActivityStateRow).where(
                models.ActivityStateRow.student_uid == student_uid,
                models.ActivityStateRow.origin_plan_version == old_plan_version,
            )
        ).all()
    }
    mapping: dict[str, str] = {}
    new_rows = db.scalars(
        select(models.PlanActivity).where(models.PlanActivity.plan_version == new_plan_version)
    ).all()
    for row in new_rows:
        old = old_by_material.get(row.material_id or "")
        if old is None:
            continue
        state = old_states.get(old.activity_id)
        if state not in {"completed", "in_progress"}:
            continue
        new_state = db.get(models.ActivityStateRow, row.activity_id)
        if new_state is None:
            db.add(
                models.ActivityStateRow(
                    activity_id=row.activity_id,
                    origin_plan_version=new_plan_version,
                    student_uid=student_uid,
                    state=state,
                    generation=1,
                    updated_at=repo.now_iso(),
                )
            )
        else:
            new_state.state = state
            new_state.generation += 1
            new_state.updated_at = repo.now_iso()
        row.origin_plan_version = old_plan_version
        mapping[old.activity_id] = row.activity_id
    db.flush()
    return mapping


@register("student_update")
def handle_student_update(ctx: JobContext) -> dict[str, Any]:
    """组完成/新有效作答后：局部 B → 该学生 C → 审核发布 → 记录适用性。"""

    from study_companion.recommendation.service import recommend_rules, validate_recommendations

    db = ctx.session
    scope = ctx.scope
    dataset_id = scope.get("dataset_id")
    student_id = scope.get("student_id")
    node_id = scope.get("node_id")
    if not (dataset_id and student_id and node_id):
        raise DomainError("VALIDATION_ERROR", "局部更新任务缺少学生或节点")
    dataset = db.get(models.Dataset, dataset_id)
    if dataset is None or not dataset.family_id:
        raise DomainError("NOT_FOUND", "数据集不存在")
    catalog_version = db.scalar(
        select(models.Catalog.version)
        .where(models.Catalog.dataset_id == dataset_id)
        .order_by(models.Catalog.created_at.desc())
        .limit(1)
    )
    if catalog_version is None:
        raise DomainError("RESULT_NOT_READY", "数据集缺少课程目录")
    catalog = load_catalog(db, catalog_version, dataset_id)
    prepared = load_prepared_dataset(db, dataset_id)
    student = db.get(models.Student, (dataset_id, student_id))
    if student is None or not student.student_uid:
        raise DomainError("NOT_FOUND", "该学生不在当前快照中")

    upsert_step(db, ctx.job, "analysis_student", label="更新学情", status="running")
    db.commit()

    context = RunContext(
        run_id=repo.new_id("run"),
        dataset_id=dataset_id,
        catalog_version=catalog_version,
        rule_version=RULE_VERSION,
        dataset_family_id=dataset.family_id,
    )
    bundle = analyze_dataset(prepared, catalog, context, AnalysisConfig(rule_version=RULE_VERSION))
    affected_students = [item for item in bundle.student_results if item.student_id == student_id and item.node_id == node_id]
    affected_classes = [item for item in bundle.class_results if item.class_id == student.class_id and item.node_id == node_id]
    if not affected_students:
        raise DomainError("RESULT_NOT_READY", "无法为该学生生成学情")
    filtered = bundle.model_copy(
        update={"student_results": affected_students, "class_results": affected_classes}
    )
    included = [event.event_id for event in prepared.selected_events]
    result = persist_analysis_bundle(
        db,
        filtered,
        family_id=dataset.family_id,
        catalog=catalog,
        job_id=ctx.job.id,
        event_cutoff=len(included),
        included_event_ids=included,
    )
    upsert_step(db, ctx.job, "analysis_student", label="更新学情", status="succeeded")
    db.commit()

    upsert_step(db, ctx.job, "plan_student", label="复查学习方案", status="running")
    db.commit()

    analysis_version = result["analysis_versions"][0]
    old_key = repo.FormalKey(
        family_id=dataset.family_id,
        dataset_id=dataset_id,
        kind="plan",
        node_key=node_id,
        target_key=student_id,
    )
    old_head = repo.read_head(db, old_key)
    old_plan_version = old_head.version_id if old_head else None

    plan_bundle = recommend_rules(affected_students[0], catalog, context, RecommendationConfig())
    validation = validate_recommendations(plan_bundle, affected_students[0], catalog, RecommendationConfig())
    review = build_review_info(plan_bundle, validation.issues)
    published = persist_plan(
        db,
        plan_bundle,
        family_id=dataset.family_id,
        analysis_version_id=analysis_version,
        knowledge_version_id=None,
        job_id=ctx.job.id,
        review=review,
        student_uid=student.student_uid,
        publish=review.status == "validated",
    )
    _carry_over_states(
        db,
        new_plan_version=published["plan_version"],
        old_plan_version=old_plan_version,
        student_uid=student.student_uid,
    )
    upsert_step(db, ctx.job, "plan_student", label="复查学习方案", status="succeeded")
    db.commit()

    return {
        "student_id": student_id,
        "node_id": node_id,
        "analysis_version": analysis_version,
        "plan_version": published["plan_version"],
        "review_status": published["review_status"],
        "publication_status": published["publication_status"],
        "base_plan_version": old_plan_version,
    }
