"""案例端到端测试（对应 11.24 三类学生复核材料）。

数据驱动：案例与期望写在 tests/fixtures/three_student_cases.json，
复核时人工核对该文件与《三类学生案例核对表.md》逐条对照。
"""

import json
from pathlib import Path

import pytest

from study_companion.contracts import (
    Catalog,
    RecommendationConfig,
    RunContext,
    StudentResult,
)
from study_companion.recommendation import (
    classify_finding,
    recommend_rules,
    validate_recommendations,
)

BASE = Path(__file__).parents[1] / "fixtures"
CASES = BASE / "three_student_cases.json"
MINIMAL = BASE / "recommendation_minimal.json"


def load_cases() -> dict:
    return json.loads(CASES.read_text(encoding="utf-8"))


def make_catalog() -> Catalog:
    return Catalog.model_validate(
        json.loads(MINIMAL.read_text(encoding="utf-8"))["catalog"]
    )


def make_context() -> RunContext:
    return RunContext.model_validate(
        json.loads(MINIMAL.read_text(encoding="utf-8"))["run_context"]
    )


def run_case(case: dict):
    catalog = make_catalog()
    context = make_context()
    analysis = StudentResult.model_validate(case["student_result"])
    bundle = recommend_rules(analysis, catalog, context, RecommendationConfig())
    return bundle, analysis, catalog


@pytest.mark.parametrize("case", load_cases()["students"], ids=lambda c: c["case_id"])
def test_case(case: dict):
    expect = case["expect"]
    bundle, analysis, catalog = run_case(case)

    assert len(bundle.recommendations) == 1
    rec = bundle.recommendations[0]

    # 分类必须与案例设定一致
    assert classify_finding(analysis.findings[0]) == expect["kind"]

    # 理由必须包含案例要求的关键表述
    for token in expect.get("reason_contains", []):
        assert token in rec.reason, f"{case['case_id']} 理由缺少关键表述：{token}"

    # 材料使用约束
    if "activity_materials_any" in expect:
        used = {a.material_id for a in rec.activities}
        assert used & set(expect["activity_materials_any"]), (
            f"{case['case_id']} 未使用期望材料"
        )

    if "support_first" in expect:
        assert rec.activities[0].material_id in set(expect["support_first"]), (
            f"{case['case_id']} 首个活动必须是扶助材料（学习卡/教材）"
        )

    if expect.get("require_thinking_task"):
        assert any("思想迁移" in a.purpose for a in rec.activities), (
            f"{case['case_id']} 缺少思想迁移总结任务"
        )

    # 全部建议必须通过证据校验
    validation = validate_recommendations(bundle, analysis, catalog)
    assert validation.valid, [i.message for i in validation.issues]
