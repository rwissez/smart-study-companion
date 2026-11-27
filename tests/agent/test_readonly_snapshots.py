"""Agent 只读工具：方案与最近事件（05 §6.1）。"""

from __future__ import annotations

from dataclasses import replace

from study_companion.agent.loop import ToolCall
from study_companion.agent.tools import build_b_registry
from study_companion.contracts import DomainError, PreparedDataset
from study_companion.tools.b_analysis import ServerContext

PLAN = {
    "plan_version": "plan-1",
    "node_id": "N01",
    "student_id": "S01",
    "goal": "T01I1",
    "reason": "去括号容易丢符号",
    "activities": [
        {
            "activity_id": "act-1",
            "kind": "question",
            "title": "去括号练习",
            "material_id": "Q1",
            "minutes": 5,
            "exposure_mode": "practice",
        }
    ],
    "limitations": ["不包含参考答案"],
}


def _context(**extras) -> ServerContext:
    return ServerContext(
        run_id="run-1",
        dataset_id="ds-1",
        catalog_version="cat-1",
        allowed_student_ids=frozenset({"S01"}),
        bundle=None,
        dataset=None,  # 只读快照工具不依赖原始数据集
        bound_student_id="S01",
        bound_node_id="N01",
        extras=extras,
    )


def _call(registry, name: str, **args):
    return registry.call(ToolCall(call_id="c1", name=name, arguments=args))


def test_plan_tool_reads_only_server_snapshot() -> None:
    registry = build_b_registry(_context(plans=[PLAN]), "teacher")
    names = {item["function"]["name"] for item in registry.schemas()}
    assert "get_student_plan" in names
    result = _call(registry, "get_student_plan")
    assert result["plans"][0]["plan_version"] == "plan-1"
    assert result["limitations"]


def test_plan_tool_never_returns_answer_fields() -> None:
    leaked = {
        **PLAN,
        "activities": [
            {**PLAN["activities"][0], "reference_answer": "x=3", "rubric_points": [{"point": "去括号"}]}
        ],
    }
    registry = build_b_registry(_context(plans=[leaked]), "teacher")
    payload = str(_call(registry, "get_student_plan"))
    assert "reference_answer" not in payload
    # 快照由服务端构造；即使上层误传，也不会从工具层再暴露 rubric 细节之外的结构
    assert "plan_version" in payload


def test_plan_tool_without_snapshot_returns_not_found() -> None:
    registry = build_b_registry(_context(), "teacher")
    try:
        _call(registry, "get_student_plan")
    except DomainError as exc:
        assert exc.code == "NOT_FOUND"
    else:  # pragma: no cover
        raise AssertionError("没有快照时应明确报错")


def test_recent_events_tool_respects_limit() -> None:
    events = [{"event_type": "answer_submitted", "occurred_at": f"2026-11-27T00:0{i}:00Z"} for i in range(5)]
    registry = build_b_registry(_context(recent_events=events), "student")
    result = _call(registry, "get_recent_learning_events", limit=2)
    assert len(result["events"]) == 2
    assert result["student_id"] == "S01"

def test_lesson_and_intervention_tools_read_snapshots() -> None:
    ctx = _context(
        micro_lessons=[{"activity_id": "act-2", "title": "去括号", "component_count": 3}],
        interventions=[{"case_id": "case-1", "reason_code": "no_improvement_after_rounds"}],
    )
    registry = build_b_registry(ctx, "teacher")
    lessons = _call(registry, "get_micro_lesson")
    assert lessons["lessons"][0]["activity_id"] == "act-2"
    cases = _call(registry, "get_open_interventions")
    assert cases["cases"][0]["case_id"] == "case-1"
