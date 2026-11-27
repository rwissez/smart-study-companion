"""Controlled perception -> action -> observation -> re-plan loop.

The model may choose among a server-provided allow-list of read-only tools,
but cannot change scope, calculate official metrics, or publish recommendations.

The loop can optionally report *real* progress to a caller: model-round
boundaries reported by this loop, and tool starts/finishes reported around the
actual registry call. It never invents reasoning text; a delta is only forwarded
when the provider itself streams answer text, and the final text is screened by
the answer guard before it is returned (a corrected answer asks the caller to
discard the provisional preview and is re-sent).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields, is_dataclass
from typing import Any, Callable, Sequence

from pydantic import BaseModel

from study_companion.agent.answer_guard import NUMBER_PATTERN, bind_answer, evidence_ids_from_results
from study_companion.agent.provider import Message, ModelProvider, ToolSchema
from study_companion.agent.task import AgentResult, AgentTaskState, ToolTrace
from study_companion.agent.tutoring_guard import SAFE_PRINCIPLE_REPLY, guard_tutoring_reply, is_tutoring_request
from study_companion.agent.types import ToolCall
from study_companion.contracts import DomainError

ProgressEvent = Callable[[dict[str, Any]], None]
TextDelta = Callable[[str], None]

#: 证据核对本身失败时的安全文本；不含数字或结论。
GUARD_FALLBACK_TEXT = "这轮回答的证据核对没有完成，我先不输出具体数字或结论。你可以换一种问法，或稍后再试。"

#: 从系统提示词识别角色；chat 路由不额外传参，保持现有调用兼容。
_STUDENT_MARKERS = ("学生端总则", "七年级学生能懂的话", "面向七年级学生")
_TEACHER_MARKERS = ("教师端呈现指引", "教师端总则")


def _latest_user_text(messages: list[Message]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            return message["content"].strip()
    return ""


def _detect_role(messages: list[Message]) -> str:
    system = " ".join(
        str(message.get("content") or "")
        for message in messages
        if message.get("role") == "system"
    )
    if any(marker in system for marker in _STUDENT_MARKERS):
        return "student"
    if any(marker in system for marker in _TEACHER_MARKERS):
        return "teacher"
    return "unknown"


def _chunks(text: str, size: int = 48) -> list[str]:
    return [text[start : start + size] for start in range(0, len(text), size)]


def _has_number(text: str) -> bool:
    return bool(NUMBER_PATTERN.search(text or ""))


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
    def __init__(
        self,
        provider: ModelProvider,
        registry: ToolRegistry,
        config: AgentLoopConfig | None = None,
        *,
        role: str | None = None,
        guard_final_answer: bool = True,
        protected_answers: Sequence[str] = (),
        protected_explanations: Sequence[str] = (),
        can_show_solution: bool | None = None,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.config = config or AgentLoopConfig()
        self.role = role
        # 05 §10：最终文本返回前做事实绑定；05 §8：学生求助先过答案保护。
        self.guard_final_answer = guard_final_answer
        self.protected_answers = tuple(protected_answers or ())
        self.protected_explanations = tuple(protected_explanations or ())
        self.can_show_solution = can_show_solution

    def _guard_text(
        self,
        text: str,
        messages: list[Message],
        state: AgentTaskState,
        trace: list[ToolTrace],
    ) -> tuple[str, dict[str, Any]]:
        """返回允许发送的文本与可审计的检查结果；失败只降级，不抛异常。"""

        info: dict[str, Any] = {}
        role = self.role or _detect_role(messages)
        request_text = _latest_user_text(messages)
        help_request = role == "student" and is_tutoring_request(request_text)
        current = text

        if help_request:
            try:
                decision = guard_tutoring_reply(
                    text=current,
                    request_text=request_text,
                    protected_answers=self.protected_answers,
                    protected_explanations=self.protected_explanations,
                    can_show_solution=self.can_show_solution,
                )
                info["tutoring"] = decision.as_dict()
                current = decision.text
            except Exception:  # 保护检查不可用时只给安全原理，不放行未验证内容
                info["tutoring"] = {"action": "degraded", "reason": "答案保护检查异常"}
                current = SAFE_PRINCIPLE_REPLY

        if not self.guard_final_answer:
            return current, info

        try:
            tool_results = [item.result for item in trace if item.result is not None]
            allowed_evidence = {
                str(ref.get("id"))
                for ref in state.evidence_refs
                if isinstance(ref, dict) and ref.get("id")
            } | set(evidence_ids_from_results(tool_results))
            binding = bind_answer(
                text=current,
                tool_results=tool_results,
                evidence_refs=state.evidence_refs,
                allowed_evidence_ids=allowed_evidence,
            )
            info["fact_binding"] = binding.as_dict()
            return binding.text, info
        except Exception:  # 核对失败时不能把未核对文本当确定事实输出
            info["fact_binding"] = {"status": "degraded"}
            if help_request:
                return SAFE_PRINCIPLE_REPLY, info
            if _has_number(current):
                return GUARD_FALLBACK_TEXT, info
            return current, info

    def _finish(
        self,
        text: str | None,
        state: AgentTaskState,
        trace: list[ToolTrace],
        messages: list[Message],
        *,
        stopped_reason: str,
        streamed: list[str],
        round_index: int,
        on_delta: TextDelta | None,
        emit: ProgressEvent,
    ) -> AgentResult:
        raw = text if text else "".join(streamed)
        if not raw:
            return AgentResult(None, state, trace, stopped_reason)
        guarded, info = self._guard_text(raw, messages, state, trace)
        if streamed and guarded != raw:
            # 原生流已经先给过预览；要求前端丢弃后用核对过的文本重发，
            # 不能把未核对的数字留在最终回答里。
            emit({"type": "discard_delta", "round": round_index})
            if on_delta is not None:
                for chunk in _chunks(guarded):
                    on_delta(chunk)
        return AgentResult(guarded, state, trace, stopped_reason, info)

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
                return self._finish(
                    turn.text,
                    state,
                    trace,
                    current,
                    stopped_reason="model_finished",
                    streamed=streamed,
                    round_index=round_index,
                    on_delta=on_delta,
                    emit=emit,
                )

            if streamed:
                # The round turned out to be a tool-selection turn. Text emitted
                # before the tool call is not the final answer; ask the caller to
                # drop that provisional preview instead of showing it twice.
                emit({"type": "discard_delta", "round": round_index})

            if total_calls + len(turn.tool_calls) > self.config.max_tool_calls:
                return self._finish(
                    turn.text,
                    state,
                    trace,
                    current,
                    stopped_reason="tool_call_limit",
                    streamed=streamed,
                    round_index=round_index,
                    on_delta=on_delta,
                    emit=emit,
                )

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
