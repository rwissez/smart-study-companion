"""The single seam between our code and any model vendor.

Synchronous by design: the database layer is synchronous and the loop runs in
an ordinary request or worker thread, so an ``httpx.Client`` avoids nesting an
event loop inside a thread. Everything above this Protocol is testable with a
scripted fake; everything below is swapped per vendor.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from study_companion.agent.types import ModelTurn

Message = dict[str, Any]
ToolSchema = dict[str, Any]


@runtime_checkable
class ModelProvider(Protocol):
    name: str

    def generate(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> ModelTurn: ...
