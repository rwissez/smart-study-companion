"""C 模块测试：四类判定、选材过滤、建议生成与证据校验。

运行方式（合并进仓库后）：
    uv run pytest tests/recommendation tests/contracts -q
"""

import json
from pathlib import Path

import pytest

from study_companion.contracts import (
    Activity,
    Catalog,
    DomainError,
    EvidenceRef,
    Finding,
    MaterialSearch,
    Metric,
    MetricScope,
    RecommendationBundle,
    RecommendationConfig,
    RunContext,
    StudentResult,
)
from study_companion.recommendation import (
    classify_finding,
    recommend_rules,
    search_materials,
    validate_recommendations,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "recommendation_minimal.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def make_catalog() -> Catalog:
    return Catalog.model_validate(load_fixture()["catalog"])


def make_context() -> RunContext:
    return RunContext.model_validate(load_fixture()["run_context"])


def make_analysis(findings: list[Finding]) -> StudentResult:
    base = load_fixture()["student_base"]
    metric = Metric(
        metric_id="M001",
        name="得分率",
        value=round(1 / 3, 4),
        numerator=1,
        denominator=3,
        unit="ratio",
        scope=MetricScope(student_id="S01", node_id="N01"),
        evidence_refs=[EvidenceRef(kind="metric", id="M001", run_id="run-demo-1")],
    )
    return StudentResult.model_validate(
        {**base, "metrics": [metric.model_dump(mode="json")], "findings": [f.model_dump(mode="json") for f in findings]}
    )


# ---------------------------------------------------------------------------
# finding 构造器（对应剧本四类）
# ---------------------------------------------------------------------------

def finding_1a() -> Finding:
    return Finding(
        finding_id="F001",
        indicator_id="T01I1",
        description="有效作答3题中2题在除以负数后未翻转不等号方向，解答过程显示移项正确",
        evidence_status="sufficient",
        performance="needs_practice",
        evidence_refs=[EvidenceRef(kind="record", id="R12", run_id="run-demo-1")],
        limitations=[],
    )


def finding_1b() -> Finding:
    return Finding(
        finding_id="F002",
        indicator_id="T01I1",
        description="该生三次作答均无有效解题步骤",
        evidence_status="sufficient",
        performance="needs_practice",
        evidence_refs=[EvidenceRef(kind="record", id="R20", run_id="run-demo-1")],
        limitations=["缺少解答过程"],
    )


def finding_type3() -> Finding:
    return Finding(
        finding_id="F003",
        indicator_id="T01I2",
        description="该指标仅1道有效作答，不足以形成表现结论",
        evidence_status="insufficient",
        performance="undetermined",
        evidence_refs=[],
        limitations=[],
    )


def finding_type2() -> Finding:
    return Finding(
        finding_id="F004",
        indicator_id="T01I2",
        description="两道变式均正确且步骤完整",
        evidence_status="sufficient",
        performance="observed_strength",
        evidence_refs=[EvidenceRef(kind="record", id="R30", run_id="run-demo-1")],
        limitations=[],
    )


# ---------------------------------------------------------------------------
# 分类启发式
# ---------------------------------------------------------------------------

def test_classify_four_types():
    assert classify_finding(finding_1a()) == "1a"
    assert classify_finding(finding_1b()) == "1b"
    assert classify_finding(finding_type3()) == "type3"
    assert classify_finding(finding_type2()) == "type2"


def test_classify_1a_requires_process_signal():
    # 有"需练习"但描述里没有过程信号，且声明缺过程 -> 1b，不冒充 1a
    f = finding_1a()
    f = f.model_copy(update={"description": "三题均答错", "limitations": ["缺少解答过程"]})
    assert classify_finding(f) == "1b"


# ---------------------------------------------------------------------------
# 材料检索
# ---------------------------------------------------------------------------

def test_search_materials_filters_by_indicator():
    catalog = make_catalog()
    result = search_materials(catalog, __import__("study_companion.contracts", fromlist=["MaterialSearch"]).MaterialSearch(indicator_ids=["T01I2"], limit=10))
    assert {m.material_id for m in result} == {"M005", "M006"}


def test_search_materials_filters_by_type_and_limit():
    from study_companion.contracts import MaterialSearch

    catalog = make_catalog()
    result = search_materials(
        catalog,
        MaterialSearch(indicator_ids=["T01I1"], material_types=["question"], limit=1),
    )
    assert len(result) == 1
    assert result[0].material_type == "question"


# ---------------------------------------------------------------------------
# 建议生成：四类路径
# ---------------------------------------------------------------------------

def test_recommend_1a_path():
    bundle = recommend_rules(make_analysis([finding_1a()]), make_catalog(), make_context(), RecommendationConfig())
    assert len(bundle.recommendations) == 1
    rec = bundle.recommendations[0]
    assert "倾听式" in rec.reason
    assert len(rec.activities) == 2
    assert all(a.material_id in {"M001", "M002"} for a in rec.activities)
    validation = validate_recommendations(bundle, make_analysis([finding_1a()]), make_catalog())
    assert validation.valid, [i.message for i in validation.issues]


def test_recommend_1b_declares_revision_limit():
    bundle = recommend_rules(make_analysis([finding_1b()]), make_catalog(), make_context(), RecommendationConfig())
    rec = bundle.recommendations[0]
    # 回头题限制必须如实声明，不编造原题引用
    assert "服务端" in rec.reason or "教师可先从记录列表指定" in rec.reason
    # 扶 + 练：扶用卡片/教材，练用题目
    assert rec.activities[0].material_id in {"M003", "M004"}
    assert rec.activities[1].material_id in {"M001", "M002"}
    validation = validate_recommendations(bundle, make_analysis([finding_1b()]), make_catalog())
    assert validation.valid, [i.message for i in validation.issues]


def test_recommend_type3_observations_no_verdict():
    bundle = recommend_rules(make_analysis([finding_type3()]), make_catalog(), make_context(), RecommendationConfig())
    rec = bundle.recommendations[0]
    # 剧本红线：证据不足时不输出会/不会式结论
    assert "不输出会/不会结论" in rec.reason
    assert len(rec.activities) == 2  # T01I2 下 M005/M006 两道观察题，覆盖两种变式
    validation = validate_recommendations(bundle, make_analysis([finding_type3()]), make_catalog())
    assert validation.valid, [i.message for i in validation.issues]


def test_recommend_type2_two_levels():
    bundle = recommend_rules(make_analysis([finding_type2()]), make_catalog(), make_context(), RecommendationConfig())
    rec = bundle.recommendations[0]
    assert "思想迁移" in rec.activities[1].purpose
    assert "数形结合" in rec.reason or "思想迁移" in rec.reason
    validation = validate_recommendations(bundle, make_analysis([finding_type2()]), make_catalog())
    assert validation.valid, [i.message for i in validation.issues]


def test_recommend_context_mismatch_raises():
    context = make_context().model_copy(update={"run_id": "run-other"})
    with pytest.raises(DomainError):
        recommend_rules(make_analysis([finding_1a()]), make_catalog(), context, RecommendationConfig())


# ---------------------------------------------------------------------------
# 证据校验
# ---------------------------------------------------------------------------

def _valid_bundle() -> tuple[RecommendationBundle, StudentResult, Catalog]:
    analysis = make_analysis([finding_1a()])
    bundle = recommend_rules(analysis, make_catalog(), make_context(), RecommendationConfig())
    return bundle, analysis, make_catalog()


def test_validate_accepts_valid_bundle():
    bundle, analysis, catalog = _valid_bundle()
    result = validate_recommendations(bundle, analysis, catalog)
    assert result.valid, [i.message for i in result.issues]


def test_validate_rejects_unknown_material():
    from study_companion.contracts import Activity

    bundle, analysis, catalog = _valid_bundle()
    broken = bundle.recommendations[0].activities[0].model_copy(update={"material_id": "M999"})
    bad_rec = bundle.recommendations[0].model_copy(update={"activities": [broken]})
    bad_bundle = RecommendationBundle(
        run_id=bundle.run_id,
        dataset_id=bundle.dataset_id,
        catalog_version=bundle.catalog_version,
        student_id=bundle.student_id,
        node_id=bundle.node_id,
        recommendations=[bad_rec],
        validation_issues=[],
    )
    result = validate_recommendations(bad_bundle, analysis, catalog)
    assert not result.valid
    assert any(i.code == "MATERIAL_REF_INVALID" for i in result.issues)


def test_validate_rejects_foreign_finding():
    bundle, analysis, catalog = _valid_bundle()
    bad_rec = bundle.recommendations[0].model_copy(update={"finding_ids": ["F999"]})
    bad_bundle = bundle.model_copy(update={"recommendations": [bad_rec]})
    result = validate_recommendations(bad_bundle, analysis, catalog)
    assert not result.valid
    assert any(i.code == "FINDING_REF_INVALID" for i in result.issues)


def test_validate_rejects_vague_reason():
    bundle, analysis, catalog = _valid_bundle()
    bad_rec = bundle.recommendations[0].model_copy(update={"reason": "多练习"})
    bad_bundle = bundle.model_copy(update={"recommendations": [bad_rec]})
    result = validate_recommendations(bad_bundle, analysis, catalog)
    assert not result.valid
    assert any(i.code == "VAGUE_REASON" for i in result.issues)


def test_validate_rejects_material_out_of_scope():
    from study_companion.contracts import Activity

    bundle, analysis, catalog = _valid_bundle()
    # M005 适配 T01I2，不属于 T01I1 的建议
    bad_act = bundle.recommendations[0].activities[0].model_copy(update={"material_id": "M005"})
    bad_rec = bundle.recommendations[0].model_copy(update={"activities": [bad_act]})
    bad_bundle = bundle.model_copy(update={"recommendations": [bad_rec]})
    result = validate_recommendations(bad_bundle, analysis, catalog)
    assert not result.valid
    assert any(i.code == "MATERIAL_SCOPE_INVALID" for i in result.issues)


def test_validate_rejects_hint_leaking_answer():
    # 15 号文档 §六：提示泄答保存前代码拦截（HINT_LEAKS_ANSWER）
    bundle, analysis, catalog = _valid_bundle()
    leaky_catalog = catalog.model_copy(
        update={
            "materials": [
                m.model_copy(update={"reference_answer": "x<4"}) for m in catalog.materials
            ]
        }
    )
    leaky_act = bundle.recommendations[0].activities[0].model_copy(
        update={"hint_levels": ["直接看答案：2x+3<11 的解是 x<4"]}
    )
    bad_rec = bundle.recommendations[0].model_copy(update={"activities": [leaky_act]})
    bad_bundle = bundle.model_copy(update={"recommendations": [bad_rec]})
    result = validate_recommendations(bad_bundle, analysis, leaky_catalog)
    assert not result.valid
    leak_issues = [i for i in result.issues if i.code == "HINT_LEAKS_ANSWER"]
    assert leak_issues, [i.code for i in result.issues]
    assert "needs_review" in leak_issues[0].message


def test_validate_accepts_hints_when_no_reference_answer():
    # 材料无参考答案时泄答检查静默跳过，正常建议不受影响
    bundle, analysis, catalog = _valid_bundle()
    result = validate_recommendations(bundle, analysis, catalog)
    assert result.valid, [i.message for i in result.issues]
    assert not any(i.code == "HINT_LEAKS_ANSWER" for i in result.issues)
