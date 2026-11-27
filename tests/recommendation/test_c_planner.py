"""C 规划层：难度、指纹、候选评分、规划器（全部合成数据，不调用模型）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from study_companion.contracts import (
    Catalog,
    EvidenceRef,
    Finding,
    Material,
    Metric,
    MetricScope,
    RubricPoint,
    SourceLocator,
    StudentResult,
    StrategySignal,
    Topic,
    Indicator,
)
from study_companion.recommendation import candidates, difficulty, fingerprint, hints, planner


def make_material(
    material_id: str,
    *,
    content: str,
    indicator: str = "T01I1",
    question_type: str = "choice",
    answer: str | None = "B",
    explanation: str | None = None,
    rubric: list[RubricPoint] | None = None,
    max_score: int = 5,
) -> Material:
    return Material(
        material_id=material_id,
        material_type="question",
        topic_ids=["T01"],
        indicator_ids=[indicator],
        content=content,
        source_locator=SourceLocator(source_file="题目与材料.csv", verification_status="unverified"),
        reference_answer=answer,
        reference_explanation=explanation,
        max_score=max_score,
        rubric_points=rubric or [],
        question_type=question_type,  # type: ignore[arg-type]
    )


def make_catalog(materials: list[Material]) -> Catalog:
    return Catalog(
        version="cat-1",
        topics=[Topic(topic_id="T01", title="一元一次方程")],
        indicators=[Indicator(indicator_id="T01I1", topic_id="T01", description="去括号时的符号处理")],
        materials=materials,
        links=[],
    )


def make_result(findings: list[Finding], *, signals: list[StrategySignal] | None = None) -> StudentResult:
    return StudentResult(
        run_id="run-1",
        dataset_id="ds-1",
        catalog_version="cat-1",
        student_id="S01",
        class_id="DC01",
        node_id="N01",
        metrics=[],
        findings=findings,
        limitations=[],
        analysis_version="ban-1",
        strategy_signals=signals or [],
    )


def make_finding(
    finding_id: str,
    *,
    evidence_status: str = "sufficient",
    performance: str = "needs_practice",
    error_types: list[str] | None = None,
) -> Finding:
    return Finding(
        finding_id=finding_id,
        indicator_id="T01I1",
        description="去括号时漏乘第二项",
        evidence_status=evidence_status,  # type: ignore[arg-type]
        performance=performance,  # type: ignore[arg-type]
        evidence_refs=[EvidenceRef(kind="record", id="R01", run_id="run-1")],
        limitations=[],
        error_types=error_types or [],  # type: ignore[arg-type]
    )


def knowledge_payload(*, with_prereq: bool = False) -> dict:
    relations = []
    if with_prereq:
        relations.append(
            {"relation_id": "rel-1", "type": "prerequisite", "source_ids": ["kp-pre"], "target_id": "kp-T01I1"}
        )
    return {
        "points": [
            {
                "kp_id": "kp-T01I1",
                "name": "去括号时的符号处理",
                "definition": "括号前是负号时，括号内每一项都要变号",
                "scope_role": "core",
                "indicator_ids": ["T01I1"],
                "source_locators": [],
            },
            {
                "kp_id": "kp-pre",
                "name": "负数乘法",
                "definition": "负数乘正数得负",
                "scope_role": "prerequisite",
                "indicator_ids": ["T01I0"],
                "source_locators": [],
            },
        ],
        "relations": relations,
        "annotations": [
            {"annotation_id": "ann-Q1", "source_type": "material", "source_id": "Q1", "target_ids": ["kp-T01I1"], "knowledge_role": "primary"},
            {"annotation_id": "ann-Q2", "source_type": "material", "source_id": "Q2", "target_ids": ["kp-T01I1"], "knowledge_role": "primary"},
            {"annotation_id": "ann-Q3", "source_type": "material", "source_id": "Q3", "target_ids": ["kp-T01I1"], "knowledge_role": "supporting"},
            {"annotation_id": "ann-Q4", "source_type": "material", "source_id": "Q4", "target_ids": ["kp-T01I1"], "knowledge_role": "primary"},
        ],
    }


# --------------------------------------------------------------------------- #
# 难度
# --------------------------------------------------------------------------- #


def test_difficulty_formula_matches_design_examples() -> None:
    simple = make_material("Q1", content="化简 -(2x-7) 的结果是（）")
    features = difficulty.extract_features(simple, primary_kp_count=1)
    assert features is not None
    score = difficulty.complexity(features)
    assert score <= difficulty.BASIC_MAX
    assert difficulty.level_for(score) == "basic"

    complex_material = make_material(
        "Q2",
        content="某工程队计划用 5 天修完一段路，实际每天多修 2 米，结果提前 1 天完成，求原计划每天修多少米？",
        question_type="solution",
    )
    complex_features = difficulty.extract_features(complex_material, primary_kp_count=2)
    assert complex_features is not None
    score = difficulty.complexity(complex_features)
    assert score >= 3, "建模 + 多步应至少进入中等档"
    assert difficulty.level_for(score) in {"intermediate", "advanced"}


def test_difficulty_returns_none_for_blank_content() -> None:
    blank = make_material("Q9", content="")
    assert difficulty.assess(blank) is None


def test_estimated_minutes_solution_adds_two() -> None:
    assessment = difficulty.assess(make_material("Q1", content="化简 -(2x-7) 的结果是（）"))
    assert difficulty.estimated_minutes(assessment, solution=False) == 2
    assert difficulty.estimated_minutes(assessment, solution=True) == 4


# --------------------------------------------------------------------------- #
# 指纹与近变式
# --------------------------------------------------------------------------- #


def test_digit_only_clone_is_near_variant() -> None:
    left = fingerprint.build_fingerprint(make_material("Q1", content="化简 -(2x-7) 的结果是（）"))
    right = fingerprint.build_fingerprint(make_material("Q2", content="化简 -(3x-9) 的结果是（）"))
    assert fingerprint.is_near_variant(left, right)
    # 只改数字时结构一致即判同簇；S 分数在方法标签/知识点为空时由结构规则兜底。
    assert fingerprint.similarity(left, right) > 0.5


def test_different_structure_is_not_near_variant() -> None:
    left = fingerprint.build_fingerprint(make_material("Q1", content="化简 -(2x-7) 的结果是（）"))
    right = fingerprint.build_fingerprint(
        make_material("Q3", content="不等式 x>2 在数轴上表示正确的是（）", indicator="T02I1")
    )
    assert not fingerprint.is_near_variant(left, right)


def test_cluster_counts_group_variants() -> None:
    items = [
        fingerprint.build_fingerprint(make_material("Q1", content="化简 -(2x-7) 的结果是（）")),
        fingerprint.build_fingerprint(make_material("Q2", content="化简 -(3x-9) 的结果是（）")),
        fingerprint.build_fingerprint(make_material("Q3", content="不等式 x>2 在数轴上表示正确的是（）")),
    ]
    counts = fingerprint.cluster_counts(items)
    assert sorted(counts, reverse=True)[0] == 2


# --------------------------------------------------------------------------- #
# 候选评分
# --------------------------------------------------------------------------- #


def _pool(history: candidates.LearningHistoryView, materials: list[Material], *, allowed=None) -> candidates.CandidatePool:
    return candidates.CandidatePool(
        catalog=make_catalog(materials),
        knowledge_payload=knowledge_payload(),
        node_available_ids=allowed,
        history=history,
        now=datetime(2026, 11, 27, tzinfo=UTC),
    )


def test_completed_without_retry_reason_is_excluded() -> None:
    material = make_material("Q1", content="化简 -(2x-7) 的结果是（）")
    history = candidates.LearningHistoryView(
        items=[
            candidates.HistoryItem(
                material_id="Q1",
                origin="online",
                status="graded",
                occurred_at=datetime(2026, 11, 25, tzinfo=UTC),
                score=5,
                max_score=5,
            )
        ]
    )
    pool = _pool(history, [material])
    result = candidates.select_for_need(
        need={"indicator_id": "T01I1", "kp_id": "kp-T01I1"},
        pool=pool,
        strategy="consolidation",
        finding=None,
        remaining_minutes=20,
        max_activities=8,
        config_minutes=20,
    )
    assert not result.selected
    assert any(
        item["reason"] in {"COMPLETED_WITHOUT_RETRY_REASON", "RECENTLY_RETRIED"}
        for item in result.rejected
    ), "已完成且不满足重练条件的题必须被排除"


def test_spaced_review_allows_old_material() -> None:
    material = make_material("Q1", content="化简 -(2x-7) 的结果是（）")
    history = candidates.LearningHistoryView(
        items=[
            candidates.HistoryItem(
                material_id="Q1",
                origin="online",
                status="graded",
                occurred_at=datetime(2026, 11, 1, tzinfo=UTC),
                score=5,
                max_score=5,
            )
        ]
    )
    pool = _pool(history, [material])
    result = candidates.select_for_need(
        need={"indicator_id": "T01I1", "kp_id": "kp-T01I1"},
        pool=pool,
        strategy="consolidation",
        finding=None,
        remaining_minutes=20,
        max_activities=8,
        config_minutes=20,
    )
    assert result.selected
    assert result.selected[0].repeat_reason == "spaced_review"
    assert result.selected[0].parts["H"] == 0.45


def test_node_allowlist_blocks_unavailable_material() -> None:
    material = make_material("Q1", content="化简 -(2x-7) 的结果是（）")
    pool = _pool(candidates.LearningHistoryView(), [material], allowed=["Q-other"])
    result = candidates.select_for_need(
        need={"indicator_id": "T01I1", "kp_id": "kp-T01I1"},
        pool=pool,
        strategy="consolidation",
        finding=None,
        remaining_minutes=20,
        max_activities=8,
        config_minutes=20,
    )
    assert not result.selected
    assert any(item["reason"] == "NOT_AVAILABLE_IN_NODE" for item in result.rejected)


def test_cluster_limit_caps_two_variants() -> None:
    materials = [
        make_material("Q1", content="化简 -(2x-7) 的结果是（）"),
        make_material("Q2", content="化简 -(3x-9) 的结果是（）"),
        make_material("Q3", content="化简 -(4x-11) 的结果是（）"),
    ]
    pool = _pool(candidates.LearningHistoryView(), materials)
    result = candidates.select_for_need(
        need={"indicator_id": "T01I1", "kp_id": "kp-T01I1"},
        pool=pool,
        strategy="consolidation",
        finding=None,
        remaining_minutes=20,
        max_activities=8,
        config_minutes=20,
    )
    assert len(result.selected) <= 2, "同一近变式簇默认最多两题"


# --------------------------------------------------------------------------- #
# 规划器
# --------------------------------------------------------------------------- #


def test_planner_builds_two_groups_with_hints_and_evidence() -> None:
    materials = [
        make_material("Q1", content="化简 -(2x-7) 的结果是（）", explanation="把负号乘进每一项"),
        make_material("Q2", content="化简 -(3x-5) 的结果是（）"),
        make_material(
            "Q3",
            content="解方程 2(x-3)+1=5",
            question_type="solution",
            answer="x=5",
            rubric=[
                RubricPoint(point_id="P1", description="去括号正确", max_score=2),
                RubricPoint(point_id="P2", description="求解正确", max_score=3),
            ],
        ),
    ]
    inputs = planner.PlannerInputs(
        student_result=make_result([make_finding("F1", error_types=["step_error"])]),
        catalog=make_catalog(materials),
        knowledge_payload=knowledge_payload(),
        history=candidates.LearningHistoryView(),
        now=datetime(2026, 11, 27, tzinfo=UTC),
    )
    result = planner.build_plan(inputs)
    bundle = result.bundle
    assert bundle.recommendations, "应至少生成一个任务组"
    assert len(bundle.recommendations) <= inputs.config.max_roots
    first = bundle.recommendations[0]
    assert first.strategy_types == ["error_correction"]
    assert first.finding_ids == ["F1"]
    assert first.reason.startswith("存在具体错误证据")
    for activity in first.activities:
        assert len(activity.hint_levels) == 3
        assert all(hint.strip() for hint in activity.hint_levels)
        material = next(item for item in materials if item.material_id == activity.material_id)
        assert not hints.hints_are_safe(material, activity.hint_levels)
    total_minutes = sum(
        activity.estimate_minutes for rec in bundle.recommendations for activity in rec.activities
    )
    assert total_minutes <= inputs.config.round_minutes


def test_priority_merges_one_target_and_prefers_evidence_first() -> None:
    inputs = planner.PlannerInputs(
        student_result=make_result(
            [
                make_finding("F1", evidence_status="insufficient", performance="undetermined"),
                make_finding("F2", error_types=["step_error"]),
            ]
        ),
        catalog=make_catalog([make_material("Q1", content="化简 -(2x-7) 的结果是（）")]),
        knowledge_payload=knowledge_payload(),
        history=candidates.LearningHistoryView(),
        now=datetime(2026, 11, 27, tzinfo=UTC),
    )
    needs = planner.rank_needs(planner.aggregate_needs(inputs), inputs)
    assert len(needs) == 1, "同目标的多条诊断应合并成一个教学需求"
    assert needs[0].primary_strategy == "collect_evidence", "04 §3.1：补证优先于纠错"
    assert "error_correction" in needs[0].auxiliary_strategies
    assert set(needs[0].finding_ids) == {"F1", "F2"}


def test_priority_is_higher_for_core_weak_target() -> None:
    weak = make_finding("F1", error_types=["step_error"], performance="needs_practice")
    uncertain = make_finding("F2", evidence_status="insufficient", performance="undetermined")
    weak.indicator_id = "T01I2"
    uncertain.indicator_id = "T01I3"
    payload = knowledge_payload()
    payload["points"].extend(
        [
            {
                "kp_id": "kp-T01I2",
                "name": "移项",
                "definition": "移项要变号",
                "scope_role": "core",
                "indicator_ids": ["T01I2"],
                "source_locators": [],
            },
            {
                "kp_id": "kp-T01I3",
                "name": "系数化为 1",
                "definition": "两边同除以系数",
                "scope_role": "prerequisite",
                "indicator_ids": ["T01I3"],
                "source_locators": [],
            },
        ]
    )
    inputs = planner.PlannerInputs(
        student_result=make_result([weak, uncertain]),
        catalog=make_catalog(
            [
                make_material("Q1", content="化简 -(2x-7) 的结果是（）", indicator="T01I2"),
                make_material("Q2", content="解方程 3x=12", indicator="T01I3"),
            ]
        ),
        knowledge_payload=payload,
        history=candidates.LearningHistoryView(),
        now=datetime(2026, 11, 27, tzinfo=UTC),
    )
    needs = planner.rank_needs(planner.aggregate_needs(inputs), inputs)
    assert needs[0].indicator_id == "T01I2", "核心知识点 + 已确认薄弱应排在前"
    assert needs[0].priority > needs[-1].priority


def test_planner_keeps_teacher_intervention_group_parallel() -> None:
    signal = StrategySignal(
        signal_id="SIG1",
        student_id="S01",
        indicator_id="T01I1",
        kp_id="kp-T01I1",
        signal_type="teacher_attention",
        limitations=["多轮无改善"],
    )
    inputs = planner.PlannerInputs(
        student_result=make_result([make_finding("F1")], signals=[signal]),
        catalog=make_catalog([make_material("Q1", content="化简 -(2x-7) 的结果是（）")]),
        knowledge_payload=knowledge_payload(),
        history=candidates.LearningHistoryView(),
        now=datetime(2026, 11, 27, tzinfo=UTC),
    )
    result = planner.build_plan(inputs)
    strategies = [rec.strategy_types[0] for rec in result.bundle.recommendations]
    assert "teacher_intervention" in strategies
