"""进展比较的可比性、四分类与证据引用（03 §10、01 §5.1）。

用例全部用手算的 StudentResult 构造，阈值断言直接引用 03 §5.4/§20 的
``mastered_min_ratio=0.80``，不引入文档之外的提升幅度阈值。
"""

from __future__ import annotations

import pytest

from study_companion.analysis.progress import (
    MASTERED_MIN_RATIO,
    compare_progress,
    is_comparable,
)
from study_companion.contracts import (
    AnalysisInputRef,
    EvidenceRef,
    Finding,
    Metric,
    MetricScope,
    StudentResult,
)

METRIC = "valid_score_ratio"
CUTOFF = 10


def analysis_input(
    *,
    cutoff: int = CUTOFF,
    event_ids: tuple[str, ...] = ("E1",),
    rule: str = "rules-v1",
    config: str = "cfg-1",
    knowledge: str = "kn-1",
) -> AnalysisInputRef:
    return AnalysisInputRef(
        dataset_id="ds-1",
        catalog_version="cat-v1",
        knowledge_version=knowledge,
        rule_version=rule,
        config_hash=config,
        event_cutoff=cutoff,
        included_event_ids=list(event_ids),
    )


def metric(
    *,
    version: str,
    numerator: int,
    denominator: int,
    name: str = METRIC,
    indicator_id: str = "I01",
    node_id: str = "N01",
    student_id: str = "S01",
    kp_id: str | None = None,
) -> Metric:
    return Metric(
        metric_id=f"M-{version}-{name}",
        name=name,
        value=(numerator / denominator) if denominator else None,
        numerator=numerator,
        denominator=denominator,
        unit="ratio",
        scope=MetricScope(
            student_id=student_id, node_id=node_id, indicator_id=indicator_id, kp_id=kp_id
        ),
        evidence_refs=[EvidenceRef(kind="record", id=f"R-{version}", run_id=f"run-{version}")],
    )


def finding(
    *,
    version: str,
    indicator_id: str = "I01",
    status: str = "sufficient",
    performance: str = "observed_strength",
) -> Finding:
    return Finding(
        finding_id=f"F-{version}",
        indicator_id=indicator_id,
        description="测试结论",
        evidence_status=status,
        performance=performance,
        evidence_refs=[EvidenceRef(kind="metric", id=f"M-{version}-{METRIC}", run_id=f"run-{version}")],
    )


def result(
    *,
    version: str = "1",
    numerator: int = 0,
    denominator: int = 5,
    node_id: str = "N01",
    indicator_id: str = "I01",
    status: str = "sufficient",
    performance: str = "observed_strength",
    cutoff: int = CUTOFF,
    event_ids: tuple[str, ...] = ("E1",),
    rule: str = "rules-v1",
    config: str = "cfg-1",
    knowledge: str = "kn-1",
    with_input: bool = True,
    analysis_version: str | None = None,
    metrics: list[Metric] | None = None,
    dataset_family_id: str | None = "fam-1",
) -> StudentResult:
    resolved_version = analysis_version if analysis_version is not None else f"v-{version}"
    if metrics is None:
        metrics = [
            metric(
                version=version,
                numerator=numerator,
                denominator=denominator,
                indicator_id=indicator_id,
                node_id=node_id,
            )
        ]
    return StudentResult(
        run_id=f"run-{version}",
        dataset_id="ds-1",
        catalog_version="cat-v1",
        student_id="S01",
        class_id="C01",
        node_id=node_id,
        metrics=metrics,
        findings=[
            finding(
                version=version,
                indicator_id=indicator_id,
                status=status,
                performance=performance,
            )
        ],
        limitations=[],
        analysis_version=resolved_version,
        analysis_input=analysis_input(
            cutoff=cutoff, event_ids=event_ids, rule=rule, config=config, knowledge=knowledge
        )
        if with_input
        else None,
        dataset_family_id=dataset_family_id,
    )


def comparison_of(comparisons, metric_name: str = METRIC):
    return next(item for item in comparisons if item.comparison_id.endswith(f"-{metric_name}"))


# 1. 0/5 -> 5/5：同 node/指标/版本，且有基线之后的新作答，判明显改善
def test_improved_from_zero_to_full():
    baseline = result(version="1", numerator=0, denominator=5, event_ids=("E1",))
    current = result(version="2", numerator=5, denominator=5, event_ids=("E1", "E2"))
    comparisons = compare_progress(baseline, current)

    item = comparison_of(comparisons)
    assert item.comparable is True
    assert item.classification == "improved"
    assert (item.baseline_value, item.current_value) == (0.0, 1.0)
    assert item.current_value >= MASTERED_MIN_RATIO > item.baseline_value
    assert "明显改善" in item.description
    # 两版各自的记录与指标引用都在，且首尾对应
    assert {ref.id for ref in item.evidence_refs} == {"M-1-valid_score_ratio", "R-1", "M-2-valid_score_ratio", "R-2"}
    assert item.baseline_analysis_version == "v-1"
    assert item.current_analysis_version == "v-2"
    assert item.scope.student_id == "S01" and item.scope.node_id == "N01"
    assert item.scope.indicator_id == "I01"


# 2. 5/5 -> 0/5：可比但下降，判无明显改善或退步
def test_declined_from_full_to_zero():
    baseline = result(version="1", numerator=5, denominator=5, event_ids=("E1",))
    current = result(version="2", numerator=0, denominator=5, event_ids=("E1", "E2"))

    item = comparison_of(compare_progress(baseline, current))
    assert item.comparable is True
    assert item.classification == "unchanged_or_declined"
    assert item.baseline_value == 1.0 and item.current_value == 0.0
    assert "下降" in item.description


# 3. 不同 node 不可比：所有条目 insufficient 且写清原因
def test_different_node_is_incomparable():
    baseline = result(version="1", node_id="N01")
    current = result(version="2", numerator=5, denominator=5, node_id="N02")

    comparable, reasons = is_comparable(baseline, current)
    assert comparable is False
    assert any("节点不同" in reason for reason in reasons)

    comparisons = compare_progress(baseline, current)
    assert comparisons
    for item in comparisons:
        assert item.comparable is False
        assert item.classification == "insufficient"
        assert any("节点不同" in limitation for limitation in item.limitations)
    assert "不生成变化结论" in comparisons[0].description


# 4. 分母为 0（只有待评分记录）返回 null 且不可比
def test_zero_denominator_is_incomparable():
    baseline = result(version="1", numerator=0, denominator=0)
    current = result(version="2", numerator=5, denominator=5, event_ids=("E1", "E2"))

    comparable, reasons = is_comparable(baseline, current, "I01")
    assert comparable is False
    assert any("分母为0" in reason for reason in reasons)

    item = comparison_of(compare_progress(baseline, current))
    assert item.classification == "insufficient"
    assert item.baseline_value is None
    assert any("分母为0" in limitation for limitation in item.limitations)


# 5. 没有新事件不能宣布改善（03 §10.3 第 5 条）
def test_no_new_evidence_cannot_claim_improvement():
    baseline = result(version="1", numerator=0, denominator=5, event_ids=("E1",))
    current = result(
        version="2", numerator=5, denominator=5, cutoff=CUTOFF, event_ids=("E1",)
    )

    item = comparison_of(compare_progress(baseline, current))
    assert item.classification == "insufficient"
    assert any("未包含基线之后的新事件" in limitation for limitation in item.limitations)


# 6. 规则版本不同不可比（口径已变化）
def test_rule_version_mismatch_is_incomparable():
    baseline = result(version="1", rule="rules-v1")
    current = result(version="2", numerator=5, denominator=5, event_ids=("E1", "E2"), rule="rules-v2")

    comparable, reasons = is_comparable(baseline, current)
    assert comparable is False
    assert any("规则版本不同" in reason for reason in reasons)

    item = comparison_of(compare_progress(baseline, current))
    assert item.classification == "insufficient" and item.comparable is False


# 7. 当前证据不足时不判改善（证据门槛优先于分数）
def test_insufficient_current_evidence_is_not_improved():
    baseline = result(version="1", numerator=0, denominator=5, event_ids=("E1",))
    current = result(
        version="2",
        numerator=5,
        denominator=5,
        event_ids=("E1", "E2"),
        status="insufficient",
        performance="undetermined",
    )

    item = comparison_of(compare_progress(baseline, current))
    assert item.classification == "insufficient"
    assert item.comparable is True
    assert any("新证据不足" in limitation for limitation in item.limitations)


# 8. 记录冲突优先判证据不足，即使数值上升
def test_conflicting_current_is_insufficient():
    baseline = result(version="1", numerator=0, denominator=5, event_ids=("E1",))
    current = result(
        version="2",
        numerator=5,
        denominator=5,
        event_ids=("E1", "E2"),
        status="conflicting",
        performance="observed_strength",
    )

    item = comparison_of(compare_progress(baseline, current))
    assert item.classification == "insufficient"
    assert any("记录冲突" in limitation for limitation in item.limitations)


# 9. 基线证据不足时降级为有所改善，不宣布明显改善
def test_insufficient_baseline_downgrades_to_partial():
    baseline = result(
        version="1",
        numerator=0,
        denominator=5,
        event_ids=("E1",),
        status="insufficient",
        performance="undetermined",
    )
    current = result(version="2", numerator=5, denominator=5, event_ids=("E1", "E2"))

    item = comparison_of(compare_progress(baseline, current))
    assert item.classification == "partially_improved"
    assert any("基线证据状态" in limitation for limitation in item.limitations)


# 10. 同 node 内小幅提升但未跨掌握线：有迹象，不称明显改善
def test_small_gain_below_mastery_line_is_partial():
    baseline = result(version="1", numerator=2, denominator=5, event_ids=("E1",))
    current = result(version="2", numerator=3, denominator=5, event_ids=("E1", "E2"))

    item = comparison_of(compare_progress(baseline, current))
    assert item.comparable is True
    assert item.classification == "partially_improved"
    assert item.current_value < MASTERED_MIN_RATIO
    assert "有所改善" in item.description


# 11. 缺少 analysis_input 无法核对口径：不可比
def test_missing_analysis_input_is_incomparable():
    baseline = result(version="1", with_input=False)
    current = result(version="2", numerator=5, denominator=5, event_ids=("E1", "E2"))

    comparable, reasons = is_comparable(baseline, current)
    assert comparable is False
    assert any("analysis_input" in reason for reason in reasons)
    assert comparison_of(compare_progress(baseline, current)).classification == "insufficient"


# 12. 拿同一次分析版本自比不算进展
def test_same_analysis_version_is_incomparable():
    baseline = result(version="1", analysis_version="v-same")
    current = result(version="2", numerator=5, denominator=5, event_ids=("E1", "E2"), analysis_version="v-same")

    comparable, reasons = is_comparable(baseline, current)
    assert comparable is False
    assert any("同一次分析版本" in reason for reason in reasons)


# 13. 只在一侧存在的指标单独记 insufficient，不隐式补零
def test_metric_only_on_one_side():
    baseline = result(version="1", numerator=0, denominator=5)
    current = result(
        version="2",
        numerator=5,
        denominator=5,
        event_ids=("E1", "E2"),
        metrics=[
            metric(version="2", numerator=5, denominator=5),
            metric(version="2", numerator=3, denominator=5, name="full_score_ratio"),
        ],
    )

    comparisons = compare_progress(baseline, current)
    assert len(comparisons) == 2
    assert comparison_of(comparisons, METRIC).classification == "improved"
    extra = comparison_of(comparisons, "full_score_ratio")
    assert extra.classification == "insufficient" and extra.comparable is False
    assert any("只在当前版本存在" in limitation for limitation in extra.limitations)


# 14. 题库覆盖范围不同（目录题目数变化）时覆盖类指标不可比
def test_item_coverage_span_mismatch_is_incomparable():
    baseline = result(
        version="1",
        metrics=[metric(version="1", numerator=1, denominator=4, name="item_coverage")],
    )
    current = result(
        version="2",
        event_ids=("E1", "E2"),
        metrics=[metric(version="2", numerator=2, denominator=6, name="item_coverage")],
    )

    item = comparison_of(compare_progress(baseline, current), "item_coverage")
    assert item.classification == "insufficient"
    assert any("题库覆盖范围不同" in limitation for limitation in item.limitations)


# 15. 阈值常量来自 03 §5.4/§20，不是本模块自定
def test_threshold_constant_matches_document():
    assert MASTERED_MIN_RATIO == pytest.approx(0.80)


# 16. 两侧引用完全相同的记录时只保留一条
def test_evidence_refs_deduplicated():
    shared = EvidenceRef(kind="record", id="R-SHARED", run_id="run-1")
    baseline = result(version="1")
    baseline.metrics[0].evidence_refs.append(shared)
    current = result(version="2", numerator=5, denominator=5, event_ids=("E1", "E2"))
    current.metrics[0].evidence_refs.append(shared)

    item = comparison_of(compare_progress(baseline, current))
    keys = [ref.key() for ref in item.evidence_refs]
    assert len(keys) == len(set(keys))
    assert sum(1 for ref in item.evidence_refs if ref.id == "R-SHARED") == 1


# 17. 同一记录在两版各自版本下保留两条，便于追溯来源
def test_same_record_in_two_versions_kept_separately():
    baseline = result(version="1")
    baseline.metrics[0].evidence_refs.append(
        EvidenceRef(kind="record", id="R-SAME", run_id="run-1")
    )
    current = result(version="2", numerator=5, denominator=5, event_ids=("E1", "E2"))
    current.metrics[0].evidence_refs.append(
        EvidenceRef(kind="record", id="R-SAME", run_id="run-2")
    )

    item = comparison_of(compare_progress(baseline, current))
    assert sum(1 for ref in item.evidence_refs if ref.id == "R-SAME") == 2


# 18. 两版都没有指标时仍返回一条范围说明，不假装有数据
def test_no_metrics_returns_scope_level_insufficient():
    baseline = result(version="1", metrics=[])
    current = result(version="2", metrics=[])

    comparisons = compare_progress(baseline, current)
    assert len(comparisons) == 1
    item = comparisons[0]
    assert item.comparable is False
    assert item.classification == "insufficient"
    assert item.scope.node_id == "N01"
    assert item.scope.student_id == "S01"
    assert "没有可比较对象" in item.limitations[0]
