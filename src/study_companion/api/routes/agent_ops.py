"""Agent 候选方案、确认与教师审核（05 §7、04 §8—9.3）。

无模型时，"让 Agent 改方案"是确定性的：按目标学生的正式学情重跑 C 规划器，
生成候选；教师确认后才走 ``agent_revision`` 发布。模型只影响意图理解，
发布门槛与审计不因模型而放松。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from study_companion.agent import proposals as proposal_rules
from study_companion.api.deps import DbSession, Scope
from study_companion.api.envelope import success
from study_companion.contracts import (
    DomainError,
    RecommendationBundle,
    RevisionChanges,
    Snapshot,
    StudentResult,
)
from study_companion.db import models
from study_companion.db import version_repository as repo
from study_companion.library.datasets import load_catalog, load_prepared_dataset
from study_companion.library.history import build_history
from study_companion.recommendation import planner as c_planner
from study_companion.recommendation import publication as publication_rules

router = APIRouter(tags=["agent-operations"])


class ProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_id: str = Field(min_length=1, max_length=64)
    node_id: str = Field(min_length=1, max_length=64)
    intent: str = Field(default="按当前学情重新生成一版安排", max_length=500)


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_id: str = Field(min_length=1, max_length=64)
    allow: bool


class PlanReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_plan_version: str | None = Field(default=None, max_length=128)
    decision: str = Field(pattern="^(approve|reject|edit)$")
    changes: RevisionChanges | None = None
    reason: str = Field(default="", max_length=500)


def _dataset_for(db: DbSession, scope: Scope, dataset_id: str) -> models.Dataset:
    dataset = db.get(models.Dataset, dataset_id)
    if dataset is None or not dataset.family_id:
        raise DomainError("NOT_FOUND", "数据集不存在")
    if scope.dataset_id is not None and scope.dataset_id != dataset_id:
        raise DomainError("FORBIDDEN", "当前会话无权访问该数据集")
    return dataset


def _current_student_result(
    db: DbSession, dataset: models.Dataset, student_id: str, node_id: str
) -> tuple[models.Version, StudentResult]:
    key = repo.FormalKey(
        family_id=dataset.family_id or "",
        dataset_id=dataset.id,
        kind="student_analysis",
        run_key=repo.active_run_id(db, dataset.id, node_id) or "",
        node_key=node_id,
        target_key=student_id,
    )
    row = repo.read_formal(db, key)
    if row is None or not row.payload_json:
        row = db.scalar(
            select(models.Version)
            .join(
                models.Publication,
                models.Publication.version_id == models.Version.version_id,
            )
            .where(
                models.Version.dataset_id == dataset.id,
                models.Version.kind == "student_analysis",
                models.Version.node_key == node_id,
                models.Version.target_key == student_id,
            )
            .order_by(models.Version.created_at.desc())
            .limit(1)
        )
    if row is None or not row.payload_json:
        raise DomainError("RESULT_NOT_READY", "该学生在此节点尚无正式学情")
    return row, StudentResult.model_validate(row.payload_json)


def build_candidate(
    db: DbSession,
    *,
    dataset: models.Dataset,
    student_id: str,
    node_id: str,
    intent: str,
    owner_ref: str,
    conversation_id: str | None = None,
) -> tuple[models.Proposal, models.Confirmation]:
    """按正式学情重跑 C 规划，产出候选与确认记录。"""

    analysis_row, student_result = _current_student_result(db, dataset, student_id, node_id)
    catalog_version = student_result.catalog_version
    catalog = load_catalog(db, catalog_version, dataset.id)
    prepared = load_prepared_dataset(db, dataset.id)
    student = db.get(models.Student, (dataset.id, student_id))
    if student is None or not student.student_uid:
        raise DomainError("NOT_FOUND", "该学生不在当前快照中")
    history = build_history(
        db,
        dataset=dataset,
        student_id=student_id,
        student_uid=student.student_uid,
        prepared=prepared,
        now=datetime.now(UTC),
    )
    node_available = next(
        (node.available_question_ids for node in prepared.nodes if node.node_id == node_id),
        None,
    )
    knowledge_payload = {}
    knowledge_row = repo.read_formal(
        db,
        repo.FormalKey(family_id=dataset.family_id or "", dataset_id=dataset.id, kind="knowledge"),
    )
    if knowledge_row is not None and knowledge_row.payload_json:
        knowledge_payload = dict(knowledge_row.payload_json)

    draft = c_planner.build_plan(
        c_planner.PlannerInputs(
            student_result=student_result,
            catalog=catalog,
            knowledge_payload=knowledge_payload,
            history=history,
            now=datetime.now(UTC),
            node_available_ids=node_available,
        )
    )
    review = c_planner.review_plan_rules(draft.bundle)
    plan_key = repo.FormalKey(
        family_id=dataset.family_id or "",
        dataset_id=dataset.id,
        kind="plan",
        node_key=node_id,
        target_key=student_id,
    )
    head = repo.read_head(db, plan_key)
    proposal = proposal_rules.create_proposal(
        db,
        dataset=dataset,
        student_id=student_id,
        node_id=node_id,
        draft=draft.bundle,
        base_plan_version=head.version_id if head else None,
        review=review,
        intent_summary=intent,
        owner_ref=owner_ref,
        snapshot=Snapshot(
            dataset_family_id=dataset.family_id or "",
            dataset_id=dataset.id,
            run_id=student_result.run_id,
            student_id=student_id,
            node_id=node_id,
            catalog_version=student_result.catalog_version,
            knowledge_version=(
                (analysis_row.payload_json or {}).get("analysis_input", {}).get("knowledge_version")
                or (analysis_row.payload_json or {}).get("knowledge_version")
                or ""
            ),
            analysis_version=student_result.analysis_version,
            event_cutoff=(
                student_result.analysis_input.event_cutoff
                if student_result.analysis_input
                else 0
            ),
            base_plan_version=head.version_id if head else None,
            config_hash="",
            rule_version=student_result.analysis_input.rule_version if student_result.analysis_input else "",
        ),
    )
    confirmation = proposal_rules.create_confirmation(
        db,
        proposal=proposal,
        session_ref=owner_ref,
        conversation_id=conversation_id,
        summary=f"确认后会把「{student_id}」的新方案发布，原方案保留历史",
    )
    return proposal, confirmation


@router.post("/conversations/{conversation_id}/proposals", status_code=202)
def create_proposal_endpoint(
    conversation_id: str,
    payload: ProposalRequest,
    db: DbSession,
    scope: Scope,
) -> dict:
    scope.require("chat_teacher")
    scope.require_teacher()
    conversation = db.get(models.Conversation, conversation_id)
    if conversation is None or conversation.owner_ref != scope.session_id:
        raise DomainError("NOT_FOUND", "会话不存在或不属于当前身份")
    if scope.dataset_id is None:
        raise DomainError("VALIDATION_ERROR", "请先选择数据集")
    dataset = _dataset_for(db, scope, scope.dataset_id)
    proposal, confirmation = build_candidate(
        db,
        dataset=dataset,
        student_id=payload.student_id,
        node_id=payload.node_id,
        intent=payload.intent,
        owner_ref=scope.session_id,
        conversation_id=conversation.conversation_id,
    )
    return success(
        {
            "proposal": proposal_rules.proposal_view(proposal).model_dump(mode="json"),
            "confirmation": proposal_rules.confirmation_view(confirmation),
            "status": "needs_confirmation",
        }
    )


@router.post("/conversations/{conversation_id}/confirm")
def confirm_endpoint(
    conversation_id: str,
    payload: ConfirmRequest,
    db: DbSession,
    scope: Scope,
    node_id: str = Query(min_length=1, max_length=64),
) -> dict:
    scope.require("chat_teacher")
    scope.require_teacher()
    conversation = db.get(models.Conversation, conversation_id)
    if conversation is None or conversation.owner_ref != scope.session_id:
        raise DomainError("NOT_FOUND", "会话不存在或不属于当前身份")
    confirmation = db.get(models.Confirmation, payload.confirmation_id)
    if confirmation is None or confirmation.conversation_id != conversation_id:
        raise DomainError("NOT_FOUND", "确认记录不存在")
    if scope.dataset_id is None:
        raise DomainError("VALIDATION_ERROR", "请先选择数据集")
    dataset = _dataset_for(db, scope, scope.dataset_id)
    result = proposal_rules.decide_confirmation(
        db,
        confirmation=confirmation,
        allow=payload.allow,
        actor=scope.session_id,
        dataset=dataset,
        node_id=node_id,
    )
    return success(result)


@router.get("/datasets/{dataset_id}/plan-reviews")
def list_plan_reviews(
    dataset_id: str,
    db: DbSession,
    scope: Scope,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> dict:
    scope.require("view_class_analysis")
    _dataset_for(db, scope, dataset_id)
    rows = [
        row
        for row in db.scalars(
            select(models.Proposal)
            .where(models.Proposal.origin == "agent", models.Proposal.state == "ready")
            .order_by(models.Proposal.created_at.desc())
        ).all()
        if (row.snapshot_json or {}).get("dataset_id") == dataset_id
    ]
    start = (page - 1) * page_size

    def _precheck(row: models.Proposal) -> dict:
        body = row.body_json or {}
        draft = body.get("draft") or {}
        activities = [
            activity
            for recommendation in draft.get("recommendations", []) or []
            for activity in recommendation.get("activities", []) or []
        ]
        draft_ids = {activity.get("activity_id") for activity in activities}
        protected: set[str] = set()
        if row.base_plan_version:
            states = db.scalars(
                select(models.ActivityStateRow).where(
                    models.ActivityStateRow.origin_plan_version == row.base_plan_version
                )
            ).all()
            protected = {
                state.activity_id
                for state in states
                if state.state in {"completed", "in_progress"}
            }
        removed = sorted(item for item in protected - draft_ids if item)
        goal = next(
            (
                recommendation.get("goal_target_key")
                for recommendation in draft.get("recommendations", []) or []
                if recommendation.get("goal_target_key")
            ),
            "",
        )
        return {
            "node_id": (row.snapshot_json or {}).get("node_id", ""),
            "goal": goal,
            "activity_count": len(activities),
            "hard_block": bool(removed),
            "blocked_reason": "候选移除了已完成或进行中的活动" if removed else "",
            "blocked_activity_ids": removed,
            "activities": [
                {
                    "activity_id": activity.get("activity_id"),
                    "activity_kind": activity.get("activity_kind"),
                    "material_id": activity.get("material_id"),
                    "title": (activity.get("title") or "")[:80],
                    "estimate_minutes": activity.get("estimate_minutes"),
                    "exposure_mode": activity.get("exposure_mode"),
                }
                for activity in activities
            ],
        }

    items = []
    for row in rows[start : start + page_size]:
        precheck = _precheck(row)
        items.append(
            {
                "review_id": row.proposal_id,
                "student_id": (row.snapshot_json or {}).get("student_id", ""),
                "node_id": precheck["node_id"],
                "plan_version": row.base_plan_version or "",
                "review_info": (row.body_json or {}).get("review_info", {}),
                "summary": "；".join(
                    str(item.get("summary", "")) for item in (row.changes_summary_json or [])
                ),
                "changes": {
                    "goal": precheck["goal"],
                    "activity_count": precheck["activity_count"],
                    "estimated_minutes": (row.body_json or {}).get("estimated_minutes"),
                },
                "precheck": {
                    "hard_block": precheck["hard_block"],
                    "reason": precheck["blocked_reason"],
                    "blocked_activity_ids": precheck["blocked_activity_ids"],
                },
                "activities": precheck["activities"],
                "estimated_minutes": (row.body_json or {}).get("estimated_minutes"),
                "warnings": (row.body_json or {}).get("warnings", []),
                "expires_at": row.expires_at,
            }
        )
    return success({"items": items, "total": len(rows), "page": page, "page_size": page_size})


@router.post("/datasets/{dataset_id}/plan-reviews/{review_id}")
def decide_plan_review(
    dataset_id: str,
    review_id: str,
    payload: PlanReviewRequest,
    db: DbSession,
    scope: Scope,
    node_id: str = Query(min_length=1, max_length=64),
) -> dict:
    """教师对 Agent 候选的审核：通过则走 agent_revision 发布，驳回保留历史。"""

    scope.require("edit_recommendations")
    scope.require_teacher()
    dataset = _dataset_for(db, scope, dataset_id)
    proposal = db.get(models.Proposal, (review_id, 1))
    if proposal is None or proposal.origin != "agent":
        raise DomainError("NOT_FOUND", "候选不存在")
    if proposal.state != "ready":
        raise DomainError("CONFLICT", "该候选已处理或已过期")

    if payload.decision == "reject":
        proposal.state = "rejected"
        db.flush()
        return success({"review_id": review_id, "review_status": "rejected", "publication_status": "unpublished"})

    body = dict(proposal.body_json or {})
    draft = RecommendationBundle.model_validate(body.get("draft") or {})
    if payload.decision == "edit":
        if payload.changes is None:
            raise DomainError("VALIDATION_ERROR", "编辑审核必须提供 changes")
        plan_key = repo.FormalKey(
            family_id=dataset.family_id or "",
            dataset_id=dataset.id,
            kind="plan",
            node_key=node_id,
            target_key=proposal.snapshot_json.get("student_id", ""),
        )
        head = repo.read_head(db, plan_key)
        base_version = payload.base_plan_version or (head.version_id if head else None)
        if base_version is None:
            raise DomainError("RESULT_NOT_READY", "缺少可编辑的基础方案")
        preview = publication_rules.preview_teacher_revision(
            db,
            dataset=dataset,
            student_id=proposal.snapshot_json.get("student_id", ""),
            node_id=node_id,
            base_plan_version=base_version,
            changes=payload.changes,
            reason=payload.reason or "教师审核 Agent 候选时修改",
            actor=scope.session_id,
        )
        return success(
            {
                "review_id": review_id,
                "review_status": "needs_review",
                "publication_status": "unpublished",
                "preview_id": preview.preview_id,
                "warning_digest": preview.warning_digest,
                "warnings": [item.model_dump(mode="json") for item in preview.warnings],
            }
        )

    authorization = models.Authorization(
        authorization_ref=f"auth-{proposal.proposal_id}",
        confirmation_id=None,
        owner_ref=scope.session_id,
        request_message_id=None,
        scope_key=proposal.scope_key,
        proposal_id=proposal.proposal_id,
        proposal_revision=proposal.proposal_revision,
        draft_hash=proposal.draft_hash,
        base_plan_version=proposal.base_plan_version,
        kind="plan_revision",
        created_at=repo.now_iso(),
    )
    db.add(authorization)
    db.flush()
    from study_companion.contracts import ReviewInfo

    result = publication_rules.publish_agent_revision(
        db,
        dataset=dataset,
        student_id=proposal.snapshot_json.get("student_id", ""),
        node_id=node_id,
        proposal=proposal,
        draft=draft,
        review=ReviewInfo.model_validate(body.get("review_info") or {}),
        authorization_ref=authorization.authorization_ref,
    )
    proposal.state = "accepted"
    db.flush()
    return success({"review_id": review_id, **result})
