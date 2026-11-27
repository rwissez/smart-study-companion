"""B 质量报告（03 §15）与班级掌握分布（03 §12.2）。

两件事都只做确定性投影，不训练、不估算、不调用模型：

- :func:`build_quality_report` 从 ``PreparedDataset``、``Catalog`` 与 ``AnalysisBundle``
  的真实记录逐项聚合计数，分子分母都能回到原始事件或目录条目；分母为 0 时
  ``QualityCount.value`` 为 ``None``，不生成假百分比。
- :func:`mastery_distribution` 输出互斥桶，桶内人数合计等于名册人数，
  无任何作答记录的学生进入"证据不足"桶（03 §12.2），不被当作 0 分或需要加强。

共享契约缺口（本次不动契约，只在 limitations 说明）：``BQualityReport`` 未定义
``source_counts``/``mapping_coverage``/``grading_quality``/``evidence_quality``/
``analysis_quality`` 子结构，``Finding`` 未定义 ``mastery_level``，
``DistributionBucket`` 因此只能表达 performance×evidence_status 的组合桶。
"""

from __future__ import annotations

from study_companion.contracts import (
    AnalysisBundle,
    AnswerEvent,
    BQualityReport,
    Catalog,
    ClassResult,
    DistributionBucket,
    EvidenceStatus,
    Finding,
    Performance,
    PreparedDataset,
    ProgressComparison,
    QualityCount,
    QualityIssue,
    QualityIssueView,
    StudentResult,
)

#: 掌握判定线（03 §5.4 / §20 建议初始值 ``mastered_min_ratio=0.80``）。
#: 工程初始值，待 03 按真实样本校准；本模块只用它说明分布口径，不重算掌握状态。
MASTERED_MIN_RATIO = 0.80

#: 问题排序：先阻塞发布的错误，再警告，最后信息。
_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}

#: 03 §15.3 建议的问题代码清单（集中声明，前端按代码映射中文说明）。
DOCUMENTED_ISSUE_CODES: frozenset[str] = frozenset(
    {
        "RAW_DATA_CONFLICT",
        "REVISION_CONFLICT",
        "INVALID_SCORE_RANGE",
        "RUBRIC_TOTAL_MISMATCH",
        "RUBRIC_PARSE_FAILED",
        "MATERIAL_NOT_IN_CATALOG",
        "PRIMARY_KNOWLEDGE_MAPPING_MISSING",
        "SOLUTION_PARSE_PARTIAL",
        "MODEL_ANALYSIS_UNCERTAIN",
        "MODEL_REVIEW_DISAGREEMENT",
        "GRADING_NEEDS_REVIEW",
        "EVIDENCE_INSUFFICIENT",
        "PROGRESS_NOT_COMPARABLE",
        "NARRATIVE_FALLBACK_USED",
        "CLASS_SAMPLE_TOO_SMALL",
    }
)

#: 掌握分布桶顺序：掌握较好 / 需要加强 / 证据不足 / 证据冲突。
#: performance 与 evidence_status 的映射按 03 §5.4：证据门槛不通过或发生冲突时
#: 一律归 undetermined（优先 insufficient），不因分数高而称掌握较好。
_BUCKET_ORDER: tuple[tuple[Performance, EvidenceStatus], ...] = (
    ("observed_strength", "sufficient"),
    ("needs_practice", "sufficient"),
    ("undetermined", "insufficient"),
    ("undetermined", "conflicting"),
)


# --------------------------------------------------------------------------- #
# 质量报告
# --------------------------------------------------------------------------- #


def build_quality_report(
    *,
    dataset_id: str,
    node_id: str,
    prepared: PreparedDataset,
    bundle: AnalysisBundle,
    catalog: Catalog,
    rule_version: str,
    generated_at: str,
) -> BQualityReport:
    """按节点汇总数据质量，返回可逐项核对的 :class:`BQualityReport`。"""

    events = [event for event in prepared.selected_events if event.node_id == node_id]
    students = prepared.students
    valid_events = _valid_events(events)
    pending_events = [event for event in events if event.status == "pending"]
    unanswered_events = [event for event in events if event.status == "unanswered"]
    excluded_events = [event for event in events if event.disposition == "exclude_score"]
    quarantined_events = [event for event in events if event.disposition == "quarantine_event"]

    observed_students = {event.student_id for event in events}
    valid_students = {event.student_id for event in valid_events}
    pending_students = {event.student_id for event in pending_events}
    no_record_students = [student for student in students if student.student_id not in observed_students]
    pending_only_students = pending_students - valid_students

    catalog_materials = list(catalog.materials)
    catalog_material_ids = {material.material_id for material in catalog_materials}
    unmapped_materials = sorted(
        {event.material_id for event in events if event.material_id not in catalog_material_ids}
    )

    student_results = [result for result in bundle.student_results if result.node_id == node_id]
    class_results = [result for result in bundle.class_results if result.node_id == node_id]
    student_findings = [finding for result in student_results for finding in result.findings]
    progress = [comparison for result in student_results for comparison in result.progress]
    insufficient_progress = [
        comparison for comparison in progress if comparison.classification == "insufficient"
    ]

    node_issues = _issues_for_node(prepared, node_id)
    issue_views = [_project_issue(issue) for issue in node_issues]
    issue_views.extend(
        _derived_issues(
            class_results=class_results,
            insufficient_progress=insufficient_progress,
            student_findings=student_findings,
        )
    )
    issue_views.sort(key=lambda view: (_SEVERITY_ORDER.get(view.severity, 9), view.issue_id))

    counts = {
        # 原始与选中（03 §15.2 source_counts）
        "raw_record_rows": sum(
            1 for record in prepared.raw_records if record.table_name == "records"
        ),
        "raw_rows_total": len(prepared.raw_records),
        "selected_events": len(events),
        "graded_events": sum(1 for event in events if event.status == "graded"),
        "valid_scored_events": len(valid_events),
        "pending_events": len(pending_events),
        "unanswered_events": len(unanswered_events),
        "excluded_score_events": len(excluded_events),
        "quarantined_events": len(quarantined_events),
        # 名册与覆盖
        "roster_students": len(students),
        "classes": len({student.class_id for student in students}),
        "students_with_valid_evidence": len(valid_students),
        "students_without_record": len(no_record_students),
        "students_pending_only": len(pending_only_students),
        "unmapped_materials": len(unmapped_materials),
        # 目录关联
        "catalog_materials": len(catalog_materials),
        "catalog_indicators": len(catalog.indicators),
        "materials_with_indicator": sum(
            1 for material in catalog_materials if material.indicator_ids
        ),
        "materials_with_rubric": sum(
            1 for material in catalog_materials if material.rubric_points
        ),
        # 证据状态（学生级 finding）
        "student_results": len(student_results),
        "class_results": len(class_results),
        "findings_total": len(student_findings),
        "findings_sufficient": sum(
            1 for finding in student_findings if finding.evidence_status == "sufficient"
        ),
        "findings_insufficient": sum(
            1 for finding in student_findings if finding.evidence_status == "insufficient"
        ),
        "findings_conflicting": sum(
            1 for finding in student_findings if finding.evidence_status == "conflicting"
        ),
        "performance_observed_strength": sum(
            1 for finding in student_findings if finding.performance == "observed_strength"
        ),
        "performance_needs_practice": sum(
            1 for finding in student_findings if finding.performance == "needs_practice"
        ),
        "performance_undetermined": sum(
            1 for finding in student_findings if finding.performance == "undetermined"
        ),
        # 进展
        "progress_comparisons": len(progress),
        "progress_insufficient": len(insufficient_progress),
        # 问题
        "quality_issues": len(issue_views),
        "quality_issues_error": sum(1 for view in issue_views if view.severity == "error"),
        "rubric_sum_mismatch": sum(
            1
            for issue in node_issues
            if issue.code in {"RUBRIC_SUM_MISMATCH", "RUBRIC_TOTAL_MISMATCH"}
        ),
    }

    coverage = [
        _quality_count(
            key="roster_coverage",
            label="名册覆盖（有有效证据学生/名册学生）",
            numerator=len(valid_students),
            denominator=len(students),
            limitations=["分母为数据集名册人数，含无任何作答记录的学生；分子只算已评分且纳入统计的学生"],
        ),
        _quality_count(
            key="response_validity",
            label="作答有效率（有效评分事件/选中事件）",
            numerator=len(valid_events),
            denominator=len(events),
            limitations=["分子只算已评分且未被排除或隔离的事件；待评分与未作答不计入有效评分"],
        ),
        _quality_count(
            key="material_indicator_mapping",
            label="指标关联覆盖（有指标映射的目录材料/目录材料）",
            numerator=sum(1 for material in catalog_materials if material.indicator_ids),
            denominator=len(catalog_materials),
            limitations=["只反映目录材料表里的指标关联，未包含知识点 primary 映射"],
        ),
        _quality_count(
            key="material_rubric_mapping",
            label="评分点关联覆盖（有评分点的目录材料/目录材料）",
            numerator=sum(1 for material in catalog_materials if material.rubric_points),
            denominator=len(catalog_materials),
            limitations=["选择/填空题通常没有评分点，比例偏低不代表数据错误"],
        ),
        _quality_count(
            key="evidence_sufficiency",
            label="证据充分性（证据充分的指标结论/全部指标结论）",
            numerator=sum(
                1 for finding in student_findings if finding.evidence_status == "sufficient"
            ),
            denominator=len(student_findings),
            limitations=["按学生×节点×指标的 finding 统计，班级层结论未重复计入"],
        ),
    ]

    limitations = _report_limitations(
        node_id=node_id,
        prepared=prepared,
        bundle=bundle,
        catalog=catalog,
        dataset_id=dataset_id,
        rule_version=rule_version,
        unmapped_materials=unmapped_materials,
    )

    scope = {
        "dataset_id": dataset_id,
        "dataset_family_id": prepared.source_manifest.dataset_family_id,
        "dataset_id_in_prepared": prepared.source_manifest.dataset_id,
        "run_id": bundle.run_id,
        "node_id": node_id,
        "catalog_version": catalog.version,
        "knowledge_version": catalog.knowledge_version,
        "rule_version": rule_version,
        "class_ids": sorted({result.class_id for result in class_results}),
        "scope_kind": "node_quality",
        "event_scope": "node_id 过滤后的选中事件；名册为数据集全部学生",
    }

    return BQualityReport(
        scope=scope,
        node_id=node_id,
        generated_at=generated_at,
        rule_version=rule_version,
        counts=counts,
        coverage=coverage,
        issues=issue_views,
        limitations=limitations,
    )


def _valid_events(events: list[AnswerEvent]) -> list[AnswerEvent]:
    """有效已评分：状态已评分且处置为纳入；零分是有效数值。"""

    return [event for event in events if event.status == "graded" and event.disposition == "include"]


def _quality_count(
    *,
    key: str,
    label: str,
    numerator: int,
    denominator: int,
    limitations: list[str] | None = None,
) -> QualityCount:
    """分母为 0 时 value 为 None（01 §5.1：不生成假百分比）。"""

    return QualityCount(
        key=key,
        label=label,
        numerator=numerator,
        denominator=denominator,
        value=(numerator / denominator) if denominator else None,
        limitations=list(limitations or []),
    )


def _issues_for_node(prepared: PreparedDataset, node_id: str) -> list[QualityIssue]:
    """按事件所属节点过滤质量问题；无 event_id 的数据集级问题一并保留。"""

    event_node = {event.event_id: event.node_id for event in prepared.selected_events}
    selected = []
    for issue in prepared.quality_issues:
        if issue.event_id is not None:
            mapped = event_node.get(issue.event_id)
            # 能定位节点且不属于本节点才过滤；定位不到时保留，避免隐藏无法归属的问题。
            if mapped is not None and mapped != node_id:
                continue
        selected.append(issue)
    return selected


def _project_issue(issue: QualityIssue) -> QualityIssueView:
    return QualityIssueView(
        issue_id=issue.issue_id,
        code=issue.code,
        severity=issue.severity,
        message=issue.message,
        action=issue.action,
        record_ids=list(issue.record_ids),
        event_id=issue.event_id,
        affected_metric_names=list(issue.affected_metric_names),
    )


def _derived_issues(
    *,
    class_results: list[ClassResult],
    insufficient_progress: list[ProgressComparison],
    student_findings: list[Finding],
) -> list[QualityIssueView]:
    """分析层面的可核对问题：只用计数与真实比较结果，不设新阈值。"""

    views: list[QualityIssueView] = []

    if insufficient_progress:
        views.append(
            QualityIssueView(
                issue_id="BQ-PROGRESS-NOT-COMPARABLE",
                code="PROGRESS_NOT_COMPARABLE",
                severity="warning",
                message=f"{len(insufficient_progress)}项进展比较因版本/范围不可比或新证据不足，未判定改善",
                action="retain",
            )
        )

    small_classes = [
        result for result in class_results if result.observed_student_count < result.roster_count
    ]
    if small_classes:
        detail = "、".join(
            f"{result.class_id}（{result.observed_student_count}/{result.roster_count}）"
            for result in small_classes
        )
        views.append(
            QualityIssueView(
                issue_id="BQ-CLASS-SAMPLE-TOO-SMALL",
                code="CLASS_SAMPLE_TOO_SMALL",
                severity="warning",
                message=f"{len(small_classes)}个班级的有效观测学生少于名册，班级结论只覆盖部分学生：{detail}",
                action="retain",
            )
        )

    insufficient_findings = [
        finding for finding in student_findings if finding.evidence_status == "insufficient"
    ]
    if insufficient_findings:
        views.append(
            QualityIssueView(
                issue_id="BQ-EVIDENCE-INSUFFICIENT",
                code="EVIDENCE_INSUFFICIENT",
                severity="info",
                message=(
                    f"{len(insufficient_findings)}个指标结论证据不足，"
                    "需要补充不同题目的有效作答后才能判定掌握程度"
                ),
                action="retain",
            )
        )
    return views


def _report_limitations(
    *,
    node_id: str,
    prepared: PreparedDataset,
    bundle: AnalysisBundle,
    catalog: Catalog,
    dataset_id: str,
    rule_version: str,
    unmapped_materials: list[str],
) -> list[str]:
    limitations = [
        f"统计范围限定为节点{node_id}的选中事件，名册为数据集全部学生，两者口径不同已分别列出",
        "待评分、未作答、无记录分开统计，均不计入有效评分；零分是有效数值",
        "评分点与总分一致性只给不一致计数：prepared 未提供可核对记录总数，故不计算比例，避免假精度",
        "Catalog 未包含知识点 primary 映射与题型/难度覆盖字段，映射覆盖只统计指标关联与评分点关联",
        "Finding 契约当前没有 mastery_level，掌握状态按 performance（旧字段）投影，无法区分初步掌握与需要加强",
        "BQualityReport 契约未定义 source_counts/grading_quality/evidence_quality/analysis_quality 子结构，相关计数以 counts 键与 coverage 项投影",
        "质量报告全部计数由程序从真实记录聚合，模型不参与生成任何数字",
    ]
    if unmapped_materials:
        limitations.append(
            f"材料{','.join(unmapped_materials)}不在当前目录，已从指标统计排除并在 counts 中计数"
        )
    if prepared.source_manifest.dataset_id != dataset_id:
        limitations.append(
            f"传入的 dataset_id（{dataset_id}）与 prepared 记录的内容哈希"
            f"（{prepared.source_manifest.dataset_id}）不一致，报告以传入值为准但需核对来源"
        )
    if bundle.catalog_version != catalog.version:
        limitations.append(
            f"分析批次使用的目录版本（{bundle.catalog_version}）与本次传入目录"
            f"（{catalog.version}）不同，覆盖类比例可能与分析时不一致"
        )
    if bundle.rule_version != rule_version:
        limitations.append(
            f"分析批次规则版本（{bundle.rule_version}）与本次报告版本"
            f"（{rule_version}）不同，阈值口径需按批次版本解释"
        )
    if not catalog.knowledge_version:
        limitations.append("本次目录没有登记知识点版本，知识点级覆盖无法核对")
    limitations.append(
        f"掌握判定线 {MASTERED_MIN_RATIO:.2f} 为 03 §5.4 工程初始值，待真实样本校准"
    )
    return limitations


# --------------------------------------------------------------------------- #
# 班级掌握分布
# --------------------------------------------------------------------------- #


def mastery_distribution(
    bundle: AnalysisBundle, *, class_id: str, indicator_id: str
) -> list[DistributionBucket]:
    """输出该班该指标的互斥掌握桶，人数合计等于名册人数（03 §12.2）。

    无作答记录的学生进入"证据不足"桶；证据冲突的学生不进掌握桶；
    同一学生在同一指标下只进入一个桶（多节点时按保守规则合并）。
    """

    class_results = [result for result in bundle.class_results if result.class_id == class_id]
    declared_roster = max((result.roster_count for result in class_results), default=0)

    roster: list[str] = []
    for result in bundle.student_results:
        if result.class_id == class_id and result.student_id not in roster:
            roster.append(result.student_id)

    if not roster and declared_roster <= 0:
        return []

    class_student_results = [
        result for result in bundle.student_results if result.class_id == class_id
    ]
    counts: dict[tuple[Performance, EvidenceStatus], int] = {
        key: 0 for key in _BUCKET_ORDER
    }
    for student_id in roster:
        counts[_student_bucket(class_student_results, student_id, indicator_id)] += 1

    roster_count = max(len(roster), declared_roster)
    if roster_count > len(roster):
        # 名册声明人数大于可逐人判定的结果数：缺结果的学生只能算证据不足。
        counts[("undetermined", "insufficient")] += roster_count - len(roster)

    return [
        DistributionBucket(
            indicator_id=indicator_id,
            performance=performance,
            evidence_status=status,
            count=counts[(performance, status)],
            roster_count=roster_count,
        )
        for performance, status in _BUCKET_ORDER
    ]


def _student_bucket(
    class_student_results: list[StudentResult], student_id: str, indicator_id: str
) -> tuple[Performance, EvidenceStatus]:
    """单个学生在同一指标下的唯一桶；冲突优先，其次证据门槛，最后看表现。"""

    findings = [
        finding
        for result in class_student_results
        if result.student_id == student_id
        for finding in result.findings
        if finding.indicator_id == indicator_id
    ]
    if not findings:
        return ("undetermined", "insufficient")
    if any(finding.evidence_status == "conflicting" for finding in findings):
        return ("undetermined", "conflicting")
    sufficient = [finding for finding in findings if finding.evidence_status == "sufficient"]
    if not sufficient:
        return ("undetermined", "insufficient")
    if all(finding.performance == "observed_strength" for finding in sufficient):
        return ("observed_strength", "sufficient")
    return ("needs_practice", "sufficient")
