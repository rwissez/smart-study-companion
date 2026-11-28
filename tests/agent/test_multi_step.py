"""受控多步骤编排的契约测试（05 §5.2—§5.3）。

全部使用 fake provider 与 fake 步骤执行器，不调用真实模型、数据库或网络。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from study_companion.agent.orchestrator import (
    MAX_STEPS,
    PlanStep,
    RoutePlan,
    StepOutcome,
    allowed_step_tools,
    assert_step_tool_allowed,
    build_route_plan,
    execute_plan,
    plan_route,
    to_route_decision,
    validate_route_steps,
)
from study_companion.agent.tasks import TASKS
from study_companion.agent.types import ModelTurn
from study_companion.contracts import DomainError


@dataclass
class FakeProvider:
    """只回应脚本化轮次；任何未预期的模型调用都暴露为断言失败。"""

    turns: list[ModelTurn] = field(default_factory=list)
    calls: int = 0
    offered_tools: list[list[str]] = field(default_factory=list)

    def generate(self, messages, tools=None, response_schema=None) -> ModelTurn:
        self.calls += 1
        self.offered_tools.append([item["function"]["name"] for item in tools or []])
        assert self.turns, "fake provider 没有更多脚本轮次"
        return self.turns.pop(0)


@dataclass
class FakeResult:
    stopped_reason: str = "model_finished"


def _messages(text: str) -> list[dict]:
    return [{"role": "user", "content": text}]


def _teacher_plan() -> RoutePlan:
    plan = plan_route(
        _messages("先看学生 S01 的学情，再按结果生成方案候选"),
        FakeProvider(),
        "teacher",
    )
    assert plan.status == "ready"
    return plan


def test_teacher_composite_request_routes_to_two_step_dependency() -> None:
    provider = FakeProvider()
    plan = plan_route(
        _messages("先看学生 S01 的学情，再按结果生成方案候选"),
        provider,
        "teacher",
    )

    assert plan.status == "ready"
    assert [step.step_key for step in plan.steps] == ["s1", "s2"]
    assert [step.task_type for step in plan.steps] == ["student_progress", "plan_revision"]
    assert plan.steps[1].depends_on == ("s1",)
    assert plan.steps[1].operation == "propose"
    assert plan.steps[1].task.task_type == "study_plan"
    assert plan.explicit_write is True
    # 规则可确定时不得额外调用模型做拆解。
    assert provider.calls == 0


def test_two_step_execution_passes_first_result_and_reports_statuses() -> None:
    plan = _teacher_plan()
    calls: list[tuple[str, list[str], list[str | None]]] = []
    events: list[dict] = []

    def run_step(step, dependencies):
        calls.append(
            (step.step_key, [item.step.step_key for item in dependencies], [item.text for item in dependencies])
        )
        if step.step_key == "s1":
            return StepOutcome(text="S01 学情：有理数计算仍需练习", result=FakeResult())
        assert dependencies[0].text == "S01 学情：有理数计算仍需练习"
        return StepOutcome(text="方案候选 P1 已生成，等待教师确认", result=FakeResult())

    execution = execute_plan(plan, run_step, on_event=events.append)

    assert [item.status for item in execution.results] == ["succeeded", "succeeded"]
    assert calls == [
        ("s1", [], []),
        ("s2", ["s1"], ["S01 学情：有理数计算仍需练习"]),
    ]
    assert execution.text == "S01 学情：有理数计算仍需练习\n\n方案候选 P1 已生成，等待教师确认"
    assert execution.stopped_reason == "model_finished"
    assert execution.explanation == ""

    first = [item["status"] for item in events if item["step_key"] == "s1"]
    second = [item["status"] for item in events if item["step_key"] == "s2"]
    assert first == ["pending", "running", "succeeded"]
    assert second == ["pending", "running", "succeeded"]


def test_dependency_failure_stops_dependent_step_and_explains() -> None:
    plan = _teacher_plan()
    calls: list[str] = []
    events: list[dict] = []

    def run_step(step, dependencies):
        calls.append(step.step_key)
        assert step.step_key == "s1", "依赖失败的步骤不得执行"
        raise DomainError("TOOL_FAILED", "读取正式学情失败")

    execution = execute_plan(plan, run_step, on_event=events.append)

    assert [item.status for item in execution.results] == ["failed", "skipped"]
    assert calls == ["s1"]
    assert execution.stopped_reason == "step_failed"
    assert "读取正式学情失败" in execution.explanation
    assert "跳过" in execution.explanation
    assert "没有发布任何方案" in execution.explanation
    second_events = [item["status"] for item in events if item["step_key"] == "s2"]
    assert second_events == ["pending", "skipped"]
    # 第二步没有产出，最终文本不能假装完成。
    assert execution.text == ""


def test_independent_step_continues_after_another_step_fails() -> None:
    steps = (
        PlanStep("s1", TASKS["student_progress"], "student_progress", "read", (), "查看学情"),
        PlanStep("s2", TASKS["student_progress"], "student_progress", "read", (), "查看另一项学情"),
    )
    plan = build_route_plan(steps, role="teacher")

    def run_step(step, dependencies):
        if step.step_key == "s1":
            raise DomainError("TOOL_FAILED", "第一项读取失败")
        return StepOutcome(text="第二项读取成功", result=FakeResult())

    execution = execute_plan(plan, run_step)

    assert [item.status for item in execution.results] == ["failed", "succeeded"]
    assert execution.text == "第二项读取成功"


def test_route_rejects_more_than_six_steps() -> None:
    steps = [
        PlanStep(
            f"s{index + 1}",
            TASKS["student_progress"],
            "student_progress",
            "read",
            (f"s{index}",) if index else (),
            f"步骤 {index + 1}",
        )
        for index in range(MAX_STEPS + 1)
    ]

    with pytest.raises(DomainError) as exc:
        validate_route_steps(steps)
    assert exc.value.code == "VALIDATION_ERROR"
    assert "6" in str(exc.value)

    with pytest.raises(DomainError) as exc_plan:
        build_route_plan(steps, role="teacher")
    assert exc_plan.value.code == "VALIDATION_ERROR"


def test_route_rejects_cycle_and_missing_dependency() -> None:
    cyclic = (
        PlanStep("s1", TASKS["student_progress"], "student_progress", "read", ("s2",), "步骤一"),
        PlanStep("s2", TASKS["student_progress"], "student_progress", "read", ("s1",), "步骤二"),
    )
    with pytest.raises(DomainError) as exc:
        validate_route_steps(cyclic)
    assert "成环" in str(exc.value)

    missing = (
        PlanStep("s1", TASKS["student_progress"], "student_progress", "read", ("s9",), "步骤一"),
    )
    with pytest.raises(DomainError) as exc_missing:
        validate_route_steps(missing)
    assert "不存在" in str(exc_missing.value)


def test_student_write_request_is_denied_without_model_call() -> None:
    provider = FakeProvider()
    plan = plan_route(_messages("帮我把作业删掉"), provider, "student")

    assert plan.status == "denied"
    assert plan.steps == ()
    assert plan.explicit_write is False
    assert plan.clarification and "只" in plan.clarification and "发布" in plan.clarification
    assert provider.calls == 0


def test_student_cannot_execute_propose_step() -> None:
    propose = PlanStep(
        "s1",
        TASKS["study_plan"],
        "plan_revision",
        "propose",
        (),
        "生成方案候选",
        "plan_revision",
    )
    with pytest.raises(DomainError) as exc:
        build_route_plan([propose], role="student")
    assert exc.value.code == "FORBIDDEN"

    denied = plan_route(_messages("给我生成方案候选"), FakeProvider(), "student")
    assert denied.status == "denied"
    assert denied.steps == ()


def test_step_tool_whitelist_rejects_unlisted_and_student_write_tools() -> None:
    plan = _teacher_plan()
    read_step, propose_step = plan.steps

    assert "prepare_plan_revision" in allowed_step_tools(propose_step, "teacher")
    assert "prepare_plan_revision" not in allowed_step_tools(read_step, "teacher")

    with pytest.raises(DomainError) as exc:
        assert_step_tool_allowed(propose_step, "teacher", "publish_teacher_revision")
    assert exc.value.code == "TOOL_NOT_ALLOWED"

    with pytest.raises(DomainError) as exc_student:
        allowed_step_tools(propose_step, "student")
    assert exc_student.value.code == "FORBIDDEN"


def test_plan_projects_to_route_decision_contract() -> None:
    plan = _teacher_plan()
    decision = to_route_decision(plan)

    assert decision.status == "ready"
    assert [item.step_key for item in decision.requests] == ["s1", "s2"]
    assert [item.task_key for item in decision.requests] == ["learning_progress", "plan_modify"]
    assert decision.requests[1].depends_on == ["s1"]
    assert decision.requests[1].operation == "propose"
    assert decision.explicit_write is True


def test_single_task_request_still_uses_existing_router() -> None:
    provider = FakeProvider()
    plan = plan_route(_messages("S01 为什么失分？"), provider, "teacher")

    assert plan.status == "ready"
    assert len(plan.steps) == 1
    assert plan.is_multi_step is False
    assert plan.steps[0].task_type == "student_loss_explanation"
    assert plan.source == "rule"
    assert provider.calls == 0

    seen: list[str] = []

    def run_step(step, dependencies):
        seen.append(step.step_key)
        return StepOutcome(text="规则路由下的单任务回答", result=FakeResult())

    execution = execute_plan(plan, run_step)
    assert seen == ["s1"]
    assert [item.status for item in execution.results] == ["succeeded"]
    assert execution.text == "规则路由下的单任务回答"


def test_explicit_task_is_single_step_and_still_role_checked() -> None:
    provider = FakeProvider()
    plan = plan_route(
        _messages("帮我看看"),
        provider,
        "teacher",
        explicit_task_type="student_loss_explanation",
    )
    assert len(plan.steps) == 1
    assert plan.source == "explicit"
    assert provider.calls == 0

    with pytest.raises(DomainError) as exc:
        plan_route(
            _messages("帮我看看"),
            FakeProvider(),
            "student",
            explicit_task_type="class_analysis",
        )
    assert exc.value.code == "FORBIDDEN"
