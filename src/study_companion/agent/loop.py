"""Controlled perception -> action -> observation -> re-plan loop.

The model may choose among a server-provided allow-list of read-only tools,
but cannot change scope, calculate official metrics, or publish recommendations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from study_companion.agent.provider import Message, ModelProvider, ToolSchema
from study_companion.agent.task import AgentResult, AgentTaskState, ToolTrace
from study_companion.agent.types import ToolCall
from study_companion.contracts import DomainError


@dataclass(frozen=True)
class RegisteredTool:
    schema: ToolSchema
    invoke: Callable[[dict[str, Any]], Any]


class ToolRegistry:
    def __init__(self, tools: list[RegisteredTool] | None = None) -> None:
        self._tools = {item.schema["function"]["name"]: item for item in (tools or [])}

    def schemas(self) -> list[ToolSchema]:
        return [item.schema for item in self._tools.values()]

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


class AgentLoop:
    def __init__(self, provider: ModelProvider, registry: ToolRegistry,
                 config: AgentLoopConfig | None = None) -> None:
        self.provider = provider
        self.registry = registry
        self.config = config or AgentLoopConfig()

    def run(self, messages: list[Message], goal: str) -> AgentResult:
        state = AgentTaskState(goal=goal)
        trace: list[ToolTrace] = []
        total_calls = 0
        current = list(messages)

        for _round in range(self.config.max_rounds):
            turn = self.provider.generate(current, tools=self.registry.schemas())
            if not turn.tool_calls:
                return AgentResult(turn.text, state, trace, "model_finished")
            if total_calls + len(turn.tool_calls) > self.config.max_tool_calls:
                return AgentResult(turn.text, state, trace, "tool_call_limit")

            current.append({"role": "assistant", "content": turn.text or "", "tool_calls": [
                {"id": call.call_id, "name": call.name, "arguments": call.arguments}
                for call in turn.tool_calls
            ]})
            for call in turn.tool_calls:
                total_calls += 1
                try:
                    result = self.registry.call(call)
                    if isinstance(result, dict):
                        evidence_refs = list(result.get("evidence_refs", []) or [])
                        status = result.get("status", "succeeded")
                        error = result.get("error")
                        data = result.get("data", result)
                    elif getattr(result, "status", "succeeded") == "failed":
                        error = getattr(result, "error", None) or {}
                        status = "failed"
                        data = None
                        evidence_refs = list(getattr(result, "evidence_refs", []) or [])
                        if error.get("code") in {"SCOPE_DENIED", "TOOL_NOT_ALLOWED", "FORBIDDEN", "CONTEXT_MISMATCH"}:
                            trace.append(ToolTrace(call.call_id, call.name, call.arguments, status, data, error, evidence_refs))
                            return AgentResult(None, state, trace, "tool_policy_failure")
                    else:
                        evidence_refs = list(getattr(result, "evidence_refs", []) or [])
                        status = getattr(result, "status", "succeeded")
                        error = getattr(result, "error", None)
                        data = getattr(result, "data", result)
                    state.observe(call.name, data, evidence_refs)
                    trace.append(ToolTrace(call.call_id, call.name, call.arguments,
                                           status, data, error, evidence_refs))
                    tool_payload = {"status": status, "data": data, "error": error,
                                    "evidence_refs": evidence_refs}
                except DomainError as exc:
                    trace.append(ToolTrace(call.call_id, call.name, call.arguments,
                                           "failed", error={"code": exc.code, "message": str(exc)}))
                    tool_payload = {"status": "failed", "data": None,
                                    "error": {"code": exc.code, "message": str(exc)}}
                    # Scope and policy failures are terminal; ordinary missing
                    # evidence may be observed and trigger another model turn.
                    current.append({"role": "tool", "tool_call_id": call.call_id,
                                    "name": call.name, "content": tool_payload})
                    if exc.code in {"SCOPE_DENIED", "TOOL_NOT_ALLOWED", "FORBIDDEN", "CONTEXT_MISMATCH"}:
                        return AgentResult(None, state, trace, "tool_policy_failure")
                    continue
                current.append({"role": "tool", "tool_call_id": call.call_id,
                                "name": call.name, "content": tool_payload})

        return AgentResult(None, state, trace, "round_limit")
