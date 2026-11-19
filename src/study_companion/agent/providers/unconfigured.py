"""The default provider: present, callable, and honest about being unconfigured.

It raises ``DomainError("MODEL_NOT_CONFIGURED")`` instead of returning canned
text, so no screen can ever present a fabricated answer as a model response.
"""

from __future__ import annotations

from typing import Any

from study_companion.agent.provider import Message, ToolSchema
from study_companion.agent.types import ModelTurn
from study_companion.contracts import DomainError


class UnconfiguredProvider:
    name = "unconfigured"

    def generate(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> ModelTurn:
        raise DomainError("MODEL_NOT_CONFIGURED", "模型服务尚未配置")
