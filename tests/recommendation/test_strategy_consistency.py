"""策略一致性回归：策略、证据、理由与组内活动必须互相对得上。

覆盖真实回归 ds-9433de079cc54581864c（D14/N01）暴露的两类问题：

* 高分表现被 ``error_types=transfer_difficulty`` 直接推成 ``error_correction``，
  教师理由却写"存在具体错误证据"；
* 组 ``primary_strategy=transfer``，但组内活动全是 ``exposure_mode=practice``。

约束不变：证据不足仍先补证，真实步骤错误仍纠错，审核门不降低。
"""

from __future__ import annotations

from datetime import UTC, datetime

from study_companion.contracts import (
    Catalog,
    EvidenceRef,
    Finding,
    Indicator,
    Material,
    Metric,
    MetricScope,
    RubricPoint,
    SourceLocator,
    StudentResult,
    Topic,
)
from study_companion.recommendation import candidates, planner

CONCRETE_REASON = "存在具体错误证据"


def make_material(
    material_id: str,
    *,
    content: str,
    indicator: str = "T01I1",
    question_type: str = "choice",
    rubric: list[RubricPoint] | None = None,
    reviewed_variant_tag: str | None = None,
) -> Material:
    return Material(
        material_id=material_id,
        material_type="question",
        topic_ids=["T01"],
        indicator_ids=[indicator],
        content=content,
        source_locator=SourceLocator(
            source_file="题目与材料.csv", verification_status="unverified"
        ),
        reference_answer="B",
        max_score=5,
        rubric_points=rubric or [],
        question_type=question_type,  # type: ignore[arg-type]
        reviewed_variant_tag=reviewed_variant_tag,
    )


def make_catalog(materials: list[Material]) -> Catalog:
    return Catalog(
        version="cat-1",
        topics=[Topic(topic_id="T01", title="一元一次方程")],
        indicators=[
            Indicator(indicator_id="T01I1", topic_id="T01", description="去括号时的符号处理")
        ],
        materials=materials,
        links=[],
    )


def make_metric(
    name: str,
    *,
    value: float | None,
    numerator: int | None,
    denominator: int | None,
    indicator: str = "T01I1",
) -> Metric:
    return Metric(
        metric_id=f"M-{name}-{indicator}",
        name=name,
        value=value,
        numerator=numerator,
        denominator=denominator,
        unit="ratio",
        scope=MetricScope(student_id="S01", node_id="N01", indicator_id=indicator),
        evidence_refs=[EvidenceRef(kind="record", id="R01", run_id="run-1")],
    )


def high_score_metrics(
    *,
    score_ratio: float = 0.91,
    full_count: int = 6,
    full_total: int = 7,
    valid_items: int = 7,
    indicator: str = "T01I1",
) -> list[Metric]:
    """D14 类似指标：得分率 0.91、满分题比例 0.86、有效样题 7 道。"""

    return [
        make_metric(
            "valid_score_ratio",
            value=score_ratio,
            numerator=int(round(score_ratio * 100)),
            denominator=100,
            indicator=indicator,
        ),
        make_metric(
            "full_score_ratio",
            value=round(full_count / full_total, 4),
            numerator=full_count,
            denominator=full_total,
            indicator=indicator,
        ),
        make_metric(
            "item_coverage",
            value=round(valid_items / 8, 4),
            numerator=valid_items,
            denominator=8,
            indicator=indicator,
        ),
    ]


def low_score_metrics(indicator: str = "T01I1") -> list[Metric]:
    return [
        make_metric(
            "valid_score_ratio",
            value=0.4,
            numerator=2,
            denominator=5,
            indicator=indicator,
        ),
        make_metric(
            "full_score_ratio",
            value=0.2,
            numerator=1,
            denominator=5,
            indicator=indicator,
        ),
        make_metric(
            "item_coverage",
            value=0.625,
            numerator=5,
            denominator=8,
            indicator=indicator,
        ),
    ]


def make_finding(
    finding_id: str = "F-STU-S01-N01-T01I1",
    *,
    performance: str = "observed_strength",
    evidence_status: str = "sufficient",
    error_types: list[str] | None = None,
    description: str = "得分率0.91（91/100），满分题比例0.86，有效不同样题7道",
) -> Finding:
    return Finding(
        finding_id=finding_id,
        indicator_id="T01I1",
        kp_id="kp-T01I1",
        description=description,
        evidence_status=evidence_status,  # type: ignore[arg-type]
        performance=performance,  # type: ignore[arg-type]
        evidence_refs=[EvidenceRef(kind="record", id="R01", run_id="run-1")],
        error_types=error_types or [],  # type: ignore[arg-type]
        mastery_level="mastered" if performance == "observed_strength" else "unknown",
    )


def make_result(findings: list[Finding], metrics: list[Metric]) -> StudentResult:
    return StudentResult(
        run_id="run-1",
        dataset_id="ds-9433de079cc54581864c",
        catalog_version="cat-1",
        student_id="D14",
        class_id="DC01",
        node_id="N01",
        metrics=metrics,
        findings=findings,
        limitations=[],
        analysis_version="ban-1",
        strategy_signals=[],
    )


def knowledge_payload() -> dict:
    return {
        "points": [
            {
                "kp_id": "kp-T01I1",
                "name": "去括号时的符号处理",
                "definition": "括号前是负号时，括号内每一项都要变号",
                "scope_role": "core",
                "indicator_ids": ["T01I1"],
                "source_locators": [],
            }
        ],
        "relations": [],
        "annotations": [
            {
                "annotation_id": "ann-Q1",
                "source_type": "material",
                "source_id": "Q1",
                "target_ids": ["kp-T01I1"],
                "knowledge_role": "primary",
            },
            {
                "annotation_id": "ann-Q2",
                "source_type": "material",
                "source_id": "Q2",
                "target_ids": ["kp-T01I1"],
                "knowledge_role": "primary",
            },
            {
                "annotation_id": "ann-Q3",
                "source_type": "material",
                "source_id": "Q3",
                "target_ids": ["kp-T01I1"],
                "knowledge_role": "primary",
            },
        ],
    }


def basic_materials() -> list[Material]:
    return [
        make_material("Q1", content="化简 -(2x-7) 的结果是（）"),
        make_material("Q2", content="化简 -(3x-9) 的结果是（）"),
        make_material("Q3", content="化简 -(4x-11) 的结果是（）"),
    ]


def advanced_materials() -> list[Material]:
    """进阶题：建模情境 + 多次运算，难度是 advanced，可承担迁移检查。"""

    return [
        make_material(
            "Q1",
            content="实际问题：解方程 3(x+2)-4=2(x-1)+5，并说明每一步依据。",
            question_type="solution",
        ),
        make_material(
            "Q2",
            content="实际方案：解方程 2(x+3)-5=3(x-2)+4，并说明每一步依据。",
            question_type="solution",
            reviewed_variant_tag="V2",
        ),
    ]


def build_inputs(
    *,
    findings: list[Finding],
    metrics: list[Metric],
    materials: list[Material],
) -> planner.PlannerInputs:
    return planner.PlannerInputs(
        student_result=make_result(findings, metrics),
        catalog=make_catalog(materials),
        knowledge_payload=knowledge_payload(),
        history=candidates.LearningHistoryView(),
        now=datetime(2026, 11, 27, tzinfo=UTC),
    )


# --------------------------------------------------------------------------- #
# 1. 高分 + 迁移困难：不再选具体纠错
# --------------------------------------------------------------------------- #


def test_high_score_transfer_difficulty_is_not_error_correction() -> None:
    finding = make_finding(error_types=["transfer_difficulty"])
    strategy = planner.strategy_for_finding(
        finding.model_dump(mode="json"),
        {"score_ratio": 0.91, "full_ratio": 0.86, "valid_items": 7},
    )
    assert strategy == "consolidation", "高分只有迁移类信号时不能选 error_correction"

    result = planner.build_plan(
        build_inputs(
            findings=[finding],
            metrics=high_score_metrics(),
            materials=basic_materials(),
        )
    )
    rec = result.bundle.recommendations[0]
    assert rec.strategy_types == ["consolidation"]
    assert CONCRETE_REASON not in rec.reason
    assert "变式" in rec.reason
    assert "巩固" in rec.reason
    assert "练稳" in rec.student_intro
    # 规则审核门保持原样：这组输入本身应当能通过，而不是靠放宽门。
    assert planner.review_plan_rules(result.bundle).status == "validated"


# --------------------------------------------------------------------------- #
# 2. 真实错误仍然纠错
# --------------------------------------------------------------------------- #


def test_concrete_step_error_still_chooses_error_correction() -> None:
    finding = make_finding(
        performance="needs_practice",
        error_types=["step_error"],
        description="结果正确但步骤分未满，去括号过程缺失",
    )
    strategy = planner.strategy_for_finding(
        finding.model_dump(mode="json"),
        {"score_ratio": 0.4, "full_ratio": 0.2, "valid_items": 5},
    )
    assert strategy == "error_correction"

    result = planner.build_plan(
        build_inputs(
            findings=[finding],
            metrics=low_score_metrics(),
            materials=basic_materials(),
        )
    )
    rec = result.bundle.recommendations[0]
    assert rec.strategy_types == ["error_correction"]
    assert rec.reason.startswith(CONCRETE_REASON)


# --------------------------------------------------------------------------- #
# 3. 证据不足仍先补证
# --------------------------------------------------------------------------- #


def test_insufficient_evidence_still_collects_evidence() -> None:
    finding = make_finding(
        performance="undetermined",
        evidence_status="insufficient",
        error_types=["transfer_difficulty"],
        description="有效不同样题仅1道，证据不足不定论",
    )
    strategy = planner.strategy_for_finding(
        finding.model_dump(mode="json"),
        {"score_ratio": None, "full_ratio": None, "valid_items": 1},
    )
    assert strategy == "collect_evidence"

    result = planner.build_plan(
        build_inputs(
            findings=[finding],
            metrics=[
                make_metric(
                    "item_coverage", value=0.125, numerator=1, denominator=8
                )
            ],
            materials=basic_materials(),
        )
    )
    rec = result.bundle.recommendations[0]
    assert rec.strategy_types == ["collect_evidence"]
    assert rec.reason.startswith("证据不足")
    independent = [
        activity for activity in rec.activities if activity.exposure_mode == "independent"
    ]
    assert independent, "补证组应保留一次独立诊断"
    assert all("诊断" in activity.purpose for activity in independent)


# --------------------------------------------------------------------------- #
# 4. transfer 必须有迁移/独立检查活动
# --------------------------------------------------------------------------- #


def test_transfer_group_has_transfer_or_independent_activity() -> None:
    finding = make_finding(description="得分率0.95，满分题比例0.90，有效不同样题6道")
    result = planner.build_plan(
        build_inputs(
            findings=[finding],
            metrics=high_score_metrics(
                score_ratio=0.95, full_count=5, full_total=6, valid_items=6
            ),
            materials=advanced_materials(),
        )
    )
    rec = result.bundle.recommendations[0]
    assert rec.strategy_types == ["transfer"]
    assert any(
        activity.exposure_mode == "independent" for activity in rec.activities
    ), "transfer 组必须有独立检查活动"
    assert any("迁移" in activity.purpose for activity in rec.activities)
    assert "迁移" in rec.reason
    group = result.bundle.task_groups[0]
    assert group.primary_strategy == "transfer"
    assert [item.exposure_mode for item in rec.activities] != [
        "practice"
    ] * len(rec.activities)


def test_transfer_degrades_to_consolidation_without_transfer_material() -> None:
    finding = make_finding(description="得分率0.95，满分题比例0.90，有效不同样题6道")
    result = planner.build_plan(
        build_inputs(
            findings=[finding],
            metrics=high_score_metrics(
                score_ratio=0.95, full_count=5, full_total=6, valid_items=6
            ),
            materials=basic_materials(),
        )
    )
    rec = result.bundle.recommendations[0]
    assert rec.strategy_types == ["consolidation"], "没有迁移题时必须降级"
    assert result.bundle.task_groups[0].primary_strategy == "consolidation"
    assert all(
        activity.exposure_mode == "practice" for activity in rec.activities
    ), "降级后不能仍标独立迁移"
    assert "巩固" in rec.reason
    assert any(
        "降级为「consolidation」" in item for item in result.bundle.limitations
    ), "降级必须写明限制"


# --------------------------------------------------------------------------- #
# 5. D14 类似输入整体不再产生矛盾理由
# --------------------------------------------------------------------------- #


def test_d14_like_plan_has_no_contradictory_reason() -> None:
    finding = make_finding(error_types=["transfer_difficulty"])
    result = planner.build_plan(
        build_inputs(
            findings=[finding],
            metrics=high_score_metrics(),
            materials=basic_materials(),
        )
    )
    bundle = result.bundle
    assert bundle.recommendations
    assert len(bundle.task_groups) == len(bundle.recommendations)

    for rec, group in zip(bundle.recommendations, bundle.task_groups):
        assert group.primary_strategy == rec.strategy_types[0], "组策略与建议策略必须一致"
        assert rec.strategy_types[0] != "error_correction", "高分迁移类信号不得写成纠错"
        assert CONCRETE_REASON not in rec.reason, "证据不支持时不得写具体错误证据"
        assert rec.strategy_types[0] == "consolidation"
        assert "练稳" in rec.student_intro
        assert "变式" in rec.reason
    assert any(
        "不按具体错误纠错" in item for item in bundle.limitations
    ), "高分只有迁移类信号时，方案限制要说明为何不按具体错误处理"
    assert planner.review_plan_rules(bundle).status == "validated"
