"""C 整份方案模型独立复核：成功、非法引用、模型失败、检查项不完整（04 §5、§8）。"""

from __future__ import annotations

from dataclasses import dataclass

from study_companion.contracts import (
    Activity,
    AdjustmentCondition,
    EvidenceRef,
    Recommendation,
    RecommendationBundle,
)
from study_companion.recommendation.plan_review_model import (
    PlanCheckOut,
    PlanIssueOut,
    PlanReviewOut,
    review_plan_with_model,
)


@dataclass
class Result:
    succeeded: bool
    value: object
    error: dict | None = None


class FakeService:
    def __init__(self, value: PlanReviewOut | None) -> None:
        self.value = value
        self.calls: list[str] = []
        self.payloads: list[dict] = []

    def generate_structured(self, request, schema):  # noqa: ANN001
        self.calls.append(request.task_key)
        self.payloads.append(request.input_payload)
        if self.value is None:
            return Result(False, None, {"code": "MODEL_TIMEOUT"})
        return Result(True, self.value)


def _refs() -> list[EvidenceRef]:
    return [EvidenceRef(kind="finding", id="F1", run_id="run-1", analysis_version="ban-1")]


def _activity(order: int) -> Activity:
    return Activity(
        order=order,
        material_id=f"Q{order}",
        activity_id=f"act-{order}",
        purpose="练「去括号」的符号分配",
        hint_levels=[
            "回忆括号前是负号时的符号规则",
            "先把减去整个括号看成乘以负一",
            "逐项核对两个乘积的符号",
        ],
        title=f"化简 -(2x-{order})",
        expected_outcome="能说明每一步的符号依据",
        estimate_minutes=4,
        knowledge_ids=["kp-1"],
        evidence_refs=_refs(),
    )


def _bundle() -> RecommendationBundle:
    refs = _refs()
    recommendation = Recommendation(
        recommendation_id="rec-1",
        indicator_id="T01I1",
        finding_ids=["F1"],
        goal="围绕「去括号时的符号处理」安排本轮学习",
        reason="存在具体错误证据 F1：漏乘第二项",
        activities=[_activity(1), _activity(2)],
        adjustment_conditions=[
            AdjustmentCondition(observable_condition="独立题全对", next_action="下一轮升级为变式")
        ],
        strategy_types=["error_correction"],
        kp_ids=["kp-1"],
        evidence_refs=refs,
        group_id="grp-1",
    )
    return RecommendationBundle(
        run_id="run-1",
        dataset_id="ds-1",
        catalog_version="cat-1",
        student_id="S01",
        node_id="N01",
        recommendations=[recommendation],
        validation_issues=[],
        analysis_version="ban-1",
        knowledge_version="kn-1",
    )


def _checks(overrides: dict | None = None) -> list[PlanCheckOut]:
    overrides = overrides or {}
    checks: list[PlanCheckOut] = []
    for check_id in (
        "evidence",
        "target",
        "sequence",
        "load",
        "answer_guard",
        "material_history",
    ):
        result, reason, keys = overrides.get(check_id, ("pass", "本项无问题", []))
        checks.append(
            PlanCheckOut(
                check_id=check_id,
                result=result,
                reason=reason,
                evidence_keys=keys,
            )
        )
    return checks


def test_review_maps_checks_and_issues_to_contract_fields() -> None:
    service = FakeService(
        PlanReviewOut(
            checks=_checks(
                {"material_history": ("fail", "Q1 与最近已用材料重复", ["E1"])}
            ),
            issues=[
                PlanIssueOut(
                    code="MATERIAL_INVALID",
                    severity="error",
                    message="Q1 与历史材料重复，且只换了一个数字",
                    group_key="G1",
                    activity_key="A1",
                    evidence_keys=["E1"],
                ),
                PlanIssueOut(
                    code="EVIDENCE_MISMATCH",
                    severity="error",
                    message="组理由引用的证据只支持错误类型，不支持掌握程度",
                    group_key="G1",
                ),
            ],
        )
    )
    outcome = review_plan_with_model(
        service=service,  # type: ignore[arg-type]
        bundle=_bundle(),
        protected_texts=["受保护题 Q1 的原文"],
        history_material_ids=["Q1"],
        rule_checks=[],
    )

    assert outcome.model_used is True
    assert outcome.limitations == []
    assert [check.code for check in outcome.checks] == [
        "evidence",
        "target",
        "sequence",
        "load",
        "answer_guard",
        "material_history",
    ]
    assert [check.passed for check in outcome.checks] == [True, True, True, True, True, False]
    assert len(outcome.issues) == 2
    material_issue, evidence_issue = outcome.issues
    assert material_issue.code == "MATERIAL_INVALID"
    assert material_issue.severity == "error"
    assert material_issue.recommendation_id == "rec-1"
    assert material_issue.activity_id == "act-1"
    assert [ref.id for ref in material_issue.evidence_refs] == ["F1"]
    # 程序重新裁定严重性：证据类问题不得由模型抬成 error
    assert evidence_issue.code == "EVIDENCE_MISMATCH"
    assert evidence_issue.severity == "warning"
    assert service.calls == ["review_plan.v1"]
    payload = service.payloads[0]
    assert [item["check_id"] for item in payload["required_checks"]] == [
        "evidence",
        "target",
        "sequence",
        "load",
        "answer_guard",
        "material_history",
    ]
    assert [item["activity_key"] for item in payload["plan"]["activities"]] == ["A1", "A2"]
    assert len(payload["plan"]["activities"][0]["hint_levels"]) == 3
    assert payload["plan"]["evidence"][0]["key"] == "E1"
    assert payload["protected_material_contents"] == ["受保护题 Q1 的原文"]
    assert payload["limits"] == {"round_minutes": 20, "max_activities": 8}


def test_unknown_activity_and_evidence_are_rejected() -> None:
    service = FakeService(
        PlanReviewOut(
            checks=_checks(
                {"evidence": ("fail", "依据缺失", ["E99"])}
            ),
            issues=[
                PlanIssueOut(
                    code="SEQUENCE_INVALID",
                    severity="error",
                    message="不存在的活动顺序有误",
                    group_key="G1",
                    activity_key="A99",
                ),
                PlanIssueOut(
                    code="UNCERTAIN",
                    message="依据无法定位",
                    evidence_keys=["E7"],
                ),
            ],
        )
    )
    outcome = review_plan_with_model(service=service, bundle=_bundle())  # type: ignore[arg-type]

    assert outcome.model_used is True
    assert outcome.issues == []
    evidence_check = next(check for check in outcome.checks if check.code == "evidence")
    assert evidence_check.passed is False
    assert "依据不在输入证据集合内" in evidence_check.reason
    assert any("已丢弃" in item for item in outcome.limitations)
    assert any("不在输入证据集合内的依据" in item for item in outcome.limitations)


def test_model_failure_returns_empty_result_with_limitation() -> None:
    service = FakeService(None)
    outcome = review_plan_with_model(service=service, bundle=_bundle())  # type: ignore[arg-type]

    assert outcome.model_used is False
    assert outcome.checks == []
    assert outcome.issues == []
    assert outcome.limitations and "MODEL_TIMEOUT" in outcome.limitations[0]


def test_incomplete_check_set_is_rejected_entirely() -> None:
    checks = _checks()[:5] + [
        PlanCheckOut(check_id="evidence", result="pass", reason="重复的检查项")
    ]
    service = FakeService(PlanReviewOut(checks=checks, issues=[]))
    outcome = review_plan_with_model(service=service, bundle=_bundle())  # type: ignore[arg-type]

    assert outcome.model_used is True
    assert outcome.checks == []
    assert outcome.issues == []
    assert any("未覆盖全部六类检查" in item for item in outcome.limitations)
