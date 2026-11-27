"""analyze_dataset：基于 PreparedDataset 与课程目录的个体/班级统计。

统计口径对应01文档"统计口径"一节，五个指标全部保存分子、分母、范围、
记录引用与排除说明；分母为0时 value 为 null，不返回假数值。
表现判定为确定性规则（阈值写入 limitations，可追溯、可复核），
不推断性格、动机等无依据标签。
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from study_companion.contracts import (
    AnalysisBundle,
    AnalysisConfig,
    AnswerEvent,
    Catalog,
    ClassResult,
    DiagnosticDetail,
    EvidenceRef,
    Finding,
    Metric,
    MetricScope,
    PreparedDataset,
    RunContext,
    StudentResult,
)

if TYPE_CHECKING:
    from study_companion.model_service import ModelService

# 表现较好判定线（得分率 >= 该值记 observed_strength）；阈值随 rule_version 可追溯
MASTERED_THRESHOLD = 0.80
DEVELOPING_THRESHOLD = 0.50
STRENGTH_THRESHOLD = 0.8

SCORE_RATIO = "valid_score_ratio"          # 得分率：得分和/满分和
FULL_SCORE_RATIO = "full_score_ratio"      # 满分题比例：得满分题数/有效已评分题数
OBSERVED_RATIO = "observed_response_ratio" # 观测作答比例：(已评分+待评分)/全部选中事件
ITEM_COVERAGE = "item_coverage"            # 题库覆盖率：有效不同题目数/目录该指标题目数
STUDENT_RATE = "student_observation_rate"  # 学生观测率：有有效记录学生数/班级名册人数


def analyze_dataset(
    data: PreparedDataset,
    catalog: Catalog,
    context: RunContext,
    config: AnalysisConfig,
    *,
    service: "ModelService | None" = None,
) -> AnalysisBundle:
    """对处理后数据集按 学生×节点×指标 与 班级×节点×指标 计算统计结果。

    ``service`` 可选：只有调用方显式传入且模型已配置时才补一次"错因语义解释"。
    模型失败只记录 limitation，不改动任何指标、分数或已有 Finding。
    """
    material_indicators: dict[str, set[str]] = {}
    indicator_materials: dict[str, set[str]] = defaultdict(set)
    reviewed_variants: dict[str, set[str]] = defaultdict(set)
    for material in catalog.materials:
        material_indicators[material.material_id] = set(material.indicator_ids)
        for iid in material.indicator_ids:
            indicator_materials[iid].add(material.material_id)
            if material.reviewed_variant_tag:
                reviewed_variants[iid].add(material.material_id)

    # 目录外的材料无法归属指标：不进指标统计，但在结果中说明
    unmapped = sorted({e.material_id for e in data.selected_events if e.material_id not in material_indicators})

    events_by_key: dict[tuple[str, str], list[AnswerEvent]] = defaultdict(list)
    for event in data.selected_events:
        events_by_key[(event.student_id, event.node_id)].append(event)

    class_of = {s.student_id: s.class_id for s in data.students}
    roster_classes: list[str] = []
    for student in data.students:
        if student.class_id not in roster_classes:
            roster_classes.append(student.class_id)
    node_ids = [n.node_id for n in data.nodes]
    indicator_ids = [i.indicator_id for i in catalog.indicators]

    student_results: list[StudentResult] = []
    for student in data.students:
        multi_node = sum(1 for n in node_ids if events_by_key.get((student.student_id, n))) > 1
        for node_id in node_ids:
            student_results.append(
                _student_result(
                    student.student_id, student.class_id, node_id,
                    events_by_key.get((student.student_id, node_id), []),
                    indicator_ids, material_indicators, indicator_materials,
                    reviewed_variants, context, config, unmapped, multi_node,
                )
            )

    class_results: list[ClassResult] = []
    for class_id in roster_classes:
        roster = [s for s in data.students if s.class_id == class_id]
        for node_id in node_ids:
            class_events: list[AnswerEvent] = []
            for student in roster:
                class_events.extend(events_by_key.get((student.student_id, node_id), []))
            class_results.append(
                _class_result(
                    class_id, node_id, roster, class_events,
                    indicator_ids, material_indicators, indicator_materials,
                    reviewed_variants, context, config, unmapped,
                )
            )

    from study_companion.analysis.class_insights import enrich_class

    for result in class_results:
        members = [
            item
            for item in student_results
            if item.class_id == result.class_id and item.node_id == result.node_id
        ]
        if not members:
            continue
        insight = enrich_class(
            class_id=result.class_id,
            node_id=result.node_id,
            roster_count=result.roster_count,
            members=members,
        )
        result.common_findings = insight["common_findings"]
        result.performance_distribution = insight["performance_distribution"]
        result.attention_students = insight["attention_students"]
        result.limitations = list(result.limitations) + list(insight["limitations"])

    # 语义解释放在确定性统计全部完成之后：只读已完成的事实，失败不回写统计。
    _apply_model_diagnosis(
        student_results, data=data, catalog=catalog, context=context, service=service
    )

    return AnalysisBundle(
        run_id=context.run_id,
        dataset_id=context.dataset_id,
        catalog_version=context.catalog_version,
        rule_version=context.rule_version,
        config=config,
        student_results=student_results,
        class_results=class_results,
        quality_summary=_quality_summary(data),
    )


# ---------------------------------------------------------------------------
# 指标计算
# ---------------------------------------------------------------------------

def _valid_events(events: list[AnswerEvent]) -> list[AnswerEvent]:
    """有效已评分：状态已评分且未被排除或隔离。零分是有效数值。"""
    return [e for e in events if e.status == "graded" and e.disposition == "include"]


def _metrics_for_events(
    scope: MetricScope,
    key_prefix: str,
    events: list[AnswerEvent],
    indicator_id: str,
    indicator_materials: dict[str, set[str]],
    reviewed_variants: dict[str, set[str]],
    context: RunContext,
    config: AnalysisConfig,
) -> list[Metric]:
    valid = _valid_events(events)
    observed = [e for e in events if e.status in ("graded", "pending")]
    excluded = [e for e in events if e.disposition in ("exclude_score", "quarantine_event")]

    score_sum = sum(e.score for e in valid if e.score is not None)
    max_sum = sum(e.max_score for e in valid if e.max_score is not None)
    full_count = sum(
        1 for e in valid
        if e.score is not None and e.max_score is not None and e.score == e.max_score
    )
    distinct_items = {e.material_id for e in valid}
    refs = [
        EvidenceRef(kind="record", id=e.selected_record_id, run_id=context.run_id)
        for e in valid if e.selected_record_id
    ]
    excluded_count = len(excluded)
    common_limitations: list[str] = []
    if excluded_count:
        common_limitations.append(f"本范围排除{excluded_count}个异常事件（超满分/冲突/缺满分），不进入分子分母")

    def mid(name: str) -> str:
        return f"M-{key_prefix}-{scope.student_id or scope.class_id}-{scope.node_id}-{indicator_id}-{name}"

    def ratio(numerator: int, denominator: int) -> float | None:
        return numerator / denominator if denominator else None

    metrics = [
        Metric(
            metric_id=mid(SCORE_RATIO), name=SCORE_RATIO,
            value=ratio(score_sum, max_sum), numerator=score_sum, denominator=max_sum,
            unit="ratio", scope=scope, evidence_refs=refs,
            excluded_event_count=excluded_count, limitations=list(common_limitations),
        ),
        Metric(
            metric_id=mid(FULL_SCORE_RATIO), name=FULL_SCORE_RATIO,
            value=ratio(full_count, len(valid)), numerator=full_count, denominator=len(valid),
            unit="ratio", scope=scope, evidence_refs=refs,
            excluded_event_count=excluded_count,
            limitations=list(common_limitations) + ["与得分率区分：本题数口径，非分数口径"],
        ),
        Metric(
            metric_id=mid(OBSERVED_RATIO), name=OBSERVED_RATIO,
            value=ratio(len(observed), len(events)), numerator=len(observed), denominator=len(events),
            unit="ratio", scope=scope, evidence_refs=refs,
            excluded_event_count=excluded_count,
            limitations=list(common_limitations) + ["仅反映已出现记录，非任务完成率"],
        ),
        Metric(
            metric_id=mid(ITEM_COVERAGE), name=ITEM_COVERAGE,
            value=ratio(len(distinct_items), len(indicator_materials.get(indicator_id, set()))),
            numerator=len(distinct_items),
            denominator=len(indicator_materials.get(indicator_id, set())),
            unit="ratio", scope=scope, evidence_refs=refs,
            excluded_event_count=excluded_count,
            limitations=list(common_limitations)
            + ["衡量观测覆盖，不意味着所有可用题均被布置；目录未含节点可用题目集合，按指标全集计算"],
        ),
    ]
    reviewed = len(reviewed_variants.get(indicator_id, set()))
    if reviewed:
        note = "已达到" if reviewed >= config.min_reviewed_item_variants else "低于"
        metrics[-1].limitations.append(
            f"已复核变体{reviewed}个，{note}配置要求{config.min_reviewed_item_variants}个"
        )
    return metrics


def _finding_for_indicator(
    indicator_id: str,
    events: list[AnswerEvent],
    metrics_by_name: dict[str, Metric],
    context: RunContext,
    config: AnalysisConfig,
    finding_id: str,
) -> Finding:
    valid = _valid_events(events)
    distinct_items = {e.material_id for e in valid}
    quarantined = [e for e in events if e.disposition == "quarantine_event"]
    score_ratio = metrics_by_name[SCORE_RATIO]
    refs = [
        EvidenceRef(kind="metric", id=m.metric_id, run_id=context.run_id)
        for m in metrics_by_name.values()
    ]

    limitations: list[str] = []
    if score_ratio.value is None:
        status, performance, description = "insufficient", "undetermined", "无有效已评分记录，无法判定"
    elif len(distinct_items) < config.min_valid_distinct_items:
        status, performance = "insufficient", "undetermined"
        description = f"有效不同样题仅{len(distinct_items)}道，低于配置下限{config.min_valid_distinct_items}道，证据不足不定论"
    else:
        status = "sufficient"
        performance = "observed_strength" if score_ratio.value >= STRENGTH_THRESHOLD else "needs_practice"
        description = (
            f"得分率{score_ratio.value:.2f}（{score_ratio.numerator}/{score_ratio.denominator}），"
            f"满分题比例{metrics_by_name[FULL_SCORE_RATIO].value:.2f}，"
            f"有效不同样题{len(distinct_items)}道"
        )
        limitations.append(f"表现判定阈值{STRENGTH_THRESHOLD}，规则版本{context.rule_version}")
    if quarantined:
        status = "conflicting"
        limitations.append(f"{len(quarantined)}个事件因记录冲突被隔离，判定基于其余有效记录")

    # 掌握程度只在证据充分时给；不足或冲突一律 unknown，不默认"未掌握"。
    mastery_level = "unknown"
    if status == "sufficient" and score_ratio.value is not None:
        if score_ratio.value >= MASTERED_THRESHOLD:
            mastery_level = "mastered"
        elif score_ratio.value >= DEVELOPING_THRESHOLD:
            mastery_level = "developing"
        else:
            mastery_level = "emerging"
    return Finding(
        finding_id=finding_id,
        indicator_id=indicator_id,
        description=description,
        evidence_status=status,
        performance=performance,
        evidence_refs=refs,
        limitations=limitations,
        mastery_level=mastery_level,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# 结果组装
# ---------------------------------------------------------------------------

def _student_result(
    student_id: str, class_id: str, node_id: str,
    events: list[AnswerEvent],
    indicator_ids: list[str],
    material_indicators: dict[str, set[str]],
    indicator_materials: dict[str, set[str]],
    reviewed_variants: dict[str, set[str]],
    context: RunContext, config: AnalysisConfig,
    unmapped: list[str], multi_node: bool,
) -> StudentResult:
    metrics: list[Metric] = []
    findings: list[Finding] = []
    limitations: list[str] = []

    for iid in indicator_ids:
        scoped = [e for e in events if iid in material_indicators.get(e.material_id, set())]
        if not scoped:
            continue
        scope = MetricScope(student_id=student_id, node_id=node_id, indicator_id=iid)
        ind_metrics = _metrics_for_events(
            scope, "STU", scoped, iid, indicator_materials, reviewed_variants, context, config
        )
        metrics.extend(ind_metrics)
        by_name = {m.name: m for m in ind_metrics}
        findings.append(_finding_for_indicator(iid, scoped, by_name, context, config, f"F-STU-{student_id}-{node_id}-{iid}"))

    if not events:
        limitations.append("该节点无作答记录")
    if multi_node:
        limitations.append("该学生跨多个节点，节点教学范围不同，不直接比较总分")
    if unmapped:
        limitations.append(f"材料{','.join(unmapped)}不在当前目录，未纳入指标统计")
    return StudentResult(
        run_id=context.run_id, dataset_id=context.dataset_id,
        catalog_version=context.catalog_version,
        student_id=student_id, class_id=class_id, node_id=node_id,
        metrics=metrics, findings=findings, limitations=limitations,
    )


def _class_result(
    class_id: str, node_id: str,
    roster: list, events: list[AnswerEvent],
    indicator_ids: list[str],
    material_indicators: dict[str, set[str]],
    indicator_materials: dict[str, set[str]],
    reviewed_variants: dict[str, set[str]],
    context: RunContext, config: AnalysisConfig,
    unmapped: list[str],
) -> ClassResult:
    metrics: list[Metric] = []
    findings: list[Finding] = []
    limitations: list[str] = []
    observed_students = {e.student_id for e in _valid_events(events)}

    for iid in indicator_ids:
        scoped = [e for e in events if iid in material_indicators.get(e.material_id, set())]
        if not scoped:
            continue
        scope = MetricScope(class_id=class_id, node_id=node_id, indicator_id=iid)
        ind_metrics = _metrics_for_events(
            scope, "CLS", scoped, iid, indicator_materials, reviewed_variants, context, config
        )
        # 学生观测率：分母固定为班级名册人数，保留无记录学生
        roster_count = len(roster)
        ind_metrics.append(
            Metric(
                metric_id=f"M-CLS-{class_id}-{node_id}-{iid}-{STUDENT_RATE}",
                name=STUDENT_RATE,
                value=len(observed_students) / roster_count if roster_count else None,
                numerator=len(observed_students), denominator=roster_count,
                unit="ratio", scope=scope,
                evidence_refs=[
                    EvidenceRef(kind="record", id=e.selected_record_id, run_id=context.run_id)
                    for e in _valid_events(scoped) if e.selected_record_id
                ],
                limitations=["分母为班级名册人数，含无作答记录学生"],
            )
        )
        metrics.extend(ind_metrics)
        by_name = {m.name: m for m in ind_metrics}
        findings.append(_finding_for_indicator(iid, scoped, by_name, context, config, f"F-CLS-{class_id}-{node_id}-{iid}"))

    if len(observed_students) < len(roster):
        limitations.append(f"名册{len(roster)}人中仅{len(observed_students)}人有有效记录，不能推断全班整体表现")
    if unmapped:
        limitations.append(f"材料{','.join(unmapped)}不在当前目录，未纳入指标统计")
    return ClassResult(
        run_id=context.run_id, dataset_id=context.dataset_id,
        catalog_version=context.catalog_version,
        class_id=class_id, node_id=node_id,
        roster_count=len(roster), observed_student_count=len(observed_students),
        metrics=metrics, findings=findings, limitations=limitations,
    )


# ---------------------------------------------------------------------------
# 可选：错因语义诊断（模型只解释，不改分）
# ---------------------------------------------------------------------------

_DIAGNOSIS_LABELS = {
    "foundation_gap": "概念/前置知识缺口",
    "step_error": "步骤或运算错误",
    "hint_dependency": "提示依赖",
    "transfer_difficulty": "迁移困难",
    "other_supported": "其他有证据支持的错误",
    "uncertain": "证据不足，未确定错误类型",
}


def _diagnosis_summary(item) -> str:  # noqa: ANN001 - 避免顶层导入模型任务类型
    label = _DIAGNOSIS_LABELS.get(item.error_type, item.error_type)
    evidence = "；".join(
        f"{entry.source}「{entry.quote.strip()}」" for entry in item.evidence[:3]
    )
    if item.error_type == "uncertain":
        summary = f"语义诊断（{label}）：{item.reason}"
    else:
        summary = f"语义诊断（{label}，置信度{item.confidence:.2f}）：{item.reason}"
    if evidence:
        summary = f"{summary}｜原文证据：{evidence}"
    return summary


def _diagnosis_refs(item, context: RunContext) -> list[EvidenceRef]:  # noqa: ANN001
    refs: list[EvidenceRef] = []
    if item.record_id:
        refs.append(EvidenceRef(kind="record", id=item.record_id, run_id=context.run_id))
    for metric_id in item.metric_ids:
        refs.append(EvidenceRef(kind="metric", id=metric_id, run_id=context.run_id))
    unique: dict[str, EvidenceRef] = {}
    for ref in refs:
        unique.setdefault(ref.key(), ref)
    return list(unique.values())


def _append_limited_notes(
    student_results: list[StudentResult],
    scopes: set[tuple[str, str]],
    notes: list[str],
) -> None:
    """把模型或数据限制写进对应学生结果，不改指标。"""

    if not notes:
        return
    for result in student_results:
        if (result.student_id, result.node_id) not in scopes:
            continue
        for note in notes:
            if note and note not in result.limitations:
                result.limitations.append(note)


def _apply_model_diagnosis(
    student_results: list[StudentResult],
    *,
    data: PreparedDataset,
    catalog: Catalog,
    context: RunContext,
    service: "ModelService | None",
) -> None:
    """在确定性统计完成后补一次语义解释；未配置/无候选时完全跳过。"""

    if service is None or not getattr(service, "configured", True):
        return

    from study_companion.analysis.diagnosis_model import (
        collect_unexplained_errors,
        diagnose_unexplained_errors,
    )

    try:
        collection = collect_unexplained_errors(
            data, catalog, student_results=student_results
        )
    except Exception as exc:  # 诊断准备失败不得影响确定性结果
        note = f"语义诊断准备失败（{type(exc).__name__}），已保留确定性统计与评分"
        for result in student_results:
            if note not in result.limitations:
                result.limitations.append(note)
        return
    if collection.candidates:
        try:
            diagnosis = diagnose_unexplained_errors(
                collection.candidates, service=service
            )
        except Exception as exc:  # 兜底：模型适配层异常不得中断学情分析
            from study_companion.analysis.diagnosis_model import DiagnosisResult

            diagnosis = DiagnosisResult(
                limitations=[
                    f"语义诊断调用异常（{type(exc).__name__}），已保留确定性统计与评分"
                ],
                called=True,
            )
    else:
        diagnosis = None

    affected = {(candidate.student_id, candidate.node_id) for candidate in collection.candidates}
    _append_limited_notes(
        student_results,
        set(collection.omitted_scopes),
        [note for note in collection.limitations if "上限" in note],
    )
    _append_limited_notes(
        student_results,
        set(collection.conflicting_scopes),
        [note for note in collection.limitations if "证据冲突" in note],
    )

    if diagnosis is None:
        return

    if not diagnosis.items:
        _append_limited_notes(student_results, affected, diagnosis.limitations)
        return

    for item in diagnosis.items:
        result = next(
            (
                candidate
                for candidate in student_results
                if candidate.student_id == item.student_id
                and candidate.node_id == item.node_id
            ),
            None,
        )
        if result is None:
            continue
        detail_limitations = list(item.limitations) + [
            "模型语义诊断只解释原因，不改写分数与确定性指标"
        ]
        for indicator_id in item.indicator_ids:
            finding = next(
                (candidate for candidate in result.findings if candidate.indicator_id == indicator_id),
                None,
            )
            if finding is None:
                continue
            finding.diagnostic_details.append(
                DiagnosticDetail(
                    summary=_diagnosis_summary(item),
                    evidence_refs=_diagnosis_refs(item, context),
                    limitations=detail_limitations,
                )
            )
            if finding.generation_method == "rule":
                finding.generation_method = "hybrid"
            if item.error_type == "uncertain":
                note = "模型对本题给出不确定结论，未提升为确定错误类型"
                if note not in finding.limitations:
                    finding.limitations.append(note)

    # 被程序丢弃或模型自述的限制仍要可追溯，挂到受影响学生结果上。
    _append_limited_notes(student_results, affected, diagnosis.limitations)


def _quality_summary(data: PreparedDataset) -> dict[str, int]:
    """处理数量对账：选中/各状态/排除/隔离/有效，与01文档第7步一致。"""
    events = data.selected_events
    return {
        "selected_events": len(events),
        "graded": sum(1 for e in events if e.status == "graded"),
        "pending": sum(1 for e in events if e.status == "pending"),
        "unanswered": sum(1 for e in events if e.status == "unanswered"),
        "excluded_scores": sum(1 for e in events if e.disposition == "exclude_score"),
        "quarantined_events": sum(1 for e in events if e.disposition == "quarantine_event"),
        "valid_scored_events": len(_valid_events(events)),
    }
