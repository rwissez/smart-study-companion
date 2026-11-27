"""C 教师意图优化：成功、非法 id、模型失败、约束冲突与无法满足（04 §5.2）。"""

from __future__ import annotations

from dataclasses import dataclass

from study_companion.contracts import (
    Activity,
    AdjustmentCondition,
    EvidenceRef,
    Recommendation,
    RecommendationBundle,
    RevisionIntent,
)
from study_companion.recommendation.optimize_model import (
    OptimizeCandidate,
    OptimizeChangeOut,
    OptimizeConstraints,
    OptimizeOut,
    optimize_plan_with_model,
)


@dataclass
class Result:
    succeeded: bool
    value: object
    error: dict | None = None


class FakeService:
    def __init__(self, value: OptimizeOut | None) -> None:
        self.value = value
        self.calls: list[str] = []
        self.payloads: list[dict] = []

    def generate_structured(self, request, schema):  # noqa: ANN001
        self.calls.append(request.task_key)
        self.payloads.append(request.input_payload)
        if self.value is None:
            return Result(False, None, {"code": "MODEL_NOT_CONFIGURED"})
        return Result(True, self.value)


def _activity(order: int, *, state: str = "not_started", minutes: int = 4) -> Activity:
    return Activity(
        order=order,
        material_id=f"Q{order}",
        activity_id=f"act-{order}",
        purpose="练「去括号」的符号分配",
        hint_levels=["回忆规则", "把括号前看作乘 -1", "逐项核对符号"],
        title=f"化简 -(2x-{order})",
        estimate_minutes=minutes,
        state=state,  # type: ignore[arg-type]
        knowledge_ids=["kp-1"],
        evidence_refs=[
            EvidenceRef(kind="finding", id="F1", run_id="run-1", analysis_version="ban-1")
        ],
    )


def _bundle(*, first_state: str = "not_started", minutes: int = 4) -> RecommendationBundle:
    recommendation = Recommendation(
        recommendation_id="rec-1",
        indicator_id="T01I1",
        finding_ids=["F1"],
        goal="围绕「去括号时的符号处理」安排本轮学习",
        reason="存在具体错误证据 F1：漏乘第二项",
        activities=[
            _activity(1, state=first_state, minutes=minutes),
            _activity(2, minutes=minutes),
        ],
        adjustment_conditions=[
            AdjustmentCondition(observable_condition="独立题全对", next_action="下一轮升级为变式")
        ],
        strategy_types=["error_correction"],
        kp_ids=["kp-1"],
        evidence_refs=[
            EvidenceRef(kind="finding", id="F1", run_id="run-1", analysis_version="ban-1")
        ],
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


def _intent() -> RevisionIntent:
    return RevisionIntent(goal="减轻书写负担", reason="教师观察到书写时间过长")


def _candidates(*, minutes: int = 4, source: str = "catalog") -> list[OptimizeCandidate]:
    return [
        OptimizeCandidate(
            activity_key="Q3",
            purpose="独立检查同一步骤",
            estimate_minutes=minutes,
            source=source,
            knowledge_ids=("kp-1",),
        )
    ]


def _add_payload() -> OptimizeOut:
    return OptimizeOut(
        kind="proposal",
        reason="先减一道重复练习，再加一道独立检查",
        changes=[
            OptimizeChangeOut(
                op="add",
                group_key="G1",
                activity_key="Q3",
                reason="独立检查能验证是否真的会分配符号",
            )
        ],
    )


def test_valid_proposal_is_returned() -> None:
    service = FakeService(_add_payload())
    outcome = optimize_plan_with_model(
        service=service,  # type: ignore[arg-type]
        intent=_intent(),
        bundle=_bundle(),
        candidates=_candidates(),
        constraints=OptimizeConstraints(max_activities=8, max_minutes=20),
    )

    assert outcome.model_used is True
    assert outcome.kind == "proposal"
    assert len(outcome.changes) == 1
    change = outcome.changes[0]
    assert change.op == "add"
    assert change.group_key == "G1"
    assert change.activity_key == "Q3"
    assert outcome.issues == []
    assert service.calls == ["optimize_plan.v1"]
    payload = service.payloads[0]
    assert [item["activity_key"] for item in payload["activities"]] == ["A1", "A2"]
    assert [item["activity_key"] for item in payload["candidates"]] == ["Q3"]
    assert payload["limits"]["max_minutes"] == 20
    assert payload["limits"]["remaining_minutes"] == 12


def test_fabricated_activity_id_rejects_whole_proposal() -> None:
    service = FakeService(
        OptimizeOut(
            kind="proposal",
            reason="增加一道不存在的题",
            changes=[
                OptimizeChangeOut(
                    op="add",
                    group_key="G1",
                    activity_key="Z9",
                    reason="编造的活动",
                ),
                OptimizeChangeOut(
                    op="reorder",
                    group_key="G1",
                    activity_key="A99",
                    position=1,
                    reason="编造的活动顺序",
                ),
            ],
        )
    )
    outcome = optimize_plan_with_model(
        service=service,  # type: ignore[arg-type]
        intent=_intent(),
        bundle=_bundle(),
        candidates=_candidates(),
        constraints=OptimizeConstraints(),
    )

    assert outcome.kind == "rejected"
    assert outcome.changes == []
    assert [issue.code for issue in outcome.issues] == [
        "UNAUTHORIZED_ACTIVITY_ID",
        "UNAUTHORIZED_ACTIVITY_ID",
    ]
    assert any("整份拒绝" in item for item in outcome.limitations)


def test_model_failure_degrades_without_changes() -> None:
    service = FakeService(None)
    outcome = optimize_plan_with_model(
        service=service,  # type: ignore[arg-type]
        intent=_intent(),
        bundle=_bundle(),
        candidates=_candidates(),
        constraints=OptimizeConstraints(),
    )

    assert outcome.kind == "unavailable"
    assert outcome.model_used is False
    assert outcome.changes == []
    assert outcome.issues == []
    assert outcome.limitations and "MODEL_NOT_CONFIGURED" in outcome.limitations[0]


def test_load_constraint_conflict_rejects_proposal() -> None:
    service = FakeService(_add_payload())
    outcome = optimize_plan_with_model(
        service=service,  # type: ignore[arg-type]
        intent=_intent(),
        bundle=_bundle(),
        candidates=_candidates(minutes=14),
        constraints=OptimizeConstraints(max_activities=8, max_minutes=20),
    )

    assert outcome.kind == "rejected"
    assert outcome.changes == []
    assert [issue.code for issue in outcome.issues] == ["CONSTRAINT_VIOLATION"]
    assert "超过本轮上限" in outcome.issues[0].message


def test_completed_activity_cannot_be_touched() -> None:
    service = FakeService(
        OptimizeOut(
            kind="proposal",
            reason="删掉第一题",
            changes=[
                OptimizeChangeOut(
                    op="remove",
                    group_key="G1",
                    activity_key="A1",
                    reason="第一题已太简单",
                )
            ],
        )
    )
    outcome = optimize_plan_with_model(
        service=service,  # type: ignore[arg-type]
        intent=_intent(),
        bundle=_bundle(first_state="completed"),
        candidates=_candidates(),
        constraints=OptimizeConstraints(),
    )

    assert outcome.kind == "rejected"
    assert outcome.changes == []
    assert outcome.issues[0].code == "COMPLETED_ACTIVITY_PROTECTED"
    assert "已完成" in outcome.issues[0].message


def test_candidate_with_illegal_source_is_rejected() -> None:
    service = FakeService(_add_payload())
    outcome = optimize_plan_with_model(
        service=service,  # type: ignore[arg-type]
        intent=_intent(),
        bundle=_bundle(),
        candidates=_candidates(source="external_unverified"),
        constraints=OptimizeConstraints(),
    )

    assert outcome.kind == "rejected"
    assert outcome.changes == []
    assert outcome.issues[0].code == "UNAUTHORIZED_ACTIVITY_SOURCE"
    assert service.payloads[0]["candidates"] == []


def test_unsatisfiable_is_reported_instead_of_forced_change() -> None:
    service = FakeService(
        OptimizeOut(
            kind="unsatisfiable",
            reason="候选池里没有可用于独立检查的题，无法减少练习量而不丢目标",
            limitations=["可用活动池只有同类基础题"],
        )
    )
    outcome = optimize_plan_with_model(
        service=service,  # type: ignore[arg-type]
        intent=_intent(),
        bundle=_bundle(),
        candidates=_candidates(),
        constraints=OptimizeConstraints(),
    )

    assert outcome.kind == "unsatisfiable"
    assert outcome.model_used is True
    assert outcome.changes == []
    assert "无法减少练习量" in outcome.reason
    assert outcome.limitations == ["可用活动池只有同类基础题"]
