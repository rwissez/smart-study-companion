"""Student chat: wired end to end, honest about the missing model.

The route exists now so the frontend can build against a real URL, and it
fails with ``MODEL_NOT_CONFIGURED`` (503) rather than returning invented text.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sqlalchemy import select

from study_companion.agent import AgentLoop, PromptRegistry, ToolRegistry, build_provider
from study_companion.agent.router import route_task
from study_companion.agent.tasks import TaskSpec, get_task
from study_companion.agent.tools import build_b_registry
from study_companion.agent.knowledge_tools import build_knowledge_registry
from study_companion.api.deps import AppSettings, DbSession, Scope
from study_companion.api.envelope import success
from study_companion.contracts import AnalysisBundle, AnalysisConfig, Catalog as CatalogContract, DomainError, PreparedDataset
from study_companion.db import models
from study_companion.tools.b_analysis import ServerContext

router = APIRouter(tags=["chat"])


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(pattern="^(system|user|assistant)$")
    content: str = Field(min_length=1, max_length=8000)


class ChatTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(pattern="^(student|class)$")
    id: str = Field(min_length=1, max_length=64)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(min_length=1, max_length=40)
    run_id: str | None = Field(default=None, max_length=64)
    student_id: str | None = Field(default=None, max_length=64)
    node_id: str | None = Field(default=None, max_length=64)
    target: ChatTarget | None = None
    task_type: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_target_alias(self):
        if self.student_id and self.target and (self.target.type != "student" or self.target.id != self.student_id):
            raise ValueError("student_id 与 target 必须一致")
        return self


def _server_context(
    db: DbSession,
    scope,
    run_id: str | None,
    target: ChatTarget | None,
    node_id: str | None,
    task: TaskSpec,
) -> ServerContext:
    if scope.dataset_id is None:
        raise DomainError("CONTEXT_MISMATCH", "会话尚未绑定数据集")

    student_rows = db.scalars(
        select(models.Student).where(models.Student.dataset_id == scope.dataset_id)
    ).all()
    students_by_id = {row.student_id: row for row in student_rows}
    classes = {row.class_id for row in student_rows}

    if target is None and scope.role == "student":
        target = ChatTarget(type="student", id=scope.student_id)
    if task.target == "student" and (target is None or target.type != "student"):
        raise DomainError("VALIDATION_ERROR", "该任务需要显式学生目标")
    if task.target == "class" and (target is None or target.type != "class"):
        raise DomainError("VALIDATION_ERROR", "该任务需要显式班级目标")
    if task.target == "student_or_dataset" and target is None and scope.role == "student":
        target = ChatTarget(type="student", id=scope.student_id)
    if task.target == "student_or_dataset" and target is not None and target.type != "student":
        raise DomainError("TASK_NOT_AVAILABLE", "班级质量过滤尚未实现；请使用学生或数据集范围")

    if target is not None:
        if target.type == "student":
            if target.id not in students_by_id:
                raise DomainError("NOT_FOUND", "目标学生不存在")
            if scope.role == "student" and target.id != scope.student_id:
                raise DomainError("FORBIDDEN", "学生会话只能访问本人数据")
        elif target.id not in classes:
            raise DomainError("NOT_FOUND", "目标班级不存在")
        if task.target == "student" and target.type != "student":
            raise DomainError("VALIDATION_ERROR", "该任务要求学生目标")
        if task.target == "class" and target.type != "class":
            raise DomainError("VALIDATION_ERROR", "该任务要求班级目标")

    if task.requires_run and not run_id:
        raise DomainError("VALIDATION_ERROR", "该任务必须提供 run_id")
    run = db.get(models.AnalysisRun, run_id) if run_id else None
    if run_id and (run is None or run.dataset_id != scope.dataset_id or run.status != "succeeded"):
        raise DomainError("CONTEXT_MISMATCH", "运行不属于当前会话或尚未完成")
    if task.requires_node and not node_id:
        raise DomainError("VALIDATION_ERROR", "该任务必须提供 node_id")
    nodes = [row.payload_json for row in db.scalars(
        select(models.Node).where(models.Node.dataset_id == scope.dataset_id)
    ).all()]
    if node_id and node_id not in {item.get("node_id") for item in nodes}:
        raise DomainError("NOT_FOUND", "目标节点不存在")

    if target and target.type == "student":
        allowed = frozenset({target.id})
        class_ids = frozenset({students_by_id[target.id].class_id})
    elif target and target.type == "class":
        allowed = frozenset(row.student_id for row in student_rows if row.class_id == target.id)
        class_ids = frozenset({target.id})
    elif scope.role == "student":
        allowed = frozenset({scope.student_id})
        class_ids = frozenset({students_by_id[scope.student_id].class_id})
    else:
        allowed = frozenset(students_by_id)
        class_ids = frozenset(classes)

    result_rows = []
    if run is not None:
        result_rows = db.scalars(
            select(models.AnalysisResult).where(models.AnalysisResult.run_id == run_id)
        ).all()
        if target and target.type == "student":
            result_rows = [
                row for row in result_rows
                if row.subject_type == "student" and row.subject_id == target.id
            ]
        elif target and target.type == "class":
            result_rows = [
                row for row in result_rows
                if row.subject_type == "class" and row.subject_id == target.id
            ]
    bundle = None
    if run is not None:
        bundle = AnalysisBundle(
            run_id=run_id, dataset_id=run.dataset_id, catalog_version=run.catalog_version,
            rule_version=run.rule_version, config=AnalysisConfig(**run.config_json),
            student_results=[row.payload_json for row in result_rows if row.subject_type == "student"],
            class_results=[row.payload_json for row in result_rows if row.subject_type == "class"],
            quality_summary={},
        )

    events = db.scalars(
        select(models.AnswerEvent).where(models.AnswerEvent.dataset_id == scope.dataset_id)
        .order_by(models.AnswerEvent.event_id)
    ).all()
    raw_rows = db.scalars(
        select(models.SourceRow).where(models.SourceRow.dataset_id == scope.dataset_id)
        .order_by(models.SourceRow.table_name, models.SourceRow.row_number)
    ).all()
    quality_issues = db.scalars(
        select(models.QualityIssue).where(models.QualityIssue.dataset_id == scope.dataset_id)
        .order_by(models.QualityIssue.id)
    ).all()
    prepared = PreparedDataset(
        students=[row.payload_json if hasattr(row, "payload_json") else {"student_id": row.student_id, "class_id": row.class_id, "grade": row.grade} for row in student_rows],
        nodes=nodes,
        raw_records=[{"record_id": row.payload_json.get("记录编号", row.id), "table_name": row.table_name, "source_row_number": row.row_number, "payload": row.payload_json} for row in raw_rows if row.table_name == "records"],
        selected_events=[row.payload_json for row in events],
        quality_issues=[row.payload_json for row in quality_issues],
        source_manifest={"dataset_id": scope.dataset_id, "table_row_counts": {}, "synthetic": False},
    )
    allowed_records: frozenset[str] | None = None
    if target and target.type == "student" and node_id and bundle:
        selected = next((r for r in bundle.student_results if r.student_id == target.id and r.node_id == node_id), None)
        allowed_records = frozenset(
            [
                ref.id for metric in selected.metrics for ref in metric.evidence_refs
                if ref.kind == "record"
            ] + [
                ref.id for finding in selected.findings for ref in finding.evidence_refs
                if ref.kind == "record"
            ] if selected else []
        )
    catalog_version = run.catalog_version if run else db.scalar(
        select(models.Catalog.version).where(models.Catalog.dataset_id == scope.dataset_id).order_by(models.Catalog.created_at.desc())
    )
    return ServerContext(
        run_id=run_id or "", dataset_id=scope.dataset_id,
        catalog_version=catalog_version or "", allowed_student_ids=allowed,
        bundle=bundle, dataset=prepared,
        bound_student_id=target.id if target and target.type == "student" else None,
        bound_class_id=target.id if target and target.type == "class" else None,
        bound_node_id=node_id,
        allowed_class_ids=class_ids,
        allowed_record_ids=allowed_records,
    )


def _catalog_context(db: DbSession, dataset_id: str, version: str) -> CatalogContract:
    row = db.get(models.Catalog, version)
    if row is None or row.dataset_id != dataset_id:
        raise DomainError("CONTEXT_MISMATCH", "课程目录不属于当前数据集")
    indicators = [item.payload_json for item in db.scalars(select(models.Indicator).where(models.Indicator.catalog_version == version)).all()]
    materials = [item.payload_json for item in db.scalars(select(models.Material).where(models.Material.catalog_version == version)).all()]
    payload = row.payload_json
    return CatalogContract(version=version, topics=payload.get("topics", []), indicators=indicators, materials=materials, links=payload.get("links", []))


@router.post("/chat")
def post_chat(payload: ChatRequest, db: DbSession, scope: Scope, settings: AppSettings) -> dict[str, Any]:
    scope.require("chat_own" if scope.role == "student" else "chat_teacher")
    provider = build_provider(settings)
    # Preserve the established response when no model is configured.
    if provider.name == "unconfigured":
        turn = provider.generate([message.model_dump() for message in payload.messages])
        return success({"text": turn.text or "", "tool_calls": []})

    target = payload.target
    if payload.student_id:
        target = ChatTarget(type="student", id=payload.student_id)
    elif target is None and scope.role == "student":
        target = ChatTarget(type="student", id=scope.student_id)
    if scope.role == "student" and target and (target.type != "student" or target.id != scope.student_id):
        raise DomainError("FORBIDDEN", "学生会话只能访问本人数据")
    if payload.task_type is not None:
        explicit = get_task(payload.task_type)
        if scope.role not in explicit.roles:
            raise DomainError("FORBIDDEN", "当前角色不能执行该任务")
        if explicit.target == "class" and target is not None and target.type != "class":
            raise DomainError("VALIDATION_ERROR", "该任务要求班级目标")
        if explicit.target == "student" and target is not None and target.type != "student":
            raise DomainError("VALIDATION_ERROR", "该任务要求学生目标")
    prompts = PromptRegistry()
    router_prompt, router_version = prompts.load("router.md")
    routed = route_task(
        [message.model_dump() for message in payload.messages if message.role != "system"], provider, scope.role,
        explicit_task_type=payload.task_type,
        router_prompt=(router_prompt, router_version),
    )
    task = routed.task
    if scope.role not in task.roles:
        raise DomainError("FORBIDDEN", "当前角色不能执行该任务")
    if task.status != "ready":
        raise DomainError("TASK_NOT_AVAILABLE", f"{task.label}暂不可用：{task.unavailable_reason}")
    if task.task_type == "out_of_scope":
        return success({"text": "我目前仅支持学情分析、作答依据和数据质量等学习支持任务。", "tool_trace": [], "stopped_reason": "out_of_scope",
                        "prompt_version": "task-out-of-scope-v1", "evidence_refs": [],
                        "task_type": task.task_type, "route_source": routed.source})

    if target is None and task.target != "none":
        if task.target == "student_or_dataset" and scope.role == "teacher":
            target = None
        else:
            raise DomainError("VALIDATION_ERROR", "该任务需要显式 target")
    if task.requires_run and not payload.run_id:
        raise DomainError("VALIDATION_ERROR", "该任务必须提供 run_id")
    if task.requires_node and not payload.node_id:
        raise DomainError("VALIDATION_ERROR", "该任务必须提供 node_id")
    context = _server_context(db, scope, payload.run_id, target, payload.node_id, task)
    registry = build_b_registry(context, scope.role, task)
    if task.task_type in {"knowledge_lookup", "prerequisite_query", "relation_review"}:
        if not context.catalog_version:
            raise DomainError("CONTEXT_MISMATCH", "知识查询需要绑定 catalog_version")
        catalog_contract = _catalog_context(db, context.dataset_id, context.catalog_version)
        registry = build_knowledge_registry(context, scope.role, task, settings.data_root, catalog_contract)
    context_description = "; ".join(filter(None, [
        f"run_id={context.run_id}" if context.run_id else "",
        f"target_type={target.type}" if target else "target_type=dataset",
        f"target_id={target.id}" if target else "",
        f"node_id={payload.node_id}" if payload.node_id else "",
    ]))
    system, prompt_version = prompts.compose(
        role=scope.role, task=task.label, task_type=task.task_type,
        context=context_description, router_prompt_version=routed.router_prompt_version,
    )
    messages = [{"role": "system", "content": system}] + [
        message.model_dump() for message in payload.messages if message.role != "system"
    ]
    result = AgentLoop(provider, registry).run(messages, task.label)
    return success({"text": result.text or "", "tool_trace": [trace.__dict__ for trace in result.tool_trace],
                    "stopped_reason": result.stopped_reason, "prompt_version": prompt_version,
                    "evidence_refs": result.state.evidence_refs,
                    "task_type": task.task_type, "route_source": routed.source})
