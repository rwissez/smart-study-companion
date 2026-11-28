"""B 质量报告与班级掌握分布（03 §15、§12.2、01 §5.1）。

所有期望值都按下面的合成数据手算得出：
  E1 S01/N01/Q01 已评分 2/2    E2 S01/N01/Q02 已评分 1/2
  E3 S02/N01/Q01 已评分 0/2    E4 S02/N01/Q02 待评分
  E5 S01/N02/Q01 已评分 1/2    E6 S01/N01/Q03 已评分 5/2 超满分排除
  S03 在名册中但没有任何作答记录
"""

from __future__ import annotations

import pytest

from study_companion.analysis import analyze_dataset
from study_companion.analysis.quality import (
    DOCUMENTED_ISSUE_CODES,
    build_quality_report,
    mastery_distribution,
)
from study_companion.contracts import (
    AnalysisConfig,
    AnswerEvent,
    Catalog,
    Indicator,
    Material,
    MetricScope,
    Node,
    PreparedDataset,
    ProgressComparison,
    QualityIssue,
    RubricPoint,
    RunContext,
    SourceLocator,
    SourceManifest,
    SourceRecord,
    Student,
    Topic,
)

CTX = RunContext(
    run_id="run-1", dataset_id="ds-1", catalog_version="cat-v1", rule_version="rules-v1"
)
CONFIG = AnalysisConfig()
GENERATED_AT = "2026-11-27T00:00:00+08:00"


def material(material_id: str, max_score: int = 2, rubric: bool = False) -> Material:
    return Material(
        material_id=material_id,
        material_type="question",
        topic_ids=["T01"],
        indicator_ids=["I01"],
        content=f"题目{material_id}",
        source_locator=SourceLocator(source_file="test", verification_status="verified"),
        max_score=max_score,
        rubric_points=[RubricPoint(point_id=f"{material_id}P1", description="评分点", max_score=1)]
        if rubric
        else [],
    )


def make_catalog(materials: list[Material] | None = None) -> Catalog:
    return Catalog(
        version="cat-v1",
        topics=[Topic(topic_id="T01", title="主题")],
        indicators=[Indicator(indicator_id="I01", topic_id="T01", description="指标")],
        materials=materials
        if materials is not None
        else [
            material("Q01", rubric=True),
            material("Q02"),
            material("Q03"),
            material("Q04", max_score=3),
        ],
        links=[],
    )


def event(
    event_id: str,
    student: str,
    node: str,
    material_id: str,
    status: str = "graded",
    score: int | None = None,
    max_score: int | None = 2,
    disposition: str = "include",
) -> AnswerEvent:
    return AnswerEvent(
        event_id=event_id,
        student_id=student,
        node_id=node,
        material_id=material_id,
        selected_record_id=f"R-{event_id}",
        status=status,
        score=score,
        max_score=max_score,
        disposition=disposition,
    )


def make_dataset(
    events: list[AnswerEvent],
    issues: list[QualityIssue] | None = None,
    raw_record_rows: int = 1,
) -> PreparedDataset:
    return PreparedDataset(
        students=[
            Student(student_id="S01", class_id="C01"),
            Student(student_id="S02", class_id="C01"),
            Student(student_id="S03", class_id="C01"),
        ],
        nodes=[Node(node_id="N01"), Node(node_id="N02")],
        raw_records=[
            SourceRecord(record_id=f"R{i}", table_name="records", source_row_number=i, payload={})
            for i in range(1, raw_record_rows + 1)
        ],
        selected_events=events,
        quality_issues=issues or [],
        source_manifest=SourceManifest(dataset_id="ds-1", table_row_counts={}, synthetic=True),
    )


BASE_EVENTS = [
    event("E1", "S01", "N01", "Q01", score=2),
    event("E2", "S01", "N01", "Q02", score=1),
    event("E3", "S02", "N01", "Q01", score=0),
    event("E4", "S02", "N01", "Q02", status="pending"),
    event("E5", "S01", "N02", "Q01", score=1),
    event("E6", "S01", "N01", "Q03", score=5, disposition="exclude_score"),
]


def build(
    *,
    events: list[AnswerEvent] | None = None,
    node_id: str = "N01",
    issues: list[QualityIssue] | None = None,
    catalog: Catalog | None = None,
    bundle_mutator=None,
):
    prepared = make_dataset(BASE_EVENTS if events is None else events, issues)
    resolved_catalog = catalog or make_catalog()
    bundle = analyze_dataset(prepared, resolved_catalog, CTX, CONFIG)
    if bundle_mutator is not None:
        bundle = bundle_mutator(bundle)
    report = build_quality_report(
        dataset_id="ds-1",
        node_id=node_id,
        prepared=prepared,
        bundle=bundle,
        catalog=resolved_catalog,
        rule_version="rules-v1",
        generated_at=GENERATED_AT,
    )
    return prepared, bundle, report


def coverage_of(report, key: str):
    return next(item for item in report.coverage if item.key == key)


# 1. 节点范围内的计数逐项手算核对
def test_counts_match_hand_calculation():
    _, _, report = build()

    assert report.node_id == "N01"
    assert report.generated_at == GENERATED_AT
    assert report.rule_version == "rules-v1"
    assert report.counts["selected_events"] == 5
    assert report.counts["graded_events"] == 4
    assert report.counts["valid_scored_events"] == 3      # E6 超满分被排除
    assert report.counts["pending_events"] == 1
    assert report.counts["unanswered_events"] == 0
    assert report.counts["excluded_score_events"] == 1
    assert report.counts["quarantined_events"] == 0
    assert report.counts["raw_record_rows"] == 1
    assert report.counts["roster_students"] == 3
    assert report.counts["classes"] == 1
    assert report.counts["students_with_valid_evidence"] == 2
    assert report.counts["students_without_record"] == 1
    assert report.counts["student_results"] == 3          # 3 名学生 × N01
    assert report.counts["class_results"] == 1

    assert (coverage_of(report, "roster_coverage").numerator, coverage_of(report, "roster_coverage").denominator) == (2, 3)
    assert (coverage_of(report, "response_validity").numerator, coverage_of(report, "response_validity").denominator) == (3, 5)
    assert coverage_of(report, "response_validity").value == pytest.approx(0.6)


# 2. 待评分不参与有效分，单独统计
def test_pending_not_counted_as_valid():
    events = [
        event("E1", "S01", "N01", "Q01", score=2),
        event("E2", "S02", "N01", "Q01", status="pending"),
    ]
    _, _, report = build(events=events)

    assert report.counts["valid_scored_events"] == 1
    assert report.counts["pending_events"] == 1
    assert report.counts["students_with_valid_evidence"] == 1
    assert report.counts["students_pending_only"] == 1
    assert coverage_of(report, "response_validity").value == pytest.approx(0.5)


# 3. 无记录学生不被当成 0 分
def test_student_without_record_is_not_zero():
    _, bundle, report = build()

    assert report.counts["students_without_record"] == 1
    student_result = next(
        item for item in bundle.student_results if item.student_id == "S03" and item.node_id == "N01"
    )
    assert student_result.metrics == []
    assert student_result.findings == []

    buckets = mastery_distribution(bundle, class_id="C01", indicator_id="I01")
    insufficient = next(
        bucket
        for bucket in buckets
        if bucket.performance == "undetermined" and bucket.evidence_status == "insufficient"
    )
    assert insufficient.count == 3                        # S01/S02 证据不足 + S03 无记录
    assert not any(bucket.performance == "needs_practice" and bucket.count for bucket in buckets)


# 4. 分母为 0 时 value 为 None，不返回假百分比
def test_zero_denominator_returns_null():
    # 节点 N02 没有任何选中事件：作答有效率分母为 0
    _, _, report = build(events=[event("E1", "S01", "N01", "Q01", score=2)], node_id="N02")
    assert report.counts["selected_events"] == 0
    response = coverage_of(report, "response_validity")
    assert (response.numerator, response.denominator, response.value) == (0, 0, None)
    roster = coverage_of(report, "roster_coverage")
    assert (roster.numerator, roster.denominator, roster.value) == (0, 3, 0.0)

    # 目录没有材料时，映射覆盖率分母为 0
    empty_catalog = Catalog(version="cat-v1", topics=[], indicators=[], materials=[], links=[])
    _, _, empty_report = build(catalog=empty_catalog)
    mapping = coverage_of(empty_report, "material_indicator_mapping")
    assert (mapping.numerator, mapping.denominator, mapping.value) == (0, 0, None)


# 5. 班级掌握桶互斥且合计等于名册
def test_class_buckets_sum_to_roster():
    _, bundle, _ = build()

    buckets = mastery_distribution(bundle, class_id="C01", indicator_id="I01")
    assert buckets
    assert sum(bucket.count for bucket in buckets) == 3
    assert {bucket.roster_count for bucket in buckets} == {3}
    assert all(bucket.indicator_id == "I01" for bucket in buckets)
    assert len({(bucket.performance, bucket.evidence_status) for bucket in buckets}) == len(buckets)


# 6. 掌握较好与证据冲突分别成桶，同一学生只进一个桶
def test_strength_and_conflicting_buckets():
    # 3 道不同题且全部满分 -> 证据充分、掌握较好
    strong_events = [
        event("E1", "S01", "N01", "Q01", score=2),
        event("E2", "S01", "N01", "Q02", score=2),
        event("E3", "S01", "N01", "Q03", score=2),
    ]
    _, strong_bundle, _ = build(events=strong_events)
    strong_buckets = mastery_distribution(strong_bundle, class_id="C01", indicator_id="I01")
    strength = next(
        bucket
        for bucket in strong_buckets
        if bucket.performance == "observed_strength" and bucket.evidence_status == "sufficient"
    )
    assert strength.count == 1
    assert sum(bucket.count for bucket in strong_buckets) == 3

    # 4 道题中 1 条被隔离 -> finding 证据冲突，优先于分数
    conflict_events = [
        event("E1", "S01", "N01", "Q01", score=2),
        event("E2", "S01", "N01", "Q02", score=2),
        event("E3", "S01", "N01", "Q03", score=2),
        event("E4", "S01", "N01", "Q04", score=0, max_score=3, disposition="quarantine_event"),
    ]
    _, conflict_bundle, conflict_report = build(events=conflict_events)
    assert conflict_report.counts["quarantined_events"] == 1
    conflict_buckets = mastery_distribution(conflict_bundle, class_id="C01", indicator_id="I01")
    conflicting = next(
        bucket for bucket in conflict_buckets if bucket.evidence_status == "conflicting"
    )
    assert conflicting.performance == "undetermined"
    assert conflicting.count == 1
    assert sum(bucket.count for bucket in conflict_buckets) == 3


# 7. 名册声明大于可逐人判定的结果数时，差额进证据不足桶
def test_declared_roster_overflow_goes_to_insufficient():
    def raise_roster(bundle):
        class_result = bundle.class_results[0]
        broken = class_result.model_copy(update={"roster_count": 5})
        return bundle.model_copy(update={"class_results": [broken, *bundle.class_results[1:]]})

    _, bundle, _ = build(bundle_mutator=raise_roster)
    buckets = mastery_distribution(bundle, class_id="C01", indicator_id="I01")
    assert sum(bucket.count for bucket in buckets) == 5
    assert {bucket.roster_count for bucket in buckets} == {5}


# 8. 未知班级返回空列表，不编造名册
def test_unknown_class_returns_empty():
    _, bundle, _ = build()
    assert mastery_distribution(bundle, class_id="C99", indicator_id="I01") == []


# 9. prepared 的质量问题投影为 QualityIssueView，错误排在前
def test_issues_projected_from_prepared():
    issues = [
        QualityIssue(
            issue_id="QI001",
            code="SCORE_EXCEEDS_MAX",
            severity="error",
            record_ids=["R-E6"],
            event_id="E6",
            message="得分超过满分，保留原值并排除该分数统计",
            action="exclude_score",
            rule_version="import-rules-v1",
        ),
        QualityIssue(
            issue_id="QI002",
            code="RUBRIC_SUM_MISMATCH",
            severity="warning",
            record_ids=["R-E1"],
            event_id="E1",
            message="评分点之和与得分不符，保留官方得分并标记待复核",
            action="retain",
            rule_version="import-rules-v1",
            affected_metric_names=["valid_score_ratio"],
        ),
    ]
    _, _, report = build(issues=issues)

    by_id = {issue.issue_id: issue for issue in report.issues}
    assert set(by_id) >= {"QI001", "QI002"}
    assert by_id["QI001"].code == "SCORE_EXCEEDS_MAX"
    assert by_id["QI001"].severity == "error"
    assert by_id["QI001"].record_ids == ["R-E6"]
    assert by_id["QI001"].event_id == "E6"
    assert by_id["QI001"].action == "exclude_score"
    assert by_id["QI002"].affected_metric_names == ["valid_score_ratio"]
    assert [issue.severity for issue in report.issues][0] == "error"
    assert report.counts["quality_issues"] >= 2
    assert report.counts["quality_issues_error"] >= 1
    assert report.counts["rubric_sum_mismatch"] == 1


# 10. 质量问题按节点过滤，无 event_id 或定位不到节点的问题保留
def test_issues_filtered_by_node():
    issues = [
        QualityIssue(
            issue_id="QI010", code="SCORE_EXCEEDS_MAX", severity="warning",
            record_ids=["R-E5"], event_id="E5", message="仅属于 N02 的问题",
            action="retain", rule_version="import-rules-v1",
        ),
        QualityIssue(
            issue_id="QI011", code="DUPLICATE_RECORD", severity="info",
            record_ids=["R-X"], event_id=None, message="数据集级重复行问题",
            action="retain", rule_version="import-rules-v1",
        ),
        QualityIssue(
            issue_id="QI012", code="REVISION_CONFLICT", severity="warning",
            record_ids=["R-Y"], event_id="E-NOT-IN-NODE", message="事件不在选中集合内",
            action="retain", rule_version="import-rules-v1",
        ),
    ]
    _, _, report = build(issues=issues, node_id="N01")
    ids = {issue.issue_id for issue in report.issues}
    assert "QI010" not in ids                              # E5 属于 N02，被过滤
    assert "QI011" in ids                                  # 无 event_id，保留
    assert "QI012" in ids                                  # 定位不到节点，保留待核对


# 11. 分析层面的派生问题只用真实计数
def test_derived_issues_report_counts_only():
    def add_progress(bundle):
        first = bundle.student_results[0]
        comparison = ProgressComparison(
            comparison_id="PC-v1-v2-I01-valid_score_ratio",
            student_id=first.student_id,
            baseline_analysis_version="v1",
            current_analysis_version="v2",
            scope=MetricScope(student_id=first.student_id, node_id=first.node_id, indicator_id="I01"),
            classification="insufficient",
            comparable=False,
            limitations=["节点不同，不可比"],
        )
        updated = first.model_copy(update={"progress": [comparison]})
        return bundle.model_copy(update={"student_results": [updated, *bundle.student_results[1:]]})

    _, _, report = build(bundle_mutator=add_progress)
    codes = {issue.code for issue in report.issues}

    assert "CLASS_SAMPLE_TOO_SMALL" in codes               # 2/3 名册有有效记录
    assert "EVIDENCE_INSUFFICIENT" in codes                # 2 个指标结论证据不足
    assert "PROGRESS_NOT_COMPARABLE" in codes              # 1 项进展不可比
    assert {"CLASS_SAMPLE_TOO_SMALL", "EVIDENCE_INSUFFICIENT", "PROGRESS_NOT_COMPARABLE"} <= DOCUMENTED_ISSUE_CODES
    assert report.counts["progress_comparisons"] == 1
    assert report.counts["progress_insufficient"] == 1


# 12. scope 与 limitations 写清口径和缺口
def test_scope_and_limitations_are_explicit():
    _, _, report = build()

    assert report.scope["node_id"] == "N01"
    assert report.scope["dataset_id"] == "ds-1"
    assert report.scope["run_id"] == "run-1"
    assert report.scope["catalog_version"] == "cat-v1"
    assert report.scope["class_ids"] == ["C01"]
    assert report.limitations
    assert any("工程初始值" in item for item in report.limitations)
    assert any("掌握" in item for item in report.limitations)
    assert any("待评分" in item for item in report.limitations)


# 13. 批次版本与传入版本不一致时在 limitations 里提示
def test_version_mismatch_is_reported():
    def bump_rule(bundle):
        return bundle.model_copy(update={"rule_version": "rules-old"})

    _, _, report = build(bundle_mutator=bump_rule)
    assert any("规则版本" in item for item in report.limitations)
