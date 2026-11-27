"""Controlled perception -> action -> observation -> re-plan loop.

The model may choose among a server-provided allow-list of read-only tools,
but cannot change scope, calculate official metrics, or publish recommendations.

The loop can optionally report *real* progress to a caller: model-round
boundaries reported by this loop, and tool starts/finishes reported around the
actual registry call. It never invents reasoning text; a delta is only forwarded
when the provider itself streams answer text.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields, is_dataclass
from typing import Any, Callable

from pydantic import BaseModel

from study_companion.agent.provider import Message, ModelProvider, ToolSchema
from study_companion.agent.task import AgentResult, AgentTaskState, ToolTrace
from study_companion.agent.types import ToolCall
from study_companion.contracts import DomainError

ProgressEvent = Callable[[dict[str, Any]], None]
TextDelta = Callable[[str], None]


@dataclass(frozen=True)
class RegisteredTool:
    schema: ToolSchema
    invoke: Callable[[dict[str, Any]], Any]


class ToolRegistry:
    def __init__(self, tools: list[RegisteredTool] | None = None) -> None:
        self._tools = {item.schema["function"]["name"]: item for item in (tools or [])}

    def schemas(self) -> list[ToolSchema]:
        return [item.schema for item in self._tools.values()]

    def label(self, name: str) -> str:
        """Human-readable tool label from the registered schema description."""
        tool = self._tools.get(name)
        if tool is None:
            return name
        description = tool.schema.get("function", {}).get("description", "")
        return description or name

    def call(self, call: ToolCall) -> Any:
        tool = self._tools.get(call.name)
        if tool is None:
            raise DomainError("TOOL_NOT_ALLOWED", f"工具未注册：{call.name}")
        parameters = tool.schema.get("function", {}).get("parameters", {})
        required = parameters.get("required", [])
        properties = parameters.get("properties", {})
        missing = [key for key in required if key not in call.arguments]
        unknown = [key for key in call.arguments if key not in properties]
        if missing or unknown:
            raise DomainError("INVALID_ARGUMENT", f"工具参数无效，缺少={missing}，未知={unknown}")
        for key, value in call.arguments.items():
            expected = properties.get(key, {}).get("type")
            if expected == "string" and not isinstance(value, str):
                raise DomainError("INVALID_ARGUMENT", f"参数 {key} 必须是字符串")
            if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
                raise DomainError("INVALID_ARGUMENT", f"参数 {key} 必须是整数")
        try:
            return tool.invoke({**call.arguments, "_call_id": call.call_id})
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError("TOOL_FAILED", f"工具执行失败：{call.name}") from exc


@dataclass(frozen=True)
class AgentLoopConfig:
    max_tool_calls: int = 8
    max_rounds: int = 8


def _json_value(value: Any) -> Any:
    """Convert tool results to JSON primitives without losing structure."""
    if isinstance(value, BaseModel):
        return _json_value(value.model_dump(mode="json", serialize_as_any=True))
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _json_value(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise DomainError("TOOL_FAILED", f"工具返回了不可序列化的数据：{type(value).__name__}")


def _assistant_message(turn: Any) -> Message:
    return {
        "role": "assistant",
        "content": turn.text,
        "tool_calls": [
            {
                "id": call.call_id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False, separators=(",", ":")),
                },
            }
            for call in turn.tool_calls
        ],
    }


def _tool_message(call_id: str, payload: dict[str, Any]) -> Message:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    }


class AgentLoop:
    def __init__(self, provider: ModelProvider, registry: ToolRegistry,
                 config: AgentLoopConfig | None = None) -> None:
        self.provider = provider
        self.registry = registry
        self.config = config or AgentLoopConfig()

    def run(
        self,
        messages: list[Message],
        goal: str,
        *,
        on_event: ProgressEvent | None = None,
        on_delta: TextDelta | None = None,
    ) -> AgentResult:
        state = AgentTaskState(goal=goal)
        trace: list[ToolTrace] = []
        total_calls = 0
        current = list(messages)
        streamer = getattr(self.provider, "generate_stream", None)

        def emit(event: dict[str, Any]) -> None:
            if on_event is None:
                return
            try:
                on_event(event)
            except Exception:  # progress reporting must never break the agent
                return

        for round_index in range(1, self.config.max_rounds + 1):
            emit({"type": "model_round", "round": round_index, "status": "running"})
            streamed: list[str] = []

            def forward_delta(chunk: str, _sink: list[str] = streamed) -> None:
                if not chunk:
                    return
                _sink.append(chunk)
                if on_delta is not None:
                    on_delta(chunk)

            if on_delta is not None and callable(streamer):
                turn = streamer(
                    current,
                    tools=self.registry.schemas(),
                    on_text=forward_delta,
                )
            else:
                turn = self.provider.generate(current, tools=self.registry.schemas())

            emit({
                "type": "model_round",
                "round": round_index,
                "status": "succeeded",
                "has_text": bool(turn.text),
                "tool_call_count": len(turn.tool_calls),
            })

            if not turn.tool_calls:
                return AgentResult(turn.text, state, trace, "model_finished")

            if streamed:
                # The round turned out to be a tool-selection turn. Text emitted
                # before the tool call is not the final answer; ask the caller to
                # drop that provisional preview instead of showing it twice.
                emit({"type": "discard_delta", "round": round_index})

            if total_calls + len(turn.tool_calls) > self.config.max_tool_calls:
                return AgentResult(turn.text, state, trace, "tool_call_limit")

            current.append(_assistant_message(turn))
            for call in turn.tool_calls:
                total_calls += 1
                emit({
                    "type": "tool",
                    "call_id": call.call_id,
                    "tool_name": call.name,
                    "label": self.registry.label(call.name),
                    "status": "running",
                })
                try:
                    result = _json_value(self.registry.call(call))
                    if isinstance(result, dict):
                        evidence_refs = list(result.get("evidence_refs", []) or [])
                        status = result.get("status", "succeeded")
                        error = result.get("error")
                        data = result.get("data", result)
                    else:
                        evidence_refs = []
                        status = "succeeded"
                        error = None
                        data = result
                    tool_payload = {
                        "status": status,
                        "data": data,
                        "error": error,
                        "evidence_refs": evidence_refs,
                    }
                    trace.append(ToolTrace(call.call_id, call.name, call.arguments,
                                           status, data, error, evidence_refs))
                    current.append(_tool_message(call.call_id, tool_payload))
                    emit({
                        "type": "tool",
                        "call_id": call.call_id,
                        "tool_name": call.name,
                        "label": self.registry.label(call.name),
                        "status": "succeeded" if status != "failed" else "failed",
                        "message": (error or {}).get("message", "") if isinstance(error, dict) else "",
                        "evidence_count": len(evidence_refs),
                    })
                    if status == "failed" and isinstance(error, dict) and error.get("code") in {
                        "SCOPE_DENIED", "TOOL_NOT_ALLOWED", "FORBIDDEN", "CONTEXT_MISMATCH"
                    }:
                        return AgentResult(None, state, trace, "tool_policy_failure")
                    state.observe(call.name, data, evidence_refs)
                except DomainError as exc:
                    error = {"code": exc.code, "message": str(exc)}
                    trace.append(ToolTrace(call.call_id, call.name, call.arguments,
                                           "failed", error=error))
                    tool_payload = {"status": "failed", "data": None,
                                    "error": error, "evidence_refs": []}
                    emit({
                        "type": "tool",
                        "call_id": call.call_id,
                        "tool_name": call.name,
                        "label": self.registry.label(call.name),
                        "status": "failed",
                        "message": str(exc),
                        "error_code": exc.code,
                        "evidence_count": 0,
                    })
                    # Scope and policy failures are terminal; ordinary missing
                    # evidence may be observed and trigger another model turn.
                    current.append(_tool_message(call.call_id, tool_payload))
                    if exc.code in {"SCOPE_DENIED", "TOOL_NOT_ALLOWED", "FORBIDDEN", "CONTEXT_MISMATCH"}:
                        return AgentResult(None, state, trace, "tool_policy_failure")
                    continue

        emit({"type": "limit", "reason": "round_limit"})
        return AgentResult(None, state, trace, "round_limit")
