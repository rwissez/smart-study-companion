"""Student chat: wired end to end, honest about the missing model.

The route exists now so the frontend can build against a real URL, and it
fails with ``MODEL_NOT_CONFIGURED`` (503) rather than returning invented text.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sqlalchemy import func, select

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

    # 01 §9.5：新客户端用单条 message，旧客户端用 messages；两者互斥。
    messages: list[ChatMessage] | None = Field(default=None, max_length=40)
    message: str | None = Field(default=None, max_length=8000)
    conversation_id: str | None = Field(default=None, max_length=128)
    run_id: str | None = Field(default=None, max_length=64)
    student_id: str | None = Field(default=None, max_length=64)
    node_id: str | None = Field(default=None, max_length=64)
    target: ChatTarget | None = None
    task_type: str | None = Field(default=None, max_length=64)
    activity_id: str | None = Field(default=None, max_length=128)
    plan_version: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_target_alias(self):
        if (self.messages is None) == (self.message is None):
            raise ValueError("message 与 messages 必须且只能提供一个")
        if self.message is not None:
            if not self.message.strip():
                raise ValueError("message 不能为空")
            object.__setattr__(self, "messages", [ChatMessage(role="user", content=self.message)])
        if self.student_id and self.target and (self.target.type != "student" or self.target.id != self.student_id):
            raise ValueError("student_id 与 target 必须一致")
        return self


def _readonly_snapshots(
    db: DbSession, scope, target, node_id: str | None
) -> dict[str, object]:
    """按会话权限预取只读快照：当前方案与最近学习事件。

    工具只读这里，模型无法用参数扩大范围；快照里不含参考答案与评分细则。
    """

    snapshots: dict[str, object] = {
        "plans": [],
        "recent_events": [],
        "limitations": ["只读快照：服务端按会话权限预取，不含答案与评分细则"],
    }
    if target is None or target.type != "student" or scope.dataset_id is None:
        return snapshots
    student = db.get(models.Student, (scope.dataset_id, target.id))
    if student is None:
        return snapshots
    dataset = db.get(models.Dataset, scope.dataset_id)
    if dataset is None or not dataset.family_id:
        return snapshots
    heads = db.scalars(
        select(models.FormalHead)
        .where(
            models.FormalHead.family_id == dataset.family_id,
            models.FormalHead.dataset_id == scope.dataset_id,
            models.FormalHead.kind == "plan",
            models.FormalHead.target_key == target.id,
            models.FormalHead.version_id.is_not(None),
        )
        .order_by(models.FormalHead.updated_at.desc())
    ).all()
    plans: list[dict] = []
    for head in heads:
        if node_id and head.node_key != node_id:
            continue
        version = db.get(models.Version, head.version_id)
        if version is None or not version.payload_json:
            continue
        payload = version.payload_json or {}
        activities = []
        for recommendation in payload.get("recommendations", []) or []:
            for activity in recommendation.get("activities", []) or []:
                activities.append(
                    {
                        "activity_id": activity.get("activity_id"),
                        "kind": activity.get("activity_kind"),
                        "title": activity.get("title"),
                        "material_id": activity.get("material_id"),
                        "minutes": activity.get("estimate_minutes"),
                        "exposure_mode": activity.get("exposure_mode"),
                    }
                )
        plans.append(
            {
                "plan_version": version.version_id,
                "node_id": version.node_key,
                "student_id": target.id,
                "goal": (payload.get("recommendations") or [{}])[0].get("goal_target_key"),
                "reason": (payload.get("recommendations") or [{}])[0].get("reason"),
                "activities": activities,
                "limitations": payload.get("limitations") or [],
            }
        )
    snapshots["plans"] = plans

    student_uid = getattr(student, "student_uid", None)
    if student_uid:
        rows = db.scalars(
            select(models.LearningEventRow)
            .where(models.LearningEventRow.student_uid == student_uid)
            .order_by(models.LearningEventRow.offset.desc())
            .limit(10)
        ).all()
        snapshots["recent_events"] = [
            {
                "event_type": row.event_type,
                "occurred_at": row.occurred_at,
                "material_id": row.material_id,
                "activity_id": row.activity_id,
            }
            for row in rows
        ]
    return snapshots


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
    extras = _readonly_snapshots(db, scope, target, node_id)
    return ServerContext(
        run_id=run_id or "", dataset_id=scope.dataset_id,
        catalog_version=catalog_version or "", allowed_student_ids=allowed,
        bundle=bundle, dataset=prepared,
        bound_student_id=target.id if target and target.type == "student" else None,
        bound_class_id=target.id if target and target.type == "class" else None,
        bound_node_id=node_id,
        allowed_class_ids=class_ids,
        allowed_record_ids=allowed_records,
        extras=extras,
    )


def _catalog_context(db: DbSession, dataset_id: str, version: str) -> CatalogContract:
    row = db.get(models.Catalog, version)
    if row is None or row.dataset_id != dataset_id:
        raise DomainError("CONTEXT_MISMATCH", "课程目录不属于当前数据集")
    indicators = [item.payload_json for item in db.scalars(select(models.Indicator).where(models.Indicator.catalog_version == version)).all()]
    materials = [item.payload_json for item in db.scalars(select(models.Material).where(models.Material.catalog_version == version)).all()]
    payload = row.payload_json
    return CatalogContract(version=version, topics=payload.get("topics", []), indicators=indicators, materials=materials, links=payload.get("links", []))


def _persist_turn(db, scope, payload: "ChatRequest", body: dict[str, Any], *, status: str = "completed") -> dict[str, Any]:
    """把本轮对话写入持久化会话，并补齐新契约字段。

    旧客户端拿到同样的 ``text``；新客户端可用 ``conversation_id`` 继续多轮。
    会话不写业务事实，长期事实仍从正式仓储读取。
    """

    from study_companion.jobs.service import new_id, now_iso

    scope_key = f"{scope.role}:{scope.dataset_id or ''}:{scope.student_id or ''}"
    conversation_id = payload.conversation_id
    if conversation_id:
        conversation = db.get(models.Conversation, conversation_id)
        if conversation is None or conversation.owner_ref != scope.session_id:
            raise DomainError("NOT_FOUND", "会话不存在或不属于当前身份")
    else:
        conversation = models.Conversation(
            conversation_id=new_id("conv"),
            owner_ref=scope.session_id,
            scope_key=scope_key,
            dataset_id=scope.dataset_id,
            role=scope.role,
            title=(payload.message or "")[:40] or "新的对话",
            context_json={},
            context_revision=0,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        db.add(conversation)
        db.flush()
        conversation_id = conversation.conversation_id

    last = db.scalar(
        select(func.max(models.ChatMessageRow.sequence)).where(
            models.ChatMessageRow.conversation_id == conversation_id
        )
    )
    sequence = int(last or 0)
    user_text = payload.message or (payload.messages[-1].content if payload.messages else "")
    user_message = models.ChatMessageRow(
        message_id=new_id("msg"),
        conversation_id=conversation_id,
        sequence=sequence + 1,
        role="user",
        content=user_text,
        status="completed",
        created_at=now_iso(),
    )
    assistant_message = models.ChatMessageRow(
        message_id=new_id("msg"),
        conversation_id=conversation_id,
        sequence=sequence + 2,
        role="assistant",
        content=str(body.get("text", "")),
        segments_json=body.get("segments"),
        status=status,
        payload_json={
            "tool_trace": body.get("tool_trace", []),
            "evidence_refs": body.get("evidence_refs", []),
            "clarification": body.get("clarification"),
            "stopped_reason": body.get("stopped_reason"),
        },
        created_at=now_iso(),
    )
    db.add(user_message)
    db.add(assistant_message)
    conversation.updated_at = now_iso()
    db.flush()

    body.setdefault("conversation_id", conversation_id)
    body.setdefault("message_id", assistant_message.message_id)
    body.setdefault("status", "completed" if status == "completed" else status)
    body.setdefault("segments", [])
    body.setdefault("operation_results", [])
    return body


def _rule_fallback(db, scope, target, node_id: str | None) -> dict[str, Any] | None:
    """模型未配置时的确定性回答：只转述正式学情，不生成教学解释。

    这是 01/05 允许的"规则摘要"降级路径，输出明确标注为规则结果，不冒充模型回答。
    """

    from study_companion.db import version_repository as repo

    if scope.dataset_id is None:
        return None
    student_id = target.id if target is not None and target.type == "student" else None
    if student_id is None and scope.role == "student":
        student_id = scope.student_id
    if not student_id:
        return None
    dataset = db.get(models.Dataset, scope.dataset_id)
    if dataset is None or not dataset.family_id:
        return None
    node_key = node_id
    if not node_key:
        run_id = repo.latest_run_id(db, dataset.id)
        run = db.get(models.AnalysisRun, run_id) if run_id else None
        node_key = (run.node_ids_json or [None])[0] if run else None
    if not node_key:
        return None
    run_id = repo.active_run_id(db, dataset.id, node_key) or repo.latest_run_id(db, dataset.id)
    if not run_id:
        return None
    key = repo.FormalKey(
        family_id=dataset.family_id,
        dataset_id=dataset.id,
        kind="student_analysis",
        run_key=run_id,
        node_key=node_key,
        target_key=student_id,
    )
    version = repo.read_formal(db, key)
    if version is None:
        return None
    payload = dict(version.payload_json or {})
    catalog_row = db.get(models.Catalog, payload.get("catalog_version") or "")
    indicator_names: dict[str, str] = {}
    if catalog_row is not None:
        for indicator in (catalog_row.payload_json or {}).get("indicators", []):
            indicator_names[indicator.get("indicator_id", "")] = indicator.get("description", "")
    for row in db.scalars(
        select(models.Indicator).where(
            models.Indicator.catalog_version == (payload.get("catalog_version") or "")
        )
    ).all():
        indicator_names.setdefault(
            row.indicator_id, (row.payload_json or {}).get("description", "")
        )
    node_label = f"第 {node_key[-1]} 次观察" if node_key and node_key[-1].isdigit() else node_key
    lines = [
        f"以下是 {student_id} 在{node_label}的正式学情（规则摘要，未使用模型）："
    ]
    for metric in payload.get("metrics", [])[:6]:
        scope_info = metric.get("scope") or {}
        indicator_id = scope_info.get("indicator_id") or ""
        label = indicator_names.get(indicator_id) or scope_info.get("class_id") or "本次观察"
        numerator = metric.get("numerator")
        denominator = metric.get("denominator")
        if numerator is None or denominator in (None, 0):
            lines.append(f"- {label}：暂无有效分母（{metric.get('name', '')}）")
        else:
            lines.append(f"- {label}：{numerator}/{denominator}")
    status_text = {
        "sufficient": "证据充分",
        "insufficient": "证据不足",
        "conflicting": "证据冲突",
    }
    performance_text = {
        "observed_strength": "表现较好",
        "needs_practice": "仍需练习",
        "undetermined": "暂不判断",
    }
    for finding in payload.get("findings", [])[:4]:
        lines.append(
            f"- 诊断：{finding.get('description', '')}"
            f"（{status_text.get(finding.get('evidence_status'), finding.get('evidence_status'))}，"
            f"{performance_text.get(finding.get('performance'), finding.get('performance'))}）"
        )
    limitations = payload.get("limitations") or []
    if limitations:
        lines.append("限制：" + "；".join(str(item) for item in limitations[:3]))
    lines.append("需要教学解释或修改学习方案时，请先配置模型后再提问。")
    evidence = []
    for finding in payload.get("findings", [])[:4]:
        evidence.extend(finding.get("evidence_refs", []))
    return {
        "text": "\n".join(lines),
        "tool_trace": [],
        "evidence_refs": evidence[:20],
        "segments": [
            {"kind": "fact", "text": line, "evidence_refs": []}
            for line in lines[1:-1]
        ],
        "stopped_reason": "model_not_configured",
        "warnings": [
            {
                "code": "MODEL_NOT_CONFIGURED",
                "message": "当前为规则摘要，未使用模型生成教学解释",
            }
        ],
        "task_type": "student_analysis",
        "route_source": "rule_fallback",
    }


@router.post("/chat")
def post_chat(payload: ChatRequest, db: DbSession, scope: Scope, settings: AppSettings) -> dict[str, Any]:
    scope.require("chat_own" if scope.role == "student" else "chat_teacher")
    provider = build_provider(settings)
    # 未配置模型时先尝试确定性的正式学情摘要；拿不到上下文才返回明确不可用。
    if provider.name == "unconfigured":
        target = payload.target
        if payload.student_id:
            target = ChatTarget(type="student", id=payload.student_id)
        elif target is None and scope.role == "student":
            target = ChatTarget(type="student", id=scope.student_id)
        fallback = _rule_fallback(db, scope, target, payload.node_id)
        if fallback is not None:
            return success(_persist_turn(db, scope, payload, fallback, status="partial"))
        turn = provider.generate([message.model_dump() for message in payload.messages or []])
        return success(
            _persist_turn(
                db,
                scope,
                payload,
                {"text": turn.text or "", "tool_calls": []},
                status="failed",
            )
        )

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
        return success(
            _persist_turn(
                db,
                scope,
                payload,
                {
                    "text": "我目前仅支持学情分析、作答依据和数据质量等学习支持任务。",
                    "tool_trace": [],
                    "stopped_reason": "out_of_scope",
                    "prompt_version": "task-out-of-scope-v1",
                    "evidence_refs": [],
                    "task_type": task.task_type,
                    "route_source": routed.source,
                },
                status="out_of_scope",
            )
        )

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
    legacy_shape = payload.message is None
    tool_trace = []
    for index, trace in enumerate(result.tool_trace):
        item = {
            "call_id": getattr(trace, "call_id", f"call-{index}"),
            "tool_name": getattr(trace, "tool_name", ""),
            "stage": getattr(trace, "stage", "") or "",
            "status": getattr(trace, "status", "succeeded"),
            "message": getattr(trace, "message", "") or "",
            "evidence_refs": getattr(trace, "evidence_refs", []) or [],
        }
        if legacy_shape:
            # 旧客户端的验收用例依赖工具执行结果；新客户端按 01 §9.5 只拿安全投影。
            item["result"] = getattr(trace, "result", None)
        tool_trace.append(item)
    body = {
        "text": result.text or "",
        "tool_trace": tool_trace,
        "stopped_reason": result.stopped_reason,
        "prompt_version": prompt_version,
        "evidence_refs": result.state.evidence_refs,
        "task_type": task.task_type,
        "route_source": routed.source,
        "segments": [
            {"kind": "explanation", "text": result.text or "", "evidence_refs": result.state.evidence_refs}
        ]
        if result.text
        else [],
    }
    return success(
        _persist_turn(
            db,
            scope,
            payload,
            body,
            status="failed" if result.stopped_reason else "completed",
        )
    )
