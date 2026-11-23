"""analyze_dataset：基于 PreparedDataset 与课程目录的个体/班级统计。

统计口径对应01文档"统计口径"一节，五个指标全部保存分子、分母、范围、
记录引用与排除说明；分母为0时 value 为 null，不返回假数值。
表现判定为确定性规则（阈值写入 limitations，可追溯、可复核），
不推断性格、动机等无依据标签。
"""

from __future__ import annotations

from collections import defaultdict

from study_companion.contracts import (
    AnalysisBundle,
    AnalysisConfig,
    AnswerEvent,
    Catalog,
    ClassResult,
    EvidenceRef,
    Finding,
    Metric,
    MetricScope,
    PreparedDataset,
    RunContext,
    StudentResult,
)
from study_companion.curriculum.builder import parse_rubric_scores

RubricObservations = dict[str, list[tuple[str, bool]]]

# 表现较好判定线（得分率 >= 该值记 observed_strength）；阈值随 rule_version 可追溯
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
) -> AnalysisBundle:
    """对处理后数据集按 学生×节点×指标 与 班级×节点×指标 计算统计结果。"""
    material_indicators: dict[str, set[str]] = {}
    indicator_materials: dict[str, set[str]] = defaultdict(set)
    reviewed_variants: dict[str, set[str]] = defaultdict(set)
    questions = {material.material_id for material in catalog.materials if material.material_type == "question"}
    for material in catalog.materials:
        material_indicators[material.material_id] = set(material.indicator_ids)
        for iid in material.indicator_ids:
            if material.material_id in questions:
                indicator_materials[iid].add(material.material_id)
            if material.reviewed_variant_tag:
                reviewed_variants[iid].add(material.material_id)

    # 目录外的材料无法归属指标：不进指标统计，但在结果中说明
    unmapped = sorted({e.material_id for e in data.selected_events if e.material_id not in material_indicators})

    events_by_key: dict[tuple[str, str], list[AnswerEvent]] = defaultdict(list)
    allowed = {
        node.node_id: set(node.available_question_ids)
        for node in data.nodes if node.available_question_ids is not None
    }
    excluded_by_node: dict[str, int] = defaultdict(int)
    for event in data.selected_events:
        if event.node_id in allowed and event.material_id not in allowed[event.node_id]:
            excluded_by_node[event.node_id] += 1
            continue
        events_by_key[(event.student_id, event.node_id)].append(event)

    raw_by_id = {record.record_id: record.payload for record in data.raw_records if record.table_name == "records"}
    rubric_by_material = {material.material_id: material.rubric_points for material in catalog.materials}
    observations = {}
    for events in events_by_key.values():
        for event in _valid_events(events):
            points = rubric_by_material.get(event.material_id, [])
            raw = raw_by_id.get(event.selected_record_id, {})
            scores = parse_rubric_scores(raw.get("评分点得分") or "", points)
            if len(scores) == len(points) and sum(scores.values()) == event.score:
                observations[event.event_id] = [
                    (point.description, scores[point.point_id] < point.max_score)
                    for point in points if point.description not in {"答案正确", "结果正确"}
                ]
    coverage_by_node = {
        node.node_id: {key: values & allowed[node.node_id] for key, values in indicator_materials.items()}
        if node.node_id in allowed else indicator_materials
        for node in data.nodes
    }
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
                    indicator_ids, material_indicators, coverage_by_node[node_id],
                    reviewed_variants, context, config, unmapped, multi_node, observations, node_id in allowed,
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
                    indicator_ids, material_indicators, coverage_by_node[node_id],
                    reviewed_variants, context, config, unmapped, observations, node_id in allowed,
                )
            )

    return AnalysisBundle(
        run_id=context.run_id,
        dataset_id=context.dataset_id,
        catalog_version=context.catalog_version,
        rule_version=context.rule_version,
        config=config,
        student_results=student_results,
        class_results=class_results,
        quality_summary={**_quality_summary(data), "outside_node_scope": sum(excluded_by_node.values())},
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
    node_scope_known: bool,
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
            + ["分母为本次测验可用的同项题目" if node_scope_known else "未提供本次可用题目，按目录中该项全部题目计算"],
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
    observations: RubricObservations,
) -> Finding:
    valid = _valid_events(events)
    distinct_items = {e.material_id for e in valid}
    quarantined = [e for e in events if e.disposition == "quarantine_event"]
    score_ratio = metrics_by_name[SCORE_RATIO]
    class_coverage = metrics_by_name.get(STUDENT_RATE)
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
    elif class_coverage and (class_coverage.numerator or 0) < 2:
        status, performance, description = "insufficient", "undetermined", "该项的有效作答只来自一名学生，暂不能判断班级共性。"
    else:
        status = "sufficient"
        performance = "observed_strength" if score_ratio.value >= STRENGTH_THRESHOLD else "needs_practice"
        description = (
            f"得分率{score_ratio.value:.2f}（{score_ratio.numerator}/{score_ratio.denominator}），"
            f"满分题比例{metrics_by_name[FULL_SCORE_RATIO].value:.2f}，"
            f"有效不同样题{len(distinct_items)}道"
        )
        limitations.append(f"表现判定阈值{STRENGTH_THRESHOLD}，规则版本{context.rule_version}")
    rubric_focus = None
    if score_ratio.value is not None and not quarantined:
        observed: dict[str, set[str]] = defaultdict(set)
        missed: dict[str, dict[str, str]] = defaultdict(dict)
        for event in valid:
            if not event.selected_record_id:
                continue
            subject = event.student_id if class_coverage else event.selected_record_id
            for label, lost_points in observations.get(event.event_id, []):
                observed[label].add(subject)
                if lost_points:
                    missed[label][subject] = event.selected_record_id
        common = [label for label, entries in missed.items() if len(entries) >= (2 if class_coverage else 1)]
        if common:
            rubric_focus = max(common, key=lambda label: (len(missed[label]), len(missed[label]) / len(observed[label]), label))
            unit = "人" if class_coverage else "条作答"
            description += f"。在记录了「{rubric_focus}」这一评分点的 {len(observed[rubric_focus])} {unit}中，{len(missed[rubric_focus])} {unit}未拿满该评分点。"
            refs.extend(EvidenceRef(kind="record", id=record_id, run_id=context.run_id)
                        for record_id in list(missed[rubric_focus].values())[:5])
    if quarantined:
        status = "conflicting"
        limitations.append(f"{len(quarantined)}个事件因记录冲突被隔离，判定基于其余有效记录")

    return Finding(
        finding_id=finding_id,
        indicator_id=indicator_id,
        description=description,
        rubric_focus=rubric_focus,
        evidence_status=status,
        performance=performance,
        evidence_refs=refs,
        limitations=limitations,
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
    unmapped: list[str], multi_node: bool, observations: RubricObservations, node_scope_known: bool,
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
            scope, "STU", scoped, iid, indicator_materials, reviewed_variants, node_scope_known, context, config
        )
        metrics.extend(ind_metrics)
        by_name = {m.name: m for m in ind_metrics}
        findings.append(_finding_for_indicator(iid, scoped, by_name, context, config, f"F-STU-{student_id}-{node_id}-{iid}", observations))

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
    unmapped: list[str], observations: RubricObservations, node_scope_known: bool,
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
            scope, "CLS", scoped, iid, indicator_materials, reviewed_variants, node_scope_known, context, config
        )
        # 学生观测率：分母固定为班级名册人数，保留无记录学生
        roster_count = len(roster)
        indicator_students = {event.student_id for event in _valid_events(scoped)}
        ind_metrics.append(
            Metric(
                metric_id=f"M-CLS-{class_id}-{node_id}-{iid}-{STUDENT_RATE}",
                name=STUDENT_RATE,
                value=len(indicator_students) / roster_count if roster_count else None,
                numerator=len(indicator_students), denominator=roster_count,
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
        findings.append(_finding_for_indicator(iid, scoped, by_name, context, config, f"F-CLS-{class_id}-{node_id}-{iid}", observations))

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
