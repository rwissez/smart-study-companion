"""analyze_dataset 手算验证用例。

测试数据（3人名册/2节点/3材料，全部指标I01）：
  E1 S01/N01/Q01 已评分 2/2   E2 S01/N01/Q02 已评分 1/2
  E3 S02/N01/Q01 已评分 0/2   E4 S02/N01/Q02 待评分
  E5 S01/N02/Q01 已评分 1/2   E6 S01/N01/Q03 已评分 5/2 超满分排除
  S03 名册中无任何作答记录
"""

from __future__ import annotations

import pytest

from study_companion.analysis import analyze_dataset
from study_companion.contracts import (
    AnalysisConfig,
    AnswerEvent,
    Catalog,
    EvidenceRef,
    Indicator,
    Material,
    Node,
    PreparedDataset,
    RunContext,
    SourceLocator,
    SourceManifest,
    Student,
    Topic,
)

CTX = RunContext(run_id="run-1", dataset_id="ds-1", catalog_version="cat-v1", rule_version="rules-v1")
CONFIG = AnalysisConfig(min_valid_distinct_items=3, min_reviewed_item_variants=2)


def material(material_id: str, max_score: int | None = 2, reviewed: str | None = None) -> Material:
    return Material(
        material_id=material_id,
        material_type="question",
        topic_ids=["T01"],
        indicator_ids=["I01"],
        content=f"题目{material_id}",
        source_locator=SourceLocator(source_file="test", verification_status="verified"),
        max_score=max_score,
        reviewed_variant_tag=reviewed,
    )


def make_catalog() -> Catalog:
    return Catalog(
        version="cat-v1",
        topics=[Topic(topic_id="T01", title="主题")],
        indicators=[Indicator(indicator_id="I01", topic_id="T01", description="指标")],
        materials=[material("Q01"), material("Q02"), material("Q03"), material("Q04", max_score=3)],
        links=[],
    )


def event(event_id: str, student: str, node: str, material_id: str, status: str = "graded",
          score: int | None = None, max_score: int | None = 2,
          disposition: str = "include") -> AnswerEvent:
    return AnswerEvent(
        event_id=event_id, student_id=student, node_id=node, material_id=material_id,
        selected_record_id=f"R-{event_id}", status=status, score=score,
        max_score=max_score, disposition=disposition,
    )


def make_dataset(events: list[AnswerEvent]) -> PreparedDataset:
    return PreparedDataset(
        students=[Student(student_id="S01", class_id="C01"),
                  Student(student_id="S02", class_id="C01"),
                  Student(student_id="S03", class_id="C01")],
        nodes=[Node(node_id="N01"), Node(node_id="N02")],
        raw_records=[],
        selected_events=events,
        quality_issues=[],
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


def run(events: list[AnswerEvent] | None = None):
    return analyze_dataset(make_dataset(BASE_EVENTS if events is None else events), make_catalog(), CTX, CONFIG)


def metric_of(result, name: str):
    return next(m for m in result.metrics if m.name == name)


def student_result(bundle, student_id: str, node_id: str):
    return next(r for r in bundle.student_results
                if r.student_id == student_id and r.node_id == node_id)


# 1. 个体得分率：S01/N01 有效为 E1(2/2)+E2(1/2)，超满分 E6 排除 => 3/4
def test_student_score_ratio_hand_calculated():
    m = metric_of(student_result(run(), "S01", "N01"), "valid_score_ratio")
    assert (m.numerator, m.denominator) == (3, 4)
    assert m.value == pytest.approx(0.75)
    assert m.excluded_event_count == 1
    # 证据引用指向有效记录，不含被排除的 E6
    assert {r.id for r in m.evidence_refs} == {"R-E1", "R-E2"}


# 2. 得分率与满分题比例区分（1分题满分 + 3分题部分得分）
def test_score_ratio_vs_full_ratio_distinction():
    events = [
        event("E1", "S01", "N01", "Q01", score=1, max_score=1),
        event("E2", "S01", "N01", "Q04", score=2, max_score=3),
    ]
    result = student_result(run(events), "S01", "N01")
    sr = metric_of(result, "valid_score_ratio")
    fr = metric_of(result, "full_score_ratio")
    assert (sr.numerator, sr.denominator) == (3, 4)   # 分数口径 (1+2)/(1+3)
    assert sr.value == pytest.approx(0.75)
    assert (fr.numerator, fr.denominator) == (1, 2)   # 题数口径：仅Q01得满分
    assert fr.value == pytest.approx(0.5)


# 3. 分母为0返回null，不返回假数值（只有待评分记录）
def test_zero_denominator_returns_null():
    events = [event("E1", "S01", "N01", "Q01", status="pending")]
    m = metric_of(student_result(run(events), "S01", "N01"), "valid_score_ratio")
    assert m.value is None and m.denominator == 0


# 4. 名册中无记录学生不消失，且不生成无依据结论
def test_roster_student_without_records_visible():
    bundle = run()
    r = student_result(bundle, "S03", "N01")
    assert r.metrics == []
    assert any("无作答记录" in lim for lim in r.limitations)
    # 班级结果中 roster 3 人、有有效记录 2 人
    cr = next(c for c in bundle.class_results if c.class_id == "C01" and c.node_id == "N01")
    assert (cr.roster_count, cr.observed_student_count) == (3, 2)


# 5. 班级统计：汇总得分/汇总满分（非学生均值）(2+1+0)/(2+2+2)=3/6
def test_class_metrics_aggregate_not_average():
    cr = next(c for c in run().class_results if c.class_id == "C01" and c.node_id == "N01")
    sr = metric_of(cr, "valid_score_ratio")
    assert (sr.numerator, sr.denominator) == (3, 6)
    assert sr.value == pytest.approx(0.5)
    fr = metric_of(cr, "full_score_ratio")
    assert (fr.numerator, fr.denominator) == (1, 3)


# 6. 学生观测率分母是名册人数：2/3
def test_student_observation_rate_denominator_is_roster():
    cr = next(c for c in run().class_results if c.class_id == "C01" and c.node_id == "N01")
    m = metric_of(cr, "student_observation_rate")
    assert (m.numerator, m.denominator) == (2, 3)
    assert m.value == pytest.approx(2 / 3)


# 7. 观测作答比例：(已评分+待评分)/全部选中事件；S02/N01 = (E3+E4)/(E3+E4) = 1
def test_observed_response_ratio_counts_pending():
    m = metric_of(student_result(run(), "S02", "N01"), "observed_response_ratio")
    assert (m.numerator, m.denominator) == (2, 2)
    # 未作答不计入分子：S01/N01 有 E1,E2,E6 三个已评分事件
    m2 = metric_of(student_result(run(), "S01", "N01"), "observed_response_ratio")
    assert (m2.numerator, m2.denominator) == (3, 3)


# 8. 样题量不足时证据不足、不定论（S01/N01 仅2道 < 配置3道）
def test_insufficient_evidence_undetermined():
    f = next(finding for finding in student_result(run(), "S01", "N01").findings
             if finding.indicator_id == "I01")
    assert f.evidence_status == "insufficient"
    assert f.performance == "undetermined"


# 9. 证据充分时的表现判定（3道有效题，得分率0.5 → 需巩固）
def test_sufficient_evidence_classifies_performance():
    events = [
        event("E1", "S01", "N01", "Q01", score=1),
        event("E2", "S01", "N01", "Q02", score=1),
        event("E3", "S01", "N01", "Q04", score=1, max_score=3),
    ]
    f = next(f for f in student_result(run(events), "S01", "N01").findings
             if f.indicator_id == "I01")
    assert f.evidence_status == "sufficient"
    assert f.performance == "needs_practice"
    assert f.evidence_refs  # 引用了具体metric


# 10. 隔离事件导致证据状态 conflicting，但判定基于其余有效记录
def test_quarantine_marks_conflicting():
    events = [
        event("E1", "S01", "N01", "Q01", score=2),
        event("E2", "S01", "N01", "Q02", score=2),
        event("E3", "S01", "N01", "Q03", score=2),
        event("E4", "S01", "N01", "Q04", score=0, disposition="quarantine_event"),
    ]
    f = next(f for f in student_result(run(events), "S01", "N01").findings
             if f.indicator_id == "I01")
    assert f.evidence_status == "conflicting"
    assert f.performance == "observed_strength"  # 基于未被隔离的3道满分题
    assert any("隔离" in lim for lim in f.limitations)


# 11. 题库覆盖率：有效不同题目2 / 目录该指标题目4（Q01-Q04）
def test_item_coverage_uses_catalog_denominator():
    m = metric_of(student_result(run(), "S01", "N01"), "item_coverage")
    assert (m.numerator, m.denominator) == (2, 4)
    assert m.value == pytest.approx(0.5)


# 12. 处理数量可对账
def test_quality_summary_reconciles():
    qs = run().quality_summary
    assert qs == {
        "selected_events": 6, "graded": 5, "pending": 1, "unanswered": 0,
        "excluded_scores": 1, "quarantined_events": 0, "valid_scored_events": 4,
    }


# 13. 每个metric带完整范围与run_id证据
def test_metrics_carry_scope_and_evidence_run_id():
    bundle = run()
    for r in bundle.student_results + bundle.class_results:
        for m in r.metrics:
            assert (m.scope.student_id is None) != (m.scope.class_id is None)  # 恰好一个主体
            assert m.scope.node_id == r.node_id
            for ref in m.evidence_refs:
                assert ref.run_id == CTX.run_id
    assert bundle.run_id == CTX.run_id and bundle.dataset_id == CTX.dataset_id
