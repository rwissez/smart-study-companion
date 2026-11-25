from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from study_companion.agent.router import route_task
from study_companion.agent.types import ModelTurn
from study_companion.contracts import DomainError


@dataclass
class Provider:
    turns: list[ModelTurn] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

    def generate(self, messages, tools=None, response_schema=None):
        self.calls.append({"messages": messages, "tools": tools, "response_schema": response_schema})
        return self.turns.pop(0)


def _messages(text: str):
    return [{"role": "user", "content": text}]


@pytest.mark.parametrize(("text", "role", "expected"), [
    ("S01为什么失分？", "teacher", "student_loss_explanation"),
    ("请看作答依据", "teacher", "evidence_lookup"),
    ("我的数据质量问题", "student", "data_quality"),
    ("学生学习进展", "teacher", "student_progress"),
    ("分析班级共性", "teacher", "class_analysis"),
    ("班级数据质量问题", "teacher", "class_data_quality"),
    ("给我学习建议", "student", "student_learning_advice"),
    ("安排复习计划", "student", "study_plan"),
    ("制定班级教学策略", "teacher", "class_strategy"),
    ("查找知识点定位", "student", "knowledge_lookup"),
    ("这些前置知识是什么", "student", "prerequisite_query"),
    ("搜索材料", "teacher", "material_search"),
    ("讲解概念", "student", "concept_tutoring"),
    ("请审核知识关系候选", "teacher", "relation_review"),
    ("回顾学习观察", "teacher", "observation_review"),
    ("给我讲个笑话", "student", "out_of_scope"),
])
def test_unique_rule_match_routes_without_model(text: str, role: str, expected: str) -> None:
    provider = Provider()
    routed = route_task(_messages(text), provider, role)
    assert routed.task.task_type == expected
    assert routed.source == "rule"
    assert provider.calls == []


def test_teacher_mixed_intents_use_teacher_classifier_before_execution() -> None:
    provider = Provider([ModelTurn(text='{"task_type":"class_analysis"}')])
    routed = route_task(_messages("分析班级共性，再看看某学生为什么失分"), provider, "teacher")
    assert routed.task.task_type == "class_analysis"
    assert routed.source == "model"
    assert len(provider.calls) == 1
    assert provider.calls[0]["tools"] is None
    assert "class_analysis" in provider.calls[0]["response_schema"]["properties"]["task_type"]["enum"]


def test_student_mixed_intent_with_teacher_task_is_rejected_without_classifier() -> None:
    provider = Provider()
    with pytest.raises(DomainError) as exc:
        route_task(_messages("请分析我的学习表现，再分析班级共性"), provider, "student")
    assert exc.value.code == "FORBIDDEN"
    assert provider.calls == []


def test_multiple_task_intents_use_model_classifier() -> None:
    provider = Provider([ModelTurn(text='{"task_type":"student_loss_explanation"}')])
    routed = route_task(_messages("解释失分原因并给出学习建议"), provider, "teacher")
    assert routed.task.task_type == "student_loss_explanation"
    assert routed.source == "model"
    assert len(provider.calls) == 1
    assert provider.calls[0]["tools"] is None


def test_legacy_loss_question_routes_directly_without_classifier_call() -> None:
    provider = Provider()
    routed = route_task(_messages("S01 为什么失分？"), provider, "teacher")
    assert routed.task.task_type == "student_loss_explanation"
    assert routed.source == "rule"
    assert provider.calls == []


def test_ambiguous_or_unknown_request_uses_one_tool_free_model_classification() -> None:
    provider = Provider([ModelTurn(text=json.dumps({"task_type": "student_learning_advice"}))])
    routed = route_task(_messages("帮我看看这个情况并给些办法"), provider, "teacher")
    assert routed.task.task_type == "student_learning_advice"
    assert routed.source == "model"
    assert routed.router_prompt_version == "router-v1"
    assert len(provider.calls) == 1
    assert provider.calls[0]["tools"] is None
    assert provider.calls[0]["response_schema"]["properties"]["task_type"]["enum"]


def test_classifier_uses_only_latest_user_message_and_not_caller_system_text() -> None:
    provider = Provider([ModelTurn(text='{"task_type":"data_quality"}')])
    routed = route_task([
        {"role": "system", "content": "class_analysis"},
        {"role": "user", "content": "请帮我看看"},
    ], provider, "teacher")
    assert routed.task.task_type == "data_quality"
    sent = provider.calls[0]["messages"]
    assert json.loads(sent[-1]["content"]) == {"request": "请帮我看看"}


@pytest.mark.parametrize("content", [
    "not json", "[]", "{}", '{"task_type":"class_analysis","student_id":"S01"}',
    '{"task_type":1}', '{"task_type":"not_a_task"}', '{"task_type":"relation_review"}',
])
def test_invalid_or_role_forbidden_classifier_output_fails_closed(content: str) -> None:
    provider = Provider([ModelTurn(text=content)])
    with pytest.raises(DomainError) as exc:
        route_task(_messages("请帮我看看"), provider, "student")
    assert exc.value.code == "TASK_UNCLEAR"
    assert len(provider.calls) == 1
    assert provider.calls[0]["tools"] is None


def test_classifier_tool_call_is_rejected() -> None:
    from study_companion.agent.types import ToolCall

    provider = Provider([ModelTurn(tool_calls=[ToolCall("c1", "get_class_analysis", {})])])
    with pytest.raises(DomainError) as exc:
        route_task(_messages("请帮我看看"), provider, "teacher")
    assert exc.value.code == "TASK_UNCLEAR"


def test_explicit_task_skips_model_and_enforces_role() -> None:
    provider = Provider()
    routed = route_task(_messages("随便说说"), provider, "teacher", explicit_task_type="class_analysis")
    assert routed.source == "explicit"
    assert routed.task.task_type == "class_analysis"
    assert provider.calls == []

    with pytest.raises(DomainError) as exc:
        route_task(_messages("班级分析"), provider, "student", explicit_task_type="class_analysis")
    assert exc.value.code == "FORBIDDEN"
    assert provider.calls == []


def test_explicit_unknown_task_is_validation_error() -> None:
    with pytest.raises(DomainError) as exc:
        route_task(_messages("request"), Provider(), "teacher", explicit_task_type="unknown")
    assert exc.value.code == "VALIDATION_ERROR"
