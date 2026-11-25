"""Task definitions for role-aware chat routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TaskType = Literal[
    "student_loss_explanation",
    "evidence_lookup",
    "data_quality",
    "student_progress",
    "class_analysis",
    "class_data_quality",
    "student_learning_advice",
    "study_plan",
    "class_strategy",
    "knowledge_lookup",
    "prerequisite_query",
    "material_search",
    "concept_tutoring",
    "relation_review",
    "observation_review",
    "out_of_scope",
]

TargetRequirement = Literal["student", "class", "student_or_dataset", "none"]


@dataclass(frozen=True)
class TaskSpec:
    task_type: TaskType
    label: str
    roles: frozenset[str]
    target: TargetRequirement
    requires_run: bool
    requires_node: bool
    tools: tuple[str, ...]
    prompt_file: str
    status: Literal["ready", "pending_tools"]
    unavailable_reason: str = ""


_TASKS = (
    TaskSpec("student_loss_explanation", "学生失分解释", frozenset({"teacher", "student"}), "student", True, True, ("get_student_analysis", "get_answer_evidence"), "student_loss_explanation.md", "ready"),
    TaskSpec("evidence_lookup", "查看作答依据", frozenset({"teacher", "student"}), "student", True, True, ("get_student_analysis", "get_answer_evidence"), "evidence_lookup.md", "pending_tools", "需要显式证据发现/引用步骤后才能独立查询作答"),
    TaskSpec("data_quality", "数据质量说明", frozenset({"teacher", "student"}), "student_or_dataset", True, False, ("get_quality_issues",), "data_quality.md", "ready"),
    TaskSpec("student_progress", "学生整体进展", frozenset({"teacher", "student"}), "student", True, True, ("get_student_analysis",), "student_progress.md", "pending_tools", "需要跨运行历史比较能力；当前单次运行不能证明进步趋势"),
    TaskSpec("class_analysis", "班级共性分析", frozenset({"teacher"}), "class", True, True, ("get_class_analysis",), "class_analysis.md", "ready"),
    TaskSpec("class_data_quality", "班级数据质量", frozenset({"teacher"}), "class", True, False, (), "class_data_quality.md", "pending_tools", "需要经验证的班级质量过滤工具"),
    TaskSpec("student_learning_advice", "学生学习建议", frozenset({"teacher", "student"}), "student", True, True, (), "student_learning_advice.md", "pending_tools", "需要 C 策略和材料工具"),
    TaskSpec("study_plan", "学习计划", frozenset({"teacher", "student"}), "student", True, True, (), "study_plan.md", "pending_tools", "需要知识图谱和 C 策略/材料工具"),
    TaskSpec("class_strategy", "班级教学策略", frozenset({"teacher"}), "class", True, True, (), "class_strategy.md", "pending_tools", "需要 C 班级策略工具"),
    TaskSpec("knowledge_lookup", "知识点与教材定位", frozenset({"teacher", "student"}), "none", False, False, ("locate_knowledge", "get_knowledge_detail"), "knowledge_lookup.md", "ready"),
    TaskSpec("prerequisite_query", "前置知识查询", frozenset({"teacher", "student"}), "none", False, False, ("locate_knowledge", "get_knowledge_relations"), "prerequisite_query.md", "ready"),
    TaskSpec("material_search", "材料检索", frozenset({"teacher", "student"}), "none", False, False, (), "material_search.md", "pending_tools", "需要角色裁剪后的 C 材料检索工具"),
    TaskSpec("concept_tutoring", "概念讲解", frozenset({"student"}), "none", False, False, (), "concept_tutoring.md", "pending_tools", "需要已核对知识内容和答案泄漏校验"),
    TaskSpec("relation_review", "知识关系候选审阅", frozenset({"teacher"}), "none", False, False, ("list_knowledge_candidates",), "relation_review.md", "ready"),
    TaskSpec("observation_review", "学习观察回顾", frozenset({"teacher", "student"}), "student", False, False, (), "observation_review.md", "pending_tools", "需要活动绑定和观察汇总工具"),
    TaskSpec("out_of_scope", "超范围请求", frozenset({"teacher", "student"}), "none", False, False, (), "out_of_scope.md", "ready"),
)

TASKS: dict[str, TaskSpec] = {task.task_type: task for task in _TASKS}


def get_task(task_type: str) -> TaskSpec:
    try:
        return TASKS[task_type]
    except KeyError as exc:
        from study_companion.contracts import DomainError

        raise DomainError("VALIDATION_ERROR", f"未知任务类型：{task_type}") from exc


def tasks_for_role(role: str) -> tuple[TaskSpec, ...]:
    return tuple(task for task in _TASKS if role in task.roles)
