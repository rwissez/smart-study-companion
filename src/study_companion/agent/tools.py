"""Role-scoped adapters for existing B tools.

The model supplies business arguments only. ServerContext and call_id are
injected by this adapter and cannot be changed by the model.
"""

from __future__ import annotations

from typing import Any

from study_companion.agent.loop import RegisteredTool, ToolRegistry
from study_companion.contracts import DomainError
from study_companion.tools.b_analysis import (
    ServerContext,
    get_answer_evidence,
    get_class_analysis,
    get_quality_issues,
    get_student_analysis,
)


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


def build_b_registry(ctx: ServerContext, role: str) -> ToolRegistry:
    tools: list[RegisteredTool] = []
    bound_student_id = next(iter(ctx.allowed_student_ids)) if role == "student" and len(ctx.allowed_student_ids) == 1 else None

    def quality_query(args: dict[str, Any]):
        if role == "student" and bound_student_id is None:
            raise DomainError("CONTEXT_MISMATCH", "学生质量查询缺少唯一会话范围")
        requested_student_id = args.get("student_id")
        if role == "student" and requested_student_id is None:
            requested_student_id = bound_student_id
        return get_quality_issues(
            ctx,
            args.get("_call_id", "model"),
            requested_student_id,
            args.get("page", 1),
        )
    tools.append(RegisteredTool(
        _schema("get_student_analysis", "读取当前范围内学生的确定性分析结果",
                {"student_id": {"type": "string"}, "node_id": {"type": "string"}},
                ["student_id", "node_id"]),
        lambda args: get_student_analysis(ctx, args.get("_call_id", "model"), args["student_id"], args["node_id"]),
    ))
    tools.append(RegisteredTool(
        _schema("get_answer_evidence", "读取当前范围内的作答证据",
                {"record_ids": {"type": "array", "items": {"type": "string"}}}, ["record_ids"]),
        lambda args: get_answer_evidence(ctx, args.get("_call_id", "model"), args["record_ids"]),
    ))
    tools.append(RegisteredTool(
        _schema("get_quality_issues", "读取当前范围内的数据质量问题",
                {"student_id": {"type": "string"}, "page": {"type": "integer"}}, []),
        quality_query,
    ))
    if role == "teacher":
        tools.append(RegisteredTool(
            _schema("get_class_analysis", "读取教师当前数据集的班级分析结果",
                    {"class_id": {"type": "string"}, "node_id": {"type": "string"}},
                    ["class_id", "node_id"]),
            lambda args: get_class_analysis(ctx, args.get("_call_id", "model"), args["class_id"], args["node_id"]),
        ))
    return ToolRegistry(tools)
