"""Model access layer, prompt registry and controlled task loop."""

from study_companion.agent.factory import build_provider
from study_companion.agent.loop import AgentLoop, AgentLoopConfig, RegisteredTool, ToolRegistry
from study_companion.agent.prompts import PromptRegistry
from study_companion.agent.provider import ModelProvider
from study_companion.agent.task import AgentResult, AgentTaskState, ToolTrace
from study_companion.agent.types import ModelTurn, ToolCall, Usage

__all__ = [
    "AgentLoop", "AgentLoopConfig", "AgentResult", "AgentTaskState",
    "ModelProvider", "ModelTurn", "PromptRegistry", "RegisteredTool",
    "ToolCall", "ToolRegistry", "ToolTrace", "Usage", "build_provider",
]
