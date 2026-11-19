"""Provider-agnostic message types for the controlled agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True)
class ModelTurn:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage | None = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)
