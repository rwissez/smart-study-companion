"""Student chat: wired end to end, honest about the missing model.

The route exists now so the frontend can build against a real URL, and it
fails with ``MODEL_NOT_CONFIGURED`` (503) rather than returning invented text.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from sqlalchemy import select

from study_companion.agent import AgentLoop, PromptRegistry, build_provider
from study_companion.agent.tools import build_b_registry
from study_companion.api.deps import AppSettings, DbSession, Scope
from study_companion.api.envelope import success
from study_companion.contracts import AnalysisBundle, AnalysisConfig, DomainError, PreparedDataset
from study_companion.db import models
from study_companion.tools.b_analysis import ServerContext

router = APIRouter(tags=["chat"])


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(pattern="^(system|user|assistant)$")
    content: str = Field(min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(min_length=1, max_length=40)
    run_id: str | None = Field(default=None, max_length=64)
    student_id: str | None = Field(default=None, max_length=64)
    node_id: str | None = Field(default=None, max_length=64)


def _server_context(db: DbSession, scope, run_id: str, student_id: str | None) -> ServerContext:
    if scope.dataset_id is None:
        raise DomainError("CONTEXT_MISMATCH", "会话尚未绑定数据集")
    run = db.get(models.AnalysisRun, run_id)
    if run is None or run.dataset_id != scope.dataset_id or run.status != "succeeded":
        raise DomainError("CONTEXT_MISMATCH", "运行不属于当前会话或尚未完成")
    student_rows = db.scalars(select(models.Student).where(models.Student.dataset_id == scope.dataset_id)).all()
    allowed = frozenset({scope.student_id} if scope.role == "student" else {row.student_id for row in student_rows})
    if student_id is not None and student_id not in allowed:
        raise DomainError("SCOPE_DENIED", "学生不在当前会话范围")
    results = db.scalars(select(models.AnalysisResult).where(models.AnalysisResult.run_id == run_id)).all()
    bundle = AnalysisBundle(
        run_id=run_id, dataset_id=run.dataset_id, catalog_version=run.catalog_version,
        rule_version=run.rule_version, config=AnalysisConfig(**run.config_json),
        student_results=[row.payload_json for row in results if row.subject_type == "student"],
        class_results=[row.payload_json for row in results if row.subject_type == "class"],
        quality_summary={},
    )
    events = db.scalars(select(models.AnswerEvent).where(models.AnswerEvent.dataset_id == scope.dataset_id)).all()
    raw_rows = db.scalars(select(models.SourceRow).where(models.SourceRow.dataset_id == scope.dataset_id)).all()
    students = [row.payload_json if hasattr(row, "payload_json") else {"student_id": row.student_id, "class_id": row.class_id, "grade": row.grade} for row in student_rows]
    nodes = [row.payload_json for row in db.scalars(select(models.Node).where(models.Node.dataset_id == scope.dataset_id)).all()]
    prepared = PreparedDataset(
        students=students, nodes=nodes,
        raw_records=[{"record_id": row.payload_json.get("记录编号", row.id), "table_name": row.table_name, "source_row_number": row.row_number, "payload": row.payload_json} for row in raw_rows if row.table_name == "records"],
        selected_events=[row.payload_json for row in events], quality_issues=[],
        source_manifest={"dataset_id": scope.dataset_id, "table_row_counts": {}, "synthetic": False},
    )
    return ServerContext(run_id, scope.dataset_id, run.catalog_version, allowed, bundle, prepared)


@router.post("/chat")
def post_chat(payload: ChatRequest, db: DbSession, scope: Scope, settings: AppSettings) -> dict[str, Any]:
    scope.require("chat_own" if scope.role == "student" else "chat_teacher")
    provider = build_provider(settings)
    # Preserve the honest A0 behavior when no real model is configured.
    if provider.name == "unconfigured":
        turn = provider.generate([message.model_dump() for message in payload.messages])
        return success({"text": turn.text or "", "tool_calls": []})
    if not payload.run_id or not payload.student_id or not payload.node_id:
        raise DomainError("VALIDATION_ERROR", "真实智能体对话必须指定 run_id、student_id、node_id")
    context = _server_context(db, scope, payload.run_id, payload.student_id)
    registry = build_b_registry(context, scope.role)
    system, prompt_version = PromptRegistry().compose(role=scope.role, task="解释学生为什么失分", context=f"run_id={payload.run_id}; student_id={payload.student_id}; node_id={payload.node_id}")
    messages = [{"role": "system", "content": system}] + [message.model_dump() for message in payload.messages]
    result = AgentLoop(provider, registry).run(messages, "解释学生为什么失分")
    return success({"text": result.text or "", "tool_trace": [trace.__dict__ for trace in result.tool_trace], "stopped_reason": result.stopped_reason, "prompt_version": prompt_version, "evidence_refs": result.state.evidence_refs})
