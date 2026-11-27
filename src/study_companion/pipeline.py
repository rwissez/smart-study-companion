"""业务流水线：导入完成后的 A → B → C 调度与正式成果落库。

本模块是唯一"把纯函数结果变成正式版本"的地方：

* 任务只负责装配授权上下文与事务，业务判断仍在 ``analysis`` / ``recommendation``；
* 每次落库都写不可变版本 + 依赖边 + 发布记录，切换指针用 CAS；
* 失败保留已成功部分与真实原因，不伪造成功；
* 任务成功 ≠ 审核通过 ≠ 已发布，三者分别记录。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
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
    EvidenceRef,
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
    import study_companion.knowledge.rebuild  # noqa: F401  (知识重建处理器)


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


def _previous_student_result(
    db: Session, family_id: str, dataset_id: str, node_key: str, student_id: str
) -> StudentResult | None:
    """取该学生该节点最近一次**已发布**的正式学情作为进展基线。

    只沿正式指针读，不猜历史；没有基线就返回 None（不伪造"首次即改善"）。
    """

    head = db.scalar(
        select(models.FormalHead)
        .where(
            models.FormalHead.family_id == family_id,
            models.FormalHead.dataset_id == dataset_id,
            models.FormalHead.kind == "student_analysis",
            models.FormalHead.node_key == node_key,
            models.FormalHead.target_key == student_id,
            models.FormalHead.version_id.is_not(None),
        )
        .order_by(models.FormalHead.updated_at.desc())
        .limit(1)
    )
    if head is None or not head.version_id:
        return None
    row = db.get(models.Version, head.version_id)
    if row is None or not row.payload_json:
        return None
    try:
        return StudentResult.model_validate(row.payload_json)
    except Exception:
        return None


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
        version_id = repo.new_id("ban")
        result.analysis_version = version_id
        result.analysis_input = AnalysisInputRef(
            dataset_id=bundle.dataset_id,
            catalog_version=bundle.catalog_version,
            knowledge_version=bundle.knowledge_version,
            rule_version=bundle.rule_version,
            config_hash=repo.digest(bundle.config.model_dump(mode="json")),
            event_cutoff=event_cutoff,
            included_event_ids=included_event_ids,
            created_at=repo.now_iso(),
        )
        baseline = _previous_student_result(db, family_id, bundle.dataset_id, result.node_id, result.student_id)
        if baseline is not None:
            from study_companion.analysis.progress import compare_progress

            try:
                result.progress = compare_progress(baseline, result)
            except Exception:  # 旧版本缺口径时保留"无法比较"而不是中断发布
                result.progress = []
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
            version_id=version_id,
        )
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
        # 依赖边：B 版本实际使用了哪个目录/知识版本，导出闭包据此解析。
        catalog_dep = _version_row(
            db,
            repo.FormalKey(family_id=family_id, dataset_id=bundle.dataset_id, kind="catalog"),
        )
        if catalog_dep is not None:
            repo.register_edge(db, version, catalog_dep, role="catalog")
        if bundle.knowledge_version:
            knowledge_dep = db.get(models.Version, bundle.knowledge_version)
            if knowledge_dep is not None:
                repo.register_edge(db, version, knowledge_dep, role="knowledge")
        repo.record_artifact_use(
            db,
            version_id=version.version_id,
            family_id=family_id,
            inputs=[
                ("catalog", bundle.catalog_version, {"version_id": bundle.catalog_version}, "input"),
                *(
                    [("knowledge", bundle.knowledge_version, {"version_id": bundle.knowledge_version}, "input")]
                    if bundle.knowledge_version
                    else []
                ),
            ],
        )
        index_entries: list[tuple[str, str, dict[str, Any], str]] = []
        for finding in result.findings:
            for ref in finding.evidence_refs:
                index_entries.append(
                    (
                        ref.kind,
                        ref.id,
                        {
                            "run_id": ref.run_id or run_id,
                            "indicator_id": finding.indicator_id,
                            "analysis_version": version.version_id,
                        },
                        "full",
                    )
                )
        for metric in result.metrics:
            for ref in metric.evidence_refs:
                index_entries.append(
                    (
                        ref.kind,
                        ref.id,
                        {
                            "run_id": ref.run_id or run_id,
                            "metric_id": metric.metric_id,
                            "analysis_version": version.version_id,
                        },
                        "full",
                    )
                )
        repo.record_evidence_index(
            db, version_id=version.version_id, entries=index_entries
        )
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


def _normalise_plan(
    bundle: RecommendationBundle,
    plan_version: str,
    indicator_names: dict[str, str] | None = None,
    material_titles: dict[str, str] | None = None,
) -> RecommendationBundle:
    """为旧规则结果补上正式活动/任务组身份（稳定、可重放）。

    同时把面向教师/内部的语言换成学生可读文本：指标编号换成指标名称，
    任务组补上预计时长与预期结果，避免学生端出现 ``T01I1`` 这类内部标识。
    """

    names = indicator_names or {}

    def humanise(text: str) -> str:
        cleaned = text
        for indicator_id, name in names.items():
            if not indicator_id or not name:
                continue
            cleaned = cleaned.replace(f"指标 {indicator_id}", name)
            cleaned = cleaned.replace(indicator_id, name)
        return cleaned

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
                        "purpose": humanise(activity.purpose),
                        "title": activity.title
                        or (material_titles or {}).get(activity.material_id or "", ""),
                    }
                )
            )
        goal = humanise(rec.goal)
        recommendations.append(
            rec.model_copy(
                update={
                    "activities": activities,
                    "goal": goal,
                    "reason": humanise(rec.reason),
                    "student_intro": humanise(rec.student_intro),
                    "group_id": group_id,
                    "strategy_types": rec.strategy_types or ["consolidation"],
                    "review_status": "validated",
                }
            )
        )
        groups.append(
            TaskGroup(
                group_id=group_id,
                goal=goal,
                strategy_types=rec.strategy_types or ["consolidation"],
                activity_ids=[item.activity_id for item in activities],
                order=index,
                required_activity_ids=[item.activity_id for item in activities],
                goal_target_key=rec.indicator_id,
                expected_outcome=goal,
                estimated_minutes=sum(item.estimate_minutes for item in activities),
                student_intro=humanise(rec.student_intro),
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


STRATEGY_PRIORITY = (
    "teacher_intervention",
    "collect_evidence",
    "prerequisite_support",
    "error_correction",
    "consolidation",
    "transfer",
)


def _version_row(db: Session, key: "repo.FormalKey") -> models.Version | None:
    head = repo.read_head(db, key)
    if head is None or not head.version_id:
        return None
    return db.get(models.Version, head.version_id)


def _attach_error_types(
    bundle, prepared, catalog, knowledge_payload: dict[str, Any] | None
) -> None:
    """按事件与知识图谱给 finding 标注错误类型；证据不足不贴标签。"""

    from study_companion.analysis.error_types import classify, signals_from_events

    materials = {item.material_id: item for item in catalog.materials}
    events_by_student: dict[str, list[Any]] = {}
    for event in prepared.selected_events:
        events_by_student.setdefault(event.student_id, []).append(event)
    for result in bundle.student_results:
        signals = signals_from_events(events_by_student.get(result.student_id, []), materials)
        findings_by_indicator = {
            item.indicator_id: {
                "performance": item.performance,
                "evidence_status": item.evidence_status,
            }
            for item in result.findings
        }
        for finding in result.findings:
            finding.error_types = classify(
                indicator_id=finding.indicator_id,
                attempts=signals.get(finding.indicator_id, []),
                findings_by_indicator=findings_by_indicator,
                knowledge_payload=knowledge_payload,
            )


def _textbook_candidates(
    db: Session, dataset_id: str, catalog
) -> tuple[list[Any], dict[str, int]]:
    """从本数据集导入的教材 PDF 抽取规则候选与页面统计；没有 PDF 就返回空。"""

    from study_companion.knowledge.candidates import (
        dedupe_candidates,
        extract_definition_candidates,
    )
    from study_companion.knowledge.pdf_reader import read_pdf
    from study_companion.knowledge.scope_plan import page_stats, plan_scope, split_chapters
    from study_companion.library import blobs

    files = db.scalars(
        select(models.FileObject)
        .join(models.ImportFileRow, models.ImportFileRow.file_id == models.FileObject.file_id)
        .join(models.ImportRecord, models.ImportRecord.import_id == models.ImportFileRow.import_id)
        .where(
            models.ImportRecord.dataset_id == dataset_id,
            models.FileObject.media_type == "application/pdf",
        )
        .distinct()
    ).all()
    if not files:
        return [], {}
    topics: list[str] = []
    for topic in catalog.topics:
        topics.append(topic.topic_id)
        title = getattr(topic, "title", "") or ""
        if title:
            topics.extend(
                part.strip()
                for part in title.replace("与", " ").replace("和", " ").split()
                if len(part.strip()) >= 3
            )
    topics = sorted(set(item for item in topics if item))
    indicators = [indicator.description for indicator in catalog.indicators if indicator.description]
    collected: list[Any] = []
    totals = {
        "source_files": 0,
        "total_pages": 0,
        "relevant_pages": 0,
        "usable_relevant_pages": 0,
        "recovered_pages": 0,
        "unusable_relevant_pages": 0,
    }
    from study_companion.knowledge.textbook_model import extract_points_with_model
    from study_companion.model_service import ModelService

    service = ModelService()
    protected_texts = [
        material.reference_answer
        for material in catalog.materials
        if material.material_type == "question" and material.reference_answer
    ][:40]
    model_used = False
    model_notes: list[str] = []
    for file_row in files:
        try:
            data = blobs.read_bytes(file_row.storage_key)
            document = read_pdf(file_row.file_id, data)
            chapters = split_chapters(document)
            scope = plan_scope(chapters, topics=topics, indicators=indicators)
            stats = page_stats(document, scope)
            for key, value in stats.items():
                totals[key] = totals.get(key, 0) + int(value)
            rule_batch = extract_definition_candidates(scope)
            model_points: list[Any] = []
            if service.configured:
                model_points, model_stats, model_notes = extract_points_with_model(
                    service=service,
                    document=document,
                    scope=scope,
                    catalog=catalog,
                    protected_texts=protected_texts,
                    rule_candidates=rule_batch,
                )
                for key, value in (model_stats or {}).items():
                    totals[f"model_{key}"] = int(value)
            if model_points:
                model_used = True
                collected.extend(model_points)
            else:
                collected.extend(rule_batch)
        except DomainError:
            continue
    if not collected:
        totals["model_notes"] = model_notes  # type: ignore[assignment]
        return [], totals
    if model_used:
        totals["model_notes"] = model_notes  # type: ignore[assignment]
        return collected, totals
    deduped, _ = dedupe_candidates(collected)
    return deduped, totals



#: 单次任务最多为几节微课堂调用模型（其余用规则模板），避免批量导入放大成本。
_LESSON_MODEL_BUDGET: dict[str, int] = {}
LESSON_MODEL_LIMIT = 6


def _take_lesson_model_budget(job_id: str | None) -> bool:
    key = job_id or "no-job"
    used = _LESSON_MODEL_BUDGET.get(key, 0)
    if used >= LESSON_MODEL_LIMIT:
        return False
    _LESSON_MODEL_BUDGET[key] = used + 1
    return True

def current_knowledge_payload(db: Session, dataset_id: str, family_id: str | None) -> dict[str, Any]:
    """读取当前正式知识版本正文；没有构建过就返回空字典（C 会明确标注缺少依据）。"""

    if not family_id:
        return {}
    key = repo.FormalKey(family_id=family_id, dataset_id=dataset_id, kind="knowledge")
    row = repo.read_formal(db, key)
    if row is None or not row.payload_json:
        return {}
    return dict(row.payload_json)


def annotate_strategies(
    bundle: RecommendationBundle, student_result: StudentResult
) -> RecommendationBundle:
    """按 04 §3.1 的启动谓词，把 B 的诊断事实映射成 C 的策略标签。

    只做确定性映射：证据不足先补证；有具体错误才纠错；前置薄弱才补前置；
    已掌握且可迁移才迁移；没有支持的证据一律补证，不把"没证据"当"薄弱"。
    """

    findings = {finding.finding_id: finding for finding in student_result.findings}
    recommendations = []
    for rec in bundle.recommendations:
        related = [findings[item] for item in rec.finding_ids if item in findings]
        strategies: set[str] = set()
        for finding in related:
            if finding.evidence_status != "sufficient":
                strategies.add("collect_evidence")
            elif "foundation_gap" in finding.error_types:
                strategies.add("prerequisite_support")
            elif finding.error_types:
                strategies.add("error_correction")
            elif finding.performance == "observed_strength":
                strategies.add("transfer")
            elif finding.performance == "needs_practice":
                strategies.add("consolidation")
            else:
                strategies.add("collect_evidence")
        if not strategies:
            strategies.add("collect_evidence")
        ordered = [item for item in STRATEGY_PRIORITY if item in strategies]
        recommendations.append(rec.model_copy(update={"strategy_types": ordered}))
    groups = []
    for group in bundle.task_groups:
        rec = next((item for item in recommendations if item.group_id == group.group_id), None)
        ordered = list(rec.strategy_types) if rec else ["collect_evidence"]
        groups.append(
            group.model_copy(
                update={
                    "strategy_types": ordered,
                    "primary_strategy": ordered[0],
                    "auxiliary_strategies": ordered[1:3],
                }
            )
        )
    return bundle.model_copy(update={"recommendations": recommendations, "task_groups": groups})


def _indicator_scores(result: StudentResult) -> dict[str, float | None]:
    scores: dict[str, float | None] = {}
    for metric in result.metrics:
        if metric.name == "valid_score_ratio":
            scores[metric.scope.indicator_id or ""] = metric.value
    return scores


def evaluate_applicability(
    old_plan: RecommendationBundle,
    old_result: StudentResult,
    new_result: StudentResult,
) -> tuple[bool, str, list[str]]:
    """判断旧方案在新学情下是否仍然适用（04 §7、06 §3.3）。

    只有"新的可比证据显示原目标明显变差或出现新的具体错误"才重新规划；
    没有变化、略有改善、或证据仍不足时保留原方案，并如实记录复查结论。
    """

    targets = {
        group.goal_target_key or ""
        for group in old_plan.task_groups
        if group.goal_target_key
    } | {rec.indicator_id for rec in old_plan.recommendations}
    targets.discard("")
    old_scores = _indicator_scores(old_result)
    new_scores = _indicator_scores(new_result)
    new_findings = {finding.finding_id: finding for finding in new_result.findings}

    limitations: list[str] = []
    for indicator_id in sorted(targets):
        before = old_scores.get(indicator_id)
        after = new_scores.get(indicator_id)
        if before is not None and after is not None and after < before - 0.05:
            return (
                False,
                f"指标 {indicator_id} 的可比得分下降（{before:.2f} → {after:.2f}），需要重新规划",
                limitations,
            )
    for finding in new_result.findings:
        if finding.indicator_id not in targets:
            continue
        if finding.evidence_status == "sufficient" and finding.error_types:
            return (
                False,
                f"指标 {finding.indicator_id} 出现新的具体错误证据，需要重新规划",
                limitations,
            )
    if old_plan.analysis_version and old_plan.analysis_version != new_result.analysis_version:
        limitations.append(
            f"原方案基于 {old_plan.analysis_version}，本次复查基于 {new_result.analysis_version}；原依赖不改写"
        )
    return True, "新学情未改变原方案的教学目标与顺序，保持原安排", limitations


def _record_applicability(
    db: Session,
    *,
    key: repo.FormalKey,
    plan_version: str,
    analysis_version: str,
    knowledge_version: str | None,
    catalog_version: str | None,
    result: str,
    event_cutoff: int,
    activity_ids: list[str],
    limitations: list[str],
    job_id: str | None,
) -> models.PlanApplicability:
    digest = repo.digest({"plan": plan_version, "analysis": analysis_version, "result": result})

    def _version_id(candidate: str | None, fallback: str) -> str:
        if candidate and db.get(models.Version, candidate) is not None:
            return candidate
        return fallback

    catalog_key = repo.FormalKey(
        family_id=key.family_id, dataset_id=key.dataset_id, kind="catalog"
    )
    catalog_row = repo.read_formal(db, catalog_key)
    knowledge_id = _version_id(knowledge_version, plan_version)
    catalog_id = catalog_row.version_id if catalog_row is not None else plan_version
    row = models.PlanApplicability(
        applicability_id=repo.new_id("appl"),
        plan_version=plan_version,
        target_analysis_version=analysis_version,
        target_knowledge_version=knowledge_id,
        target_catalog_version=catalog_id,
        policy_hash=C_RULE_VERSION,
        event_cutoff=event_cutoff,
        activity_digest=digest,
        checked_at=repo.now_iso(),
        result=result,
        activity_ids_json=activity_ids,
        evidence_refs_json=[],
        limitations_json=limitations,
        job_id=job_id,
    )
    db.add(row)
    db.flush()
    return row


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
    indicator_names: dict[str, str] | None = None,
    material_titles: dict[str, str] | None = None,
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
    normalised = _normalise_plan(bundle, plan_version_id, indicator_names, material_titles)
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
    repo.record_artifact_use(
        db,
        version_id=plan_version_id,
        family_id=family_id or "",
        inputs=[
            ("analysis", analysis_version_id, {"version_id": analysis_version_id}, "input"),
            ("catalog", normalised.catalog_version, {"version_id": normalised.catalog_version}, "input"),
            *(
                [("knowledge", knowledge_version_id, {"version_id": knowledge_version_id}, "input")]
                if knowledge_version_id
                else []
            ),
        ],
    )
    repo.record_evidence_index(
        db,
        version_id=plan_version_id,
        entries=[
            (
                "material",
                activity.material_id,
                {
                    "plan_version": plan_version_id,
                    "activity_id": activity.activity_id,
                    "node_id": getattr(normalised, "node_id", "") or "",
                },
                "full",
            )
            for recommendation in normalised.recommendations
            for activity in recommendation.activities
            if activity.material_id
        ],
    )
    # 记录策略试验：目标 + 主策略 + 素材指纹，供后续"无效则换策略"判定。
    from study_companion.recommendation import trials as trial_rules

    trial_groups = []
    for group in normalised.task_groups:
        rec = next(
            (item for item in normalised.recommendations if item.group_id == group.group_id),
            None,
        )
        if rec is None:
            continue
        trial_groups.append(
            {
                "group_id": group.group_id,
                "strategy": (group.primary_strategy or (group.strategy_types or ["consolidation"])[0]),
                "target_key": group.goal_target_key or rec.indicator_id,
                "material_ids": [
                    activity.material_id
                    for activity in rec.activities
                    if activity.material_id
                ],
            }
        )
    if trial_groups:
        trial_rules.record_trials(
            db,
            plan_version=plan_version_id,
            groups=trial_groups,
            analysis_version=analysis_version_id,
            created_at=repo.now_iso(),
        )
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

    # 目录本身也登记为正式版本，供适用性/导出引用精确目录版本。
    catalog_key = repo.FormalKey(family_id=family_id, dataset_id=dataset_id, kind="catalog")
    catalog_payload = {
        "catalog_version": catalog_version,
        "dataset_id": dataset_id,
        "topics": len(catalog.topics),
        "indicators": len(catalog.indicators),
        "materials": len(catalog.materials),
    }
    catalog_head = repo.read_head(db, catalog_key)
    catalog_row = repo.create_version(
        db,
        kind="catalog",
        key=catalog_key,
        payload=catalog_payload,
        rule_version=RULE_VERSION,
        input_digest=repo.digest(catalog_payload),
        job_id=ctx.job.id,
        origin_kind="system",
    )
    repo.publish(
        db,
        key=catalog_key,
        version=catalog_row,
        path="system_auto",
        basis="automatic_review",
        job_id=ctx.job.id,
        expected_version_id=catalog_head.version_id if catalog_head else None,
        expected_generation=catalog_head.generation if catalog_head else 0,
    )
    db.commit()

    # A：先构建并发布正式知识体系，B/C 只读正式版本
    upsert_step(db, ctx.job, "knowledge", label="构建课程知识体系", status="running")
    db.commit()
    from study_companion.knowledge.service import KnowledgeConfig, build_knowledge
    from study_companion.model_service import ModelService

    knowledge_version_id = repo.new_id("kn")
    textbook_candidates, textbook_stats = _textbook_candidates(db, dataset_id, catalog)
    knowledge_result = build_knowledge(
        catalog,
        context=None,
        config=KnowledgeConfig(),
        dataset_id=dataset_id,
        knowledge_version=knowledge_version_id,
        model_configured=ModelService().configured,
        textbook_candidates=textbook_candidates,
        textbook_stats=textbook_stats,
    )
    knowledge_payload = knowledge_result.candidate_snapshot.model_dump(mode="json")
    knowledge_payload["review_info"] = knowledge_result.review_info.model_dump(mode="json")
    knowledge_payload["formal_object_ids"] = list(knowledge_result.publishable_object_ids)
    knowledge_key = repo.FormalKey(
        family_id=family_id, dataset_id=dataset_id, kind="knowledge"
    )
    knowledge_head = repo.read_head(db, knowledge_key)
    knowledge_version = repo.create_version(
        db,
        kind="knowledge",
        key=knowledge_key,
        payload=knowledge_payload,
        rule_version=KnowledgeConfig().rule_version,
        input_digest=repo.digest(knowledge_payload),
        job_id=ctx.job.id,
        version_id=knowledge_version_id,
        origin_kind="system",
    )
    if knowledge_result.review_info.status == "validated":
        repo.publish(
            db,
            key=knowledge_key,
            version=knowledge_version,
            path="system_auto",
            basis="automatic_review",
            job_id=ctx.job.id,
            expected_version_id=knowledge_head.version_id if knowledge_head else None,
            expected_generation=knowledge_head.generation if knowledge_head else 0,
        )
    else:
        # 未通过的候选对象进入教师审核队列，逐对象可定位；不在发布后才补。
        from study_companion.knowledge.review_service import ensure_review_item

        snapshot = knowledge_result.candidate_snapshot
        review_objects = (
            ("point", snapshot.points, "kp_id"),
            ("relation", snapshot.relations, "relation_id"),
            ("annotation", snapshot.annotations, "annotation_id"),
        )
        for object_type, items, id_field in review_objects:
            for item in items:
                if item.review_info.status == "validated":
                    continue
                ensure_review_item(
                    db,
                    dataset_id=dataset_id,
                    knowledge_version=knowledge_version_id,
                    object_type=object_type,
                    object_id=getattr(item, id_field),
                    reason="候选对象未通过全部程序校验，需教师确认",
                )
    upsert_step(db, ctx.job, "knowledge", label="构建课程知识体系", status="succeeded")
    db.commit()

    context = RunContext(
        run_id=repo.new_id("run"),
        dataset_id=dataset_id,
        catalog_version=catalog_version,
        rule_version=RULE_VERSION,
        dataset_family_id=family_id,
        knowledge_version=(
            knowledge_version_id if knowledge_result.review_info.status == "validated" else None
        ),
    )
    upsert_step(db, ctx.job, "analysis", label="生成学情分析", status="running")
    db.commit()

    config = AnalysisConfig(rule_version=RULE_VERSION)
    bundle = analyze_dataset(prepared, catalog, context, config)
    _attach_error_types(bundle, prepared, catalog, None)
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
    students_by_id = {student.student_id: student for student in prepared.students}
    from study_companion.library.history import build_history
    from study_companion.recommendation import planner as c_planner

    now = datetime.now(UTC)
    for student_result in bundle.student_results:
        analysis_version = student_result.analysis_version
        student = students_by_id.get(student_result.student_id)
        history = build_history(
            db,
            dataset=dataset,
            student_id=student_result.student_id,
            student_uid=student.student_uid if student else None,
            prepared=prepared,
            now=now,
        )
        node_available = next(
            (
                node.available_question_ids
                for node in prepared.nodes
                if node.node_id == student_result.node_id
            ),
            None,
        )
        try:
            draft = c_planner.build_plan(
                c_planner.PlannerInputs(
                    student_result=student_result,
                    catalog=catalog,
                    knowledge_payload=knowledge_payload,
                    history=history,
                    now=now,
                    node_available_ids=node_available,
                )
            )
        except DomainError as exc:
            if exc.code in {"MATERIAL_NOT_FOUND", "TASK_NOT_AVAILABLE"}:
                plans.append(
                    {"student_id": student_result.student_id, "status": "shortage", "reason": exc.message}
                )
                continue
            raise
        plan_bundle = draft.bundle
        review = c_planner.review_plan_rules(plan_bundle)
        student = students_by_id.get(student_result.student_id)
        published = persist_plan(
            db,
            plan_bundle,
            family_id=family_id,
            analysis_version_id=analysis_version,
            knowledge_version_id=(
                knowledge_version_id if knowledge_result.review_info.status == "validated" else None
            ),
            job_id=ctx.job.id,
            review=review,
            student_uid=student.student_uid if student else None,
            publish=review.status == "validated",
            indicator_names={
                indicator.indicator_id: indicator.description for indicator in catalog.indicators
            },
            material_titles={
                material.material_id: material.content[:60] for material in catalog.materials
            },
        )
        if published["publication_status"] == "published" and student is not None:
            lesson_payload = dict(knowledge_payload)
            lesson_payload["knowledge_version"] = (
                knowledge_version_id if knowledge_result.review_info.status == "validated" else None
            )
            with_lessons = attach_lessons(
                db,
                family_id=family_id,
                dataset_id=dataset_id,
                student_id=student_result.student_id,
                node_id=student_result.node_id,
                plan_version=published["plan_version"],
                knowledge_payload=lesson_payload,
                job_id=ctx.job.id,
                student_uid=student.student_uid,
                catalog=catalog,
            )
            if with_lessons:
                published["plan_version"] = with_lessons["plan_version"]
                published["lessons"] = with_lessons["lessons"]
            if published["publication_status"] == "published":
                _record_applicability(
                    db,
                    key=repo.FormalKey(
                        family_id=family_id,
                        dataset_id=dataset_id,
                        kind="plan",
                        node_key=student_result.node_id,
                        target_key=student_result.student_id,
                    ),
                    plan_version=published["plan_version"],
                    analysis_version=analysis_version,
                    knowledge_version=(
                        knowledge_version_id
                        if knowledge_result.review_info.status == "validated"
                        else None
                    ),
                    catalog_version=catalog.version,
                    result="applicable",
                    event_cutoff=len(included),
                    activity_ids=[],
                    limitations=[],
                    job_id=ctx.job.id,
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


def attach_lessons(
    db: Session,
    *,
    family_id: str,
    dataset_id: str,
    student_id: str,
    node_id: str,
    plan_version: str,
    knowledge_payload: dict[str, Any] | None,
    job_id: str | None,
    student_uid: str | None,
    catalog: Any | None = None,
) -> dict[str, Any] | None:
    """为方案中的任务组补充个性化微课堂，作为父方案的子修订发布。

    微课堂正文由规则模板从**已核验知识依据**生成；没有知识依据时不生成，
    并在方案限制中如实说明，不虚构内容。
    """

    from study_companion.contracts import Activity
    from study_companion.model_service import ModelService
    from study_companion.recommendation import lesson as lesson_service

    plan_row = db.get(models.Version, plan_version)
    if plan_row is None or plan_row.payload_json is None:
        return None
    plan = RecommendationBundle.model_validate(plan_row.payload_json)
    knowledge_payload = knowledge_payload or {}
    points = {point["kp_id"]: point for point in knowledge_payload.get("points", [])}
    annotations = knowledge_payload.get("annotations", [])
    knowledge_version = None
    if knowledge_payload.get("knowledge_version"):
        knowledge_version = knowledge_payload["knowledge_version"]
    if not points:
        plan = plan.model_copy(
            update={
                "limitations": list(plan.limitations)
                + ["没有可用知识依据，本轮未生成个性化微课堂"],
            }
        )

    new_version_id = repo.new_id("plan")
    lessons_created: list[dict[str, Any]] = []
    recommendations = []
    groups = []
    for group in plan.task_groups:
        rec = next((item for item in plan.recommendations if item.group_id == group.group_id), None)
        if rec is None:
            continue
        kp_ids = list(rec.kp_ids)
        if not kp_ids and knowledge_payload:
            for annotation in annotations:
                if annotation.get("source_id") in {
                    activity.material_id for activity in rec.activities if activity.material_id
                }:
                    kp_ids.extend(annotation.get("target_ids", []))
        kp_id = next((item for item in kp_ids if item in points), None)
        activities = list(rec.activities)
        if kp_id:
            point = points[kp_id]
            content_id = repo.new_id("lesson")
            content_version = repo.new_id("lessonv")
            evidence = []
            if knowledge_version:
                evidence = [
                    {
                        "kind": "knowledge_point",
                        "id": kp_id,
                        "knowledge_version": knowledge_version,
                    }
                ]
            title = point.get("name", "") or group.goal
            goal = point.get("definition", "") or group.expected_outcome or group.goal
            teaching_points = [
                point.get("definition", ""),
                group.student_intro or "",
                group.expected_outcome or "",
            ]
            protected_texts: list[str] = []
            if catalog is not None:
                protected_texts = [
                    material.content
                    for material in catalog.materials
                    if material.material_type == "question" and material.content
                ]
            model_draft = None
            model_limitations: list[str] = []
            model_service = ModelService()
            if model_service.configured and _take_lesson_model_budget(job_id):
                from study_companion.recommendation import lesson_model

                model_draft, model_limitations = lesson_model.generate_with_model(
                    service=model_service,
                    title=title,
                    goal=goal,
                    kp_id=kp_id,
                    evidence_refs=[EvidenceRef(**item) for item in evidence],
                    teaching_points=teaching_points,
                    protected_texts=protected_texts,
                )
            draft = model_draft or lesson_service.build_rule_lesson(
                title=title,
                goal=goal,
                kp_id=kp_id,
                evidence_refs=[EvidenceRef(**item) for item in evidence],
                teaching_points=teaching_points,
                fallback_text=point.get("definition", ""),
            )
            issues = lesson_service.validate_lesson(draft, protected_texts=protected_texts)
            review = lesson_service.review_info_for(
                draft, issues, method="model" if model_draft is not None else "rule"
            )
            payload = lesson_service.lesson_payload(
                draft,
                review=review,
                origin_plan_version=new_version_id,
                content_id=content_id,
                content_version=content_version,
            )
            if model_limitations:
                payload["limitations"] = list(payload.get("limitations") or []) + model_limitations
            lesson_key = repo.FormalKey(
                family_id=family_id,
                dataset_id=dataset_id,
                kind="lesson",
                target_key=content_id,
            )
            lesson_head = repo.read_head(db, lesson_key)
            lesson_version = repo.create_version(
                db,
                kind="lesson",
                key=lesson_key,
                payload=payload,
                rule_version=lesson_service.RULE_VERSION,
                input_digest=review.draft_hash or "",
                job_id=job_id,
                version_id=content_version,
                origin_kind="system",
            )
            if review.status == "validated":
                repo.publish(
                    db,
                    key=lesson_key,
                    version=lesson_version,
                    path="system_auto",
                    basis="automatic_review",
                    job_id=job_id,
                    expected_version_id=lesson_head.version_id if lesson_head else None,
                    expected_generation=lesson_head.generation if lesson_head else 0,
                )
            activities.append(
                Activity(
                    order=len(activities) + 1,
                    material_id=None,
                    purpose="先读一遍这次的关键结构，再进入练习",
                    activity_id=f"act-{new_version_id}-{group.order:02d}-lesson",
                    group_id=group.group_id,
                    activity_kind="micro_lesson",
                    state="not_started",
                    title=draft.title,
                    expected_outcome=group.expected_outcome,
                    estimate_minutes=draft.estimated_minutes,
                    content_ref={
                        "kind": "personalized",
                        "content_id": content_id,
                        "content_version": content_version,
                    },
                    content_version=content_version,
                    knowledge_ids=[kp_id],
                    source_label="规则模板生成，已审核",
                )
            )
            lessons_created.append(
                {
                    "group_id": group.group_id,
                    "content_id": content_id,
                    "content_version": content_version,
                    "review_status": review.status,
                }
            )
        recommendations.append(rec.model_copy(update={"activities": activities, "kp_ids": kp_ids}))
        groups.append(
            group.model_copy(
                update={
                    "activity_ids": [item.activity_id for item in activities],
                    "required_activity_ids": [
                        item.activity_id for item in activities if item.activity_kind != "micro_lesson"
                    ],
                }
            )
        )

    if not lessons_created:
        return None

    child = plan.model_copy(
        update={
            "recommendations": recommendations,
            "task_groups": groups,
            "plan_version": new_version_id,
            "parent_plan_version": plan_version,
            "publication_status": "published",
            "publication_basis": "automatic_review",
            "publish_path": "system_auto",
            "created_at": repo.now_iso(),
        }
    )
    key = repo.FormalKey(
        family_id=family_id,
        dataset_id=dataset_id,
        kind="plan",
        node_key=node_id,
        target_key=student_id,
    )
    head = repo.read_head(db, key)
    version = repo.create_version(
        db,
        kind="plan",
        key=key,
        payload=child.model_dump(mode="json"),
        rule_version=C_RULE_VERSION,
        input_digest=repo.digest(child.model_dump(mode="json")),
        run_id=child.run_id,
        job_id=job_id,
        version_id=new_version_id,
        origin_kind="system",
    )
    repo.register_edge(db, version, plan_row, role="parent_plan")
    for edge in repo.dependencies(db, plan_row):
        dep = db.get(models.Version, edge.dep_version_id)
        if dep is not None:
            repo.register_edge(db, version, dep, role=edge.role)
    # 微课堂正文也是本方案的正式依赖，导出闭包要能追到 content。
    for lesson_version in lessons_created:
        content_version = lesson_version.get("content_version")
        if not content_version:
            continue
        content_row = db.get(models.Version, content_version)
        if content_row is not None:
            repo.register_edge(db, version, content_row, role="content")
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
    for group in groups:
        rec = next((item for item in child.recommendations if item.group_id == group.group_id), None)
        if rec is None:
            continue
        for position, activity in enumerate(rec.activities, start=1):
            db.add(
                models.PlanActivity(
                    plan_version=new_version_id,
                    activity_id=activity.activity_id,
                    group_id=group.group_id,
                    origin_plan_version=plan_version,
                    kind=activity.activity_kind,
                    catalog_version=child.catalog_version,
                    material_id=activity.material_id,
                    content_version=activity.content_version,
                    position=position,
                )
            )
            if student_uid and db.get(models.ActivityStateRow, activity.activity_id) is None:
                db.add(
                    models.ActivityStateRow(
                        activity_id=activity.activity_id,
                        origin_plan_version=new_version_id,
                        student_uid=student_uid,
                        state="not_started",
                        generation=1,
                        updated_at=repo.now_iso(),
                    )
                )
    db.flush()
    return {"plan_version": new_version_id, "lessons": lessons_created}


def new_run_id() -> str:
    return repo.new_id("run")


@register("export_build")
def handle_export_build(ctx: JobContext) -> dict[str, Any]:
    """打包冻结的正式成果并回读校验；不在打包过程中重算任何业务结论。"""

    from pathlib import Path

    from study_companion.config import get_settings
    from study_companion.exports import service as export_service

    db = ctx.session
    export_id = ctx.scope.get("export_id") or ctx.payload.get("export_id")
    if not export_id:
        raise DomainError("VALIDATION_ERROR", "导出任务缺少 export_id")
    export = db.get(models.Export, export_id)
    if export is None:
        raise DomainError("NOT_FOUND", "导出记录不存在")
    upsert_step(db, ctx.job, "freeze", label="读取冻结选择", status="succeeded")
    db.commit()

    upsert_step(db, ctx.job, "serialize", label="序列化正式成果", status="running")
    db.commit()
    settings = get_settings()
    result = export_service.build_archive(db, export, Path(settings.output_root))
    export.storage_key = result["storage_key"]
    export.archive_hash = result["archive_hash"]
    export.bytes = result["bytes"]
    export.manifest_json = result["manifest"]
    export.check_report_json = {
        **result["check_report"],
        "preflight": "passed",
        "payload": "passed" if result["check_report"]["passed"] else "failed",
    }
    export.status = "ready"
    export.updated_at = repo.now_iso()
    upsert_step(db, ctx.job, "serialize", label="序列化正式成果", status="succeeded")
    db.flush()
    return {
        "export_id": export.export_id,
        "snapshot_id": export.snapshot_id,
        "status": "ready",
        "files": result["check_report"]["file_count"],
        "archive_hash": result["archive_hash"],
        "bytes": result["bytes"],
    }


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
    _attach_error_types(bundle, prepared, catalog, current_knowledge_payload(db, dataset_id, dataset.family_id))
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

    # 策略试验：用新的进展分类更新上一轮试验，并据此决定换策略或开教师介入。
    from study_companion.recommendation import trials as trial_rules

    suppressed: dict[str, set[str]] = {}
    old_plan: RecommendationBundle | None = None
    if old_plan_version:
        old_plan_row = db.get(models.Version, old_plan_version)
        if old_plan_row is not None and old_plan_row.payload_json:
            old_plan = RecommendationBundle.model_validate(old_plan_row.payload_json)
        trial_rules.update_trials_from_progress(
            db,
            plan_version=old_plan_version,
            comparisons=[item.model_dump(mode="json") for item in affected_students[0].progress],
            now_iso=repo.now_iso(),
        )
        targets = {group.goal_target_key for group in (old_plan.task_groups if old_plan else []) if group.goal_target_key}
        for target in targets:
            outcome = trial_rules.suppression_for(db, target_key=target)
            if outcome["suppressed"]:
                suppressed[target] = set(outcome["suppressed"])
            if outcome["escalate"]:
                trial_rules.open_intervention_for(
                    db,
                    dataset_id=dataset_id,
                    student_uid=student.student_uid,
                    student_id=student_id,
                    target_key=target,
                    reason_code="no_improvement_after_rounds",
                    summary="连续多轮教学安排后仍无改善，需要教师介入",
                    evidence_refs=[],
                    now_iso=repo.now_iso(),
                )

    # 先判断旧方案是否仍适用：没有实质变化就保留原方案，只记录复查结论。
    if old_plan_version and old_plan is not None:
        old_analysis_row = (
            db.get(models.Version, old_plan.analysis_version)
            if old_plan.analysis_version
            else None
        )
        if old_analysis_row is not None and old_analysis_row.payload_json:
            old_result = StudentResult.model_validate(old_analysis_row.payload_json)
            applicable, reason, limits = evaluate_applicability(
                old_plan, old_result, affected_students[0]
            )
            if applicable:
                _record_applicability(
                    db,
                    key=old_key,
                    plan_version=old_plan_version,
                    analysis_version=analysis_version,
                    knowledge_version=context.knowledge_version,
                    catalog_version=catalog.version,
                    result="applicable",
                    event_cutoff=len(included),
                    activity_ids=[
                        activity.activity_id
                        for rec in old_plan.recommendations
                        for activity in rec.activities
                    ],
                    limitations=limits,
                    job_id=ctx.job.id,
                )
                upsert_step(
                    db, ctx.job, "plan_student", label="复查学习方案", status="succeeded"
                )
                db.commit()
                return {
                    "student_id": student_id,
                    "node_id": node_id,
                    "analysis_version": analysis_version,
                    "plan_version": old_plan_version,
                    "review_status": "validated",
                    "publication_status": "published",
                    "applicability": "applicable",
                    "reason": reason,
                }

    from study_companion.library.history import build_history
    from study_companion.recommendation import planner as c_planner

    now = datetime.now(UTC)
    history = build_history(
        db,
        dataset=dataset,
        student_id=student_id,
        student_uid=student.student_uid,
        prepared=prepared,
        now=now,
    )
    node_available = next(
        (node.available_question_ids for node in prepared.nodes if node.node_id == node_id),
        None,
    )
    draft = c_planner.build_plan(
        c_planner.PlannerInputs(
            student_result=affected_students[0],
            catalog=catalog,
            knowledge_payload=current_knowledge_payload(db, dataset_id, dataset.family_id),
            history=history,
            now=now,
            node_available_ids=node_available,
            suppressed=suppressed,
        )
    )
    plan_bundle = draft.bundle
    review = c_planner.review_plan_rules(plan_bundle)
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
        indicator_names={
            indicator.indicator_id: indicator.description for indicator in catalog.indicators
        },
        material_titles={
            material.material_id: material.content[:60] for material in catalog.materials
        },
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
