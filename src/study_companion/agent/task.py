"""State and trace types for the controlled internal agent task loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentTaskState:
    goal: str
    known_facts: list[dict[str, Any]] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    evidence_refs: list[dict[str, str]] = field(default_factory=list)
    completed_steps: list[str] = field(default_factory=list)
    selected_strategy: str | None = None
    next_action: str | None = None

    def observe(self, tool_name: str, result: Any, evidence_refs: list[dict[str, str]] | None = None) -> None:
        self.completed_steps.append(tool_name)
        self.known_facts.append({"tool": tool_name, "result": result})
        if evidence_refs:
            self.evidence_refs.extend(evidence_refs)
        self.next_action = None


@dataclass(frozen=True)
class ToolTrace:
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    status: str
    result: Any = None
    error: dict[str, str] | None = None
    evidence_refs: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class AgentResult:
    text: str | None
    state: AgentTaskState
    tool_trace: list[ToolTrace]
    stopped_reason: str
