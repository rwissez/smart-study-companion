"""OpenAI-compatible synchronous model provider with tool-call parsing.

``generate`` is the non-streaming call used by every old caller. ``generate_stream``
is the same contract with ``stream=true``: answer text is forwarded chunk by
chunk while tool-call deltas are accumulated and returned as one ``ModelTurn``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from study_companion.agent.provider import Message, ToolSchema
from study_companion.agent.types import ModelTurn, ToolCall, Usage
from study_companion.contracts import DomainError


class OpenAICompatibleProvider:
    name = "openai_compatible"
    supports_streaming = True

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 45) -> None:
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, trust_env=False)
        self._model = model
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def _body(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None,
        response_schema: dict[str, Any] | None,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self._model, "messages": messages, "temperature": 0.2}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if response_schema:
            body["response_format"] = {"type": "json_object"}
        if stream:
            body["stream"] = True
        return body

    def _raise_for_status(self, status_code: int) -> None:
        if status_code in (401, 403):
            raise DomainError("MODEL_NOT_CONFIGURED", "模型凭据不可用")
        if status_code == 429 or status_code >= 500:
            raise DomainError("MODEL_UNAVAILABLE", "模型服务暂时不可用")
        if status_code >= 400:
            raise DomainError("MODEL_BAD_RESPONSE", "模型请求被拒绝")

    @staticmethod
    def _parse_arguments(raw_args: Any) -> dict[str, Any]:
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except json.JSONDecodeError as exc:
                raise DomainError("MODEL_BAD_RESPONSE", "工具参数不是有效JSON") from exc
        if not isinstance(raw_args, dict):
            raise DomainError("MODEL_BAD_RESPONSE", "工具参数必须是对象")
        return raw_args

    def generate(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> ModelTurn:
        body = self._body(messages, tools, response_schema, stream=False)
        try:
            response = self._client.post("/chat/completions", headers=self._headers, json=body)
        except httpx.TimeoutException as exc:
            raise DomainError("MODEL_TIMEOUT", "模型请求超时") from exc
        except httpx.HTTPError as exc:
            raise DomainError("MODEL_UNAVAILABLE", "模型服务不可用") from exc
        self._raise_for_status(response.status_code)
        try:
            message = response.json()["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise DomainError("MODEL_BAD_RESPONSE", "模型返回格式无效") from exc
        calls: list[ToolCall] = []
        for item in message.get("tool_calls") or []:
            function = item.get("function") or {}
            calls.append(
                ToolCall(
                    str(item.get("id", "tool-call")),
                    function.get("name", ""),
                    self._parse_arguments(function.get("arguments", {})),
                )
            )
        usage = response.json().get("usage") or {}
        return ModelTurn(
            text=message.get("content"),
            tool_calls=calls,
            usage=Usage(usage.get("prompt_tokens"), usage.get("completion_tokens")),
        )

    def generate_stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        response_schema: dict[str, Any] | None = None,
        on_text: Any = None,
    ) -> ModelTurn:
        """Native SSE streaming call.

        ``on_text`` receives provider-native answer deltas as they arrive. Tool
        call fragments are accumulated per index and returned after the stream
        ends, so the agent loop still sees a normal ``ModelTurn``.
        """

        body = self._body(messages, tools, response_schema, stream=True)
        text_parts: list[str] = []
        call_parts: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] = {}
        try:
            with self._client.stream(
                "POST", "/chat/completions", headers=self._headers, json=body
            ) as response:
                self._raise_for_status(response.status_code)
                for line in response.iter_lines():
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise DomainError("MODEL_BAD_RESPONSE", "模型流式返回不是有效JSON") from exc
                    if isinstance(chunk.get("usage"), dict):
                        usage = chunk["usage"]
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0] or {}
                    delta = choice.get("delta") or {}
                    content = delta.get("content")
                    if content:
                        text_parts.append(content)
                        if callable(on_text):
                            on_text(content)
                    for position, item in enumerate(delta.get("tool_calls") or []):
                        index = item.get("index", position)
                        slot = call_parts.setdefault(
                            int(index), {"id": "", "name": "", "arguments": ""}
                        )
                        if item.get("id"):
                            slot["id"] = str(item["id"])
                        function = item.get("function") or {}
                        if function.get("name"):
                            slot["name"] = str(function["name"])
                        if isinstance(function.get("arguments"), str):
                            slot["arguments"] += function["arguments"]
        except httpx.TimeoutException as exc:
            raise DomainError("MODEL_TIMEOUT", "模型请求超时") from exc
        except httpx.HTTPError as exc:
            raise DomainError("MODEL_UNAVAILABLE", "模型服务不可用") from exc

        calls = [
            ToolCall(
                slot["id"] or f"tool-call-{index}",
                slot["name"],
                self._parse_arguments(slot["arguments"]),
            )
            for index, slot in sorted(call_parts.items())
        ]
        return ModelTurn(
            text="".join(text_parts) or None,
            tool_calls=calls,
            usage=Usage(usage.get("prompt_tokens"), usage.get("completion_tokens")),
        )
