"""Rules-first task routing with a single constrained model fallback."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from study_companion.agent.provider import Message, ModelProvider
from study_companion.agent.tasks import TASKS, TaskSpec, tasks_for_role
from study_companion.contracts import DomainError


@dataclass(frozen=True)
class RoutedTask:
    task: TaskSpec
    source: str
    router_prompt_version: str | None = None


# Rules are intentionally phrase-based and conservative. Ambiguous matches go
# to the classifier instead of silently selecting an execution path.
_RULE_PHRASES: dict[str, tuple[str, ...]] = {
    "student_loss_explanation": ("为什么失分", "为什么丢分", "哪里失分", "失分原因", "丢分原因", "错在哪里", "哪一步错"),
    "evidence_lookup": ("作答证据", "作答依据", "查看作答", "原始作答", "答题记录"),
    "data_quality": ("数据质量", "质量问题", "数据异常", "数据错误", "导入问题"),
    "student_progress": ("学习进展", "整体进展", "学习情况概览", "学习表现", "进步情况"),
    "class_analysis": ("班级共性", "班级分析", "全班表现", "班级学情", "全班情况"),
    "class_data_quality": ("班级数据质量", "全班数据质量", "班级质量问题"),
    "student_learning_advice": ("学习建议", "怎么提高", "如何改进", "下一步怎么学", "给学生建议"),
    "study_plan": ("学习计划", "复习计划", "安排练习", "练习计划", "学习安排"),
    "class_strategy": ("班级教学策略", "全班教学策略", "班级巩固策略", "全班怎么教"),
    "knowledge_lookup": ("知识点定位", "教材位置", "在哪一章", "哪一页", "属于哪个单元", "教材定位"),
    "prerequisite_query": ("前置知识", "先学什么", "知识前置", "依赖哪些知识", "前置关系"),
    "material_search": ("找材料", "搜索材料", "推荐材料", "有哪些练习", "查找练习题"),
    "concept_tutoring": ("讲解概念", "解释概念", "教我理解", "怎么理解", "概念是什么"),
    "relation_review": ("审核知识关系", "审阅关系候选", "知识关系候选", "核对关系"),
    "observation_review": ("学习过程回顾", "提示使用情况", "活动完成情况", "学习观察", "练习过程"),
    "out_of_scope": ("写代码", "天气", "讲个笑话", "你是谁", "翻译这句话", "做饭"),
}


def _latest_user_text(messages: list[Message]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            return message["content"].strip()
    raise DomainError("TASK_UNCLEAR", "没有可用于识别任务的用户消息")


def _rule_matches(text: str, role: str | None = None) -> list[TaskSpec]:
    normalized = text.casefold()
    matches = []
    for task_type, phrases in _RULE_PHRASES.items():
        task = TASKS[task_type]
        if role is not None and role not in task.roles:
            continue
        if task_type == "data_quality" and "班级" in normalized:
            continue
        if any(phrase.casefold() in normalized for phrase in phrases):
            matches.append(task)
    return matches


def _classifier_messages(text: str, tasks: tuple[TaskSpec, ...], prompt: str) -> list[Message]:
    choices = ", ".join(task.task_type for task in tasks)
    return [
        {"role": "system", "content": f"{prompt}\n可选 task_type: {choices}"},
        {"role": "user", "content": json.dumps({"request": text}, ensure_ascii=False)},
    ]


def rule_task(text: str, role: str) -> TaskSpec | None:
    matches = _rule_matches(text, role)
    return matches[0] if len(matches) == 1 else None


def _has_forbidden_rule(text: str, role: str) -> bool:
    return any(role not in task.roles for task in _rule_matches(text))


def route_task(
    messages: list[Message],
    provider: ModelProvider,
    role: str,
    *,
    explicit_task_type: str | None = None,
    router_prompt: tuple[str, str] | None = None,
) -> RoutedTask:
    """Choose a task without allowing the model to choose its target or tools."""
    if explicit_task_type is not None:
        task = TASKS.get(explicit_task_type)
        if task is None:
            raise DomainError("VALIDATION_ERROR", f"未知任务类型：{explicit_task_type}")
        if role not in task.roles:
            raise DomainError("FORBIDDEN", "当前角色不能执行该任务")
        return RoutedTask(task, "explicit")

    text = _latest_user_text(messages)
    if _has_forbidden_rule(text, role):
        raise DomainError("FORBIDDEN", "当前角色不能执行该任务")
    matches = _rule_matches(text, role)
    if len(matches) == 1:
        return RoutedTask(matches[0], "rule")

    candidates = tasks_for_role(role)
    prompt, prompt_version = router_prompt or (
        "根据用户请求判断任务类型。只能输出 JSON 对象 {\"task_type\":\"...\"}。"
        "不得选择、猜测或输出学生、班级、数据集标识；不得调用工具。",
        "router-v1",
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"task_type": {"type": "string", "enum": [task.task_type for task in candidates]}},
        "required": ["task_type"],
        "additionalProperties": False,
    }
    turn = provider.generate(
        _classifier_messages(text, candidates, prompt),
        tools=None,
        response_schema=schema,
    )
    if turn.tool_calls or not turn.text:
        raise DomainError("TASK_UNCLEAR", "无法确定请求类型，请明确说明希望完成的任务")
    try:
        data = json.loads(turn.text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise DomainError("TASK_UNCLEAR", "任务分类结果无效，请明确说明希望完成的任务") from exc
    if not isinstance(data, dict) or set(data) != {"task_type"} or not isinstance(data["task_type"], str):
        raise DomainError("TASK_UNCLEAR", "任务分类结果无效，请明确说明希望完成的任务")
    task = TASKS.get(data["task_type"])
    if task is None or role not in task.roles:
        raise DomainError("TASK_UNCLEAR", "无法识别当前角色可执行的任务，请明确说明")
    return RoutedTask(task, "model", prompt_version)
