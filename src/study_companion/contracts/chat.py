"""Agent 对话、路由与确认契约（01 §9.5、05 §5/§7/§11）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from study_companion.contracts.base import Contract, JobStatus
from study_companion.contracts.common import EvidenceRef

TaskType = Literal[
    "student_analysis",
    "class_analysis",
    "knowledge_lookup",
    "plan_query",
    "plan_modify",
    "concept_tutoring",
    "data_quality",
    "learning_progress",
]

ChatStatus = Literal[
    "completed", "partial", "needs_confirmation", "failed", "out_of_scope"
]


class ChatMessage(Contract):
    role: Literal["user", "assistant"]
    content: str


class Target(Contract):
    type: Literal["student", "class"]
    id: str


class AnalysisContext(Contract):
    """D 旧入口兼容：只在服务端转为 target/run/node。"""

    run_id: str
    node_id: str
    subject_type: Literal["student", "class"]
    subject_id: str


class ChatRequest(Contract):
    messages: list[ChatMessage] | None = None
    message: str | None = None
    conversation_id: str | None = None
    run_id: str | None = None
    student_id: str | None = None
    node_id: str | None = None
    target: Target | None = None
    task_type: str | None = None
    activity_id: str | None = None
    plan_version: str | None = None
    analysis_context: AnalysisContext | None = None

    @model_validator(mode="after")
    def message_shape(self):
        if (self.messages is None) == (self.message is None):
            raise ValueError("messages and message are mutually exclusive")
        if self.messages is not None:
            if len(self.messages) > 40:
                raise ValueError("at most 40 history messages")
            if any(not item.content.strip() for item in self.messages):
                raise ValueError("message content must not be empty")
        if self.message is not None and not self.message.strip():
            raise ValueError("message must not be empty")
        return self


class ToolTraceView(Contract):
    call_id: str
    stage: str = ""
    tool_name: str
    status: Literal["running", "succeeded", "failed", "skipped", "pending"] = "succeeded"
    message: str = ""
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class OperationResult(Contract):
    operation_id: str
    target_student_id: str
    status: Literal["pending", "running", "succeeded", "failed", "needs_confirmation"]
    previous_plan_version: str | None = None
    new_plan_version: str | None = None
    review_status: str | None = None
    publication_status: str | None = None
    error: dict[str, Any] | None = None


class AnswerSegmentView(Contract):
    kind: Literal["fact", "explanation", "suggestion", "operation", "limitation"]
    text: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class ConfirmationTarget(Contract):
    student_id: str
    base_plan_version: str | None = None
    proposal_id: str | None = None
    draft_hash: str | None = None
    changes_summary: str = ""


class ConfirmationView(Contract):
    confirmation_id: str
    summary: str
    targets: list[ConfirmationTarget] = Field(default_factory=list)
    expires_at: str


class AssistedConfirmationView(Contract):
    confirmation_id: str
    activity_id: str
    attempt_id: str
    plan_version: str
    summary: str
    expires_at: str


class ChatResponse(Contract):
    conversation_id: str
    message_id: str
    text: str
    task_type: str | None = None
    route_source: str | None = None
    status: ChatStatus = "completed"
    tool_trace: list[ToolTraceView] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    stopped_reason: str | None = None
    prompt_version: str | None = None
    confirmation: ConfirmationView | None = None
    operation_results: list[OperationResult] = Field(default_factory=list)
    job_id: str | None = None
    segments: list[AnswerSegmentView] = Field(default_factory=list)
    clarification: str | None = None
    assisted_confirmation: AssistedConfirmationView | None = None


class ChatAccepted(Contract):
    job_id: str
    conversation_id: str
    message_id: str
    status: JobStatus = "queued"


class ConversationSummary(Contract):
    conversation_id: str
    title: str
    role: str
    dataset_id: str | None = None
    created_at: str = ""
    updated_at: str = ""
    last_message: str | None = None


class ChatMessageView(Contract):
    message_id: str
    conversation_id: str
    sequence: int = Field(ge=1)
    role: Literal["user", "assistant"]
    content: str
    status: str = "completed"
    created_at: str = ""
    segments: list[AnswerSegmentView] = Field(default_factory=list)
    job_id: str | None = None
    tool_trace: list[ToolTraceView] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    confirmation: ConfirmationView | None = None
    assisted_confirmation: AssistedConfirmationView | None = None
    clarification: str | None = None
    operation_results: list[OperationResult] = Field(default_factory=list)


class ConfirmRequest(Contract):
    confirmation_id: str
    decision: Literal["accept", "reject"]


class ModelSettingsView(Contract):
    configured: bool
    provider: str | None = None
    url: str | None = None
    model_id: str | None = None
    has_api_key: bool = False


class ModelSettingsUpdate(Contract):
    provider: str | None = None
    url: str | None = None
    model_id: str | None = None
    api_key: str | None = None
    clear_api_key: bool = False
    allow_external_model_input: bool | None = None

    @model_validator(mode="after")
    def no_conflict(self):
        if self.api_key and self.clear_api_key:
            raise ValueError("api_key and clear_api_key are mutually exclusive")
        return self


class StepDecision(Contract):
    kind: Literal["call", "finish", "clarify"]
    step_key: str | None = None
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None
    missing_fact: str | None = None


class RouteRequest(Contract):
    step_key: str
    task_key: str
    operation: Literal["read", "propose", "write"]
    targets: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    intent_summary: str


class RouteDecision(Contract):
    status: Literal["ready", "unclear", "out_of_scope"]
    requests: list[RouteRequest] = Field(default_factory=list, max_length=6)
    clarification: str | None = None
    explicit_write: bool = False
