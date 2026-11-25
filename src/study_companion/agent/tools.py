"""Role- and task-scoped adapters for existing B tools."""

from __future__ import annotations

from typing import Any

from study_companion.agent.loop import RegisteredTool, ToolRegistry
from study_companion.agent.tasks import TaskSpec
from study_companion.contracts import DomainError
from study_companion.tools.b_analysis import (
    ServerContext, get_answer_evidence, get_class_analysis,
    get_quality_issues, get_student_analysis,
)


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


def build_b_registry(ctx: ServerContext, role: str, task: TaskSpec | None = None) -> ToolRegistry:
    tools: list[RegisteredTool] = []
    allowed = set(task.tools) if task is not None else None
    student_id = ctx.bound_student_id or (next(iter(ctx.allowed_student_ids)) if role == "student" and len(ctx.allowed_student_ids) == 1 else None)

    def add(tool: RegisteredTool) -> None:
        name = tool.schema["function"]["name"]
        if (allowed is None or name in allowed) and (role == "teacher" or name != "get_class_analysis"):
            tools.append(tool)

    add(RegisteredTool(
        _schema("get_student_analysis", "读取绑定目标学生和节点的分析结果",
                {"student_id": {"type": "string"}, "node_id": {"type": "string"}},
                [] if ctx.bound_student_id and ctx.bound_node_id else ["student_id", "node_id"]),
        lambda a: get_student_analysis(ctx, a.get("_call_id", "model"),
            a.get("student_id", ctx.bound_student_id), a.get("node_id", ctx.bound_node_id)),
    ))
    add(RegisteredTool(
        _schema("get_answer_evidence", "读取已验证分析引用中的作答证据",
                {"record_ids": {"type": "array", "items": {"type": "string"}}}, ["record_ids"]),
        lambda a: get_answer_evidence(ctx, a.get("_call_id", "model"), a["record_ids"]),
    ))

    def quality_query(args: dict[str, Any]):
        if role == "student" and student_id is None:
            raise DomainError("CONTEXT_MISMATCH", "学生质量查询缺少唯一会话范围")
        return get_quality_issues(ctx, args.get("_call_id", "model"),
            args.get("student_id", student_id), args.get("page", 1))

    add(RegisteredTool(
        _schema("get_quality_issues", "读取当前目标范围的数据质量问题",
                {"student_id": {"type": "string"}, "page": {"type": "integer"}}, []),
        quality_query,
    ))
    add(RegisteredTool(
        _schema("get_class_analysis", "读取绑定班级和节点的分析结果",
                {"class_id": {"type": "string"}, "node_id": {"type": "string"}},
                [] if ctx.bound_class_id and ctx.bound_node_id else ["class_id", "node_id"]),
        lambda a: get_class_analysis(ctx, a.get("_call_id", "model"),
            a.get("class_id", ctx.bound_class_id), a.get("node_id", ctx.bound_node_id)),
    ))
    return ToolRegistry(tools)
