"""OpenAI-compatible synchronous model provider with tool-call parsing."""

from __future__ import annotations

from typing import Any

import httpx

from study_companion.agent.provider import Message, ToolSchema
from study_companion.agent.types import ModelTurn, ToolCall, Usage
from study_companion.contracts import DomainError


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 45) -> None:
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, trust_env=False)
        self._model = model
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def generate(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> ModelTurn:
        body: dict[str, Any] = {"model": self._model, "messages": messages, "temperature": 0.2}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if response_schema:
            body["response_format"] = {"type": "json_object"}
        try:
            response = self._client.post("/chat/completions", headers=self._headers, json=body)
        except httpx.TimeoutException as exc:
            raise DomainError("MODEL_TIMEOUT", "模型请求超时") from exc
        except httpx.HTTPError as exc:
            raise DomainError("MODEL_UNAVAILABLE", "模型服务不可用") from exc
        if response.status_code in (401, 403):
            raise DomainError("MODEL_NOT_CONFIGURED", "模型凭据不可用")
        if response.status_code == 429 or response.status_code >= 500:
            raise DomainError("MODEL_UNAVAILABLE", "模型服务暂时不可用")
        if response.status_code >= 400:
            raise DomainError("MODEL_BAD_RESPONSE", "模型请求被拒绝")
        try:
            message = response.json()["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise DomainError("MODEL_BAD_RESPONSE", "模型返回格式无效") from exc
        calls: list[ToolCall] = []
        for item in message.get("tool_calls") or []:
            function = item.get("function") or {}
            raw_args = function.get("arguments", {})
            if isinstance(raw_args, str):
                try:
                    import json
                    raw_args = json.loads(raw_args)
                except json.JSONDecodeError as exc:
                    raise DomainError("MODEL_BAD_RESPONSE", "工具参数不是有效JSON") from exc
            if not isinstance(raw_args, dict):
                raise DomainError("MODEL_BAD_RESPONSE", "工具参数必须是对象")
            calls.append(ToolCall(str(item.get("id", "tool-call")), function.get("name", ""), raw_args))
        usage = response.json().get("usage") or {}
        return ModelTurn(
            text=message.get("content"),
            tool_calls=calls,
            usage=Usage(usage.get("prompt_tokens"), usage.get("completion_tokens")),
        )
