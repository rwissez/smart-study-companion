from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
from typing import Any

import pytest
from pydantic import BaseModel

from study_companion.agent import AgentLoop, AgentLoopConfig, ModelTurn, PromptRegistry, RegisteredTool, ToolRegistry, ToolCall
from study_companion.contracts import DomainError


SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_fact",
        "description": "read one fact",
        "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
    },
}


@dataclass
class ScriptedProvider:
    turns: list[ModelTurn]
    seen_tools: list[list[dict[str, Any]]]
    seen_messages: list[list[dict[str, Any]]] = field(default_factory=list)

    def generate(self, messages, tools=None, response_schema=None):
        self.seen_tools.append(tools or [])
        self.seen_messages.append(deepcopy(messages))
        return self.turns.pop(0)


def test_loop_observes_tool_and_replans():
    provider = ScriptedProvider([
        ModelTurn(tool_calls=[ToolCall("c1", "read_fact", {"key": "evidence"})]),
        ModelTurn(text="根据证据形成候选回答"),
    ], [])
    registry = ToolRegistry([RegisteredTool(SCHEMA, lambda args: {"status": "succeeded", "data": {"value": args["key"]}, "evidence_refs": [{"kind": "metric", "id": "m1"}]})])
    result = AgentLoop(provider, registry).run([{"role": "user", "content": "分析"}], "分析并回答")
    assert result.text == "根据证据形成候选回答"
    assert result.stopped_reason == "model_finished"
    assert result.state.completed_steps == ["read_fact"]
    assert result.state.evidence_refs == [{"kind": "metric", "id": "m1"}]
    assert result.tool_trace[0].status == "succeeded"
    assert provider.seen_tools[0][0]["function"]["name"] == "read_fact"


class NestedResult(BaseModel):
    value: str


class ToolEnvelope(BaseModel):
    status: str = "succeeded"
    data: NestedResult
    evidence_refs: list[dict[str, str]]


def test_loop_sends_openai_compatible_tool_history_with_json_results():
    provider = ScriptedProvider([
        ModelTurn(tool_calls=[ToolCall("c1", "read_fact", {"key": "证据"})]),
        ModelTurn(text="完成"),
    ], [])
    registry = ToolRegistry([RegisteredTool(
        SCHEMA,
        lambda _: ToolEnvelope(data=NestedResult(value="已核对"), evidence_refs=[{"kind": "metric", "id": "m1"}]),
    )])

    result = AgentLoop(provider, registry).run([{"role": "user", "content": "分析"}], "分析")

    assistant = provider.seen_messages[1][-2]
    assert assistant["content"] is None
    assert assistant["tool_calls"] == [{
        "id": "c1",
        "type": "function",
        "function": {"name": "read_fact", "arguments": json.dumps({"key": "证据"}, ensure_ascii=False, separators=(",", ":"))},
    }]
    tool_message = provider.seen_messages[1][-1]
    assert set(tool_message) == {"role", "tool_call_id", "content"}
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "c1"
    assert json.loads(tool_message["content"])["data"] == {"value": "已核对"}
    assert result.tool_trace[0].result == {"value": "已核对"}
    assert result.state.evidence_refs == [{"kind": "metric", "id": "m1"}]


def test_loop_rejects_unknown_tool_without_expanding_scope():
    provider = ScriptedProvider([ModelTurn(tool_calls=[ToolCall("c1", "write_fact", {})])], [])
    result = AgentLoop(provider, ToolRegistry([RegisteredTool(SCHEMA, lambda _: {})])).run([], "读取")
    assert result.stopped_reason == "tool_policy_failure"
    assert result.tool_trace[0].status == "failed"
    assert result.tool_trace[0].error["code"] == "TOOL_NOT_ALLOWED"


def test_loop_enforces_tool_call_limit():
    provider = ScriptedProvider([ModelTurn(tool_calls=[ToolCall("a", "read_fact", {}), ToolCall("b", "read_fact", {})])], [])
    registry = ToolRegistry([RegisteredTool(SCHEMA, lambda _: {})])
    result = AgentLoop(provider, registry, AgentLoopConfig(max_tool_calls=1)).run([], "读取")
    assert result.stopped_reason == "tool_call_limit"
    assert result.tool_trace == []


def test_prompt_registry_loads_versioned_existing_prompt():
    prompt, version = PromptRegistry().compose(role="student", task="解释证据", context="metric=m1")
    assert "七年级学生" in prompt
    assert version == "student-style-v1"


def test_tool_registry_rejects_invalid_arguments_before_execution():
    registry = ToolRegistry([RegisteredTool(SCHEMA, lambda _: (_ for _ in ()).throw(RuntimeError("boom")))])
    with pytest.raises(DomainError) as exc:
        registry.call(ToolCall("c", "read_fact", {}))
    assert exc.value.code == "INVALID_ARGUMENT"
