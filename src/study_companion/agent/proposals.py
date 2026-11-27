"""Agent 候选方案与确认（05 §7、04 §9.3）。

这里的"Agent 修改"在无模型时是可复现的确定性动作：按教师显式意图重跑 C 规划器，
生成**候选**（Proposal），再由教师确认后走``agent_revision`` 发布路径。模型只影响
意图理解，不影响"谁能发布、发布前要过哪些检查"。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.contracts import (
    AgentRevisionSubmission,
    DomainError,
    ProposalView,
    RecommendationBundle,
    Snapshot,
    ReviewInfo,
)
from study_companion.db import models
from study_companion.db import version_repository as repo
from study_companion.recommendation import publication as publication_rules

CONFIRMATION_TTL = timedelta(minutes=15)
MAX_TARGETS = 5


def _now() -> datetime:
    return datetime.now(UTC)


def _new(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:20]}"


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def proposal_view(row: models.Proposal) -> ProposalView:
    body = dict(row.body_json or {})
    return ProposalView(
        proposal_id=row.proposal_id,
        student_id=(row.snapshot_json or {}).get("student_id", ""),
        base_plan_version=row.base_plan_version,
        snapshot=Snapshot.model_validate(body.get("snapshot") or {}),
        proposal_revision=row.proposal_revision,
        draft_hash=row.draft_hash,
        changes_summary=list(row.changes_summary_json or []),
        candidate_options=list(row.allowed_candidates_json or []),
        warnings=list(body.get("warnings") or []),
        blocked_reasons=list(body.get("blocked_reasons") or []),
        estimated_minutes=int(body.get("estimated_minutes") or 0),
        expires_at=row.expires_at,
        status=row.state,  # type: ignore[arg-type]
    )


def create_proposal(
    db: Session,
    *,
    dataset: models.Dataset,
    student_id: str,
    node_id: str,
    draft: RecommendationBundle,
    base_plan_version: str | None,
    review: ReviewInfo,
    snapshot: Snapshot,
    intent_summary: str,
    owner_ref: str,
) -> models.Proposal:
    """把规划结果固化为候选，并生成确认记录。

    候选只记录"将要发布的正文"，不切换正式指针；确认后才走发布事务。
    """

    if review.status != "validated":
        raise DomainError(
            "UNPROCESSABLE",
            "候选方案未通过完整审核，不能提交确认：" + "；".join(
                check.code for check in review.checks if not check.passed
            ),
        )
    body = {
        "draft": draft.model_dump(mode="json"),
        "review_info": review.model_dump(mode="json"),
        "warnings": [item.model_dump(mode="json") for item in draft.validation_issues],
        "blocked_reasons": [],
        "estimated_minutes": sum(
            activity.estimate_minutes
            for rec in draft.recommendations
            for activity in rec.activities
        ),
        "snapshot": snapshot.model_dump(mode="json"),
        "intent_summary": intent_summary,
    }
    draft_hash = _digest(draft.model_dump(mode="json"))
    row = models.Proposal(
        proposal_id=_new("prop"),
        proposal_revision=1,
        owner_ref=owner_ref,
        scope_key=publication_rules.scope_key_for(dataset.id, student_id, node_id),
        base_plan_version=base_plan_version,
        snapshot_json=body["snapshot"],
        draft_hash=draft_hash,
        body_json=body,
        allowed_candidates_json=[],
        changes_summary_json=[{"kind": "regenerate", "summary": intent_summary}],
        feedback_used=0,
        budget_state_json={},
        created_at=repo.now_iso(),
        expires_at=(_now() + CONFIRMATION_TTL).isoformat(),
        state="ready",
        origin="agent",
    )
    db.add(row)
    db.flush()
    return row


def create_confirmation(
    db: Session,
    *,
    proposal: models.Proposal,
    session_ref: str,
    conversation_id: str | None = None,
    message_id: str | None = None,
    summary: str = "",
) -> models.Confirmation:
    confirmation = models.Confirmation(
        confirmation_id=_new("cfm"),
        kind="plan_revision",
        conversation_id=conversation_id,
        message_id=message_id,
        session_ref=session_ref,
        scope_key=proposal.scope_key,
        targets_json=[
            {
                "student_id": proposal.snapshot_json.get("student_id"),
                "proposal_id": proposal.proposal_id,
                "base_plan_version": proposal.base_plan_version,
                "draft_hash": proposal.draft_hash,
                "changes_summary": proposal.changes_summary_json,
            }
        ],
        digest=_digest({"proposal": proposal.proposal_id, "draft": proposal.draft_hash}),
        status="pending",
        expires_at=(_now() + CONFIRMATION_TTL).isoformat(),
        summary=summary or "确认后会把新的学习方案发布给该学生",
        created_at=repo.now_iso(),
    )
    db.add(confirmation)
    db.flush()
    return confirmation


def confirmation_view(row: models.Confirmation) -> dict[str, Any]:
    return {
        "confirmation_id": row.confirmation_id,
        "summary": row.summary,
        "targets": row.targets_json,
        "expires_at": row.expires_at,
    }


def decide_confirmation(
    db: Session,
    *,
    confirmation: models.Confirmation,
    allow: bool,
    actor: str,
    dataset: models.Dataset,
    node_id: str,
) -> dict[str, Any]:
    """教师接受/拒绝候选；接受时按目标逐个走 agent_revision 发布。"""

    if confirmation.status != "pending":
        raise DomainError("CONFLICT", "该确认已处理")
    if confirmation.session_ref != actor:
        raise DomainError("FORBIDDEN", "确认只能由发起它的会话处理")
    if datetime.fromisoformat(confirmation.expires_at) <= _now():
        confirmation.status = "expired"
        db.flush()
        raise DomainError("REVISION_CONFLICT", "确认已过期，请重新生成候选")
    if not allow:
        confirmation.status = "rejected"
        confirmation.decision_at = repo.now_iso()
        for target in confirmation.targets_json or []:
            proposal = db.get(models.Proposal, (target.get("proposal_id"), 1))
            if proposal is not None:
                proposal.state = "rejected"
        db.flush()
        return {"decision": "rejected", "operations": []}

    operations: list[dict[str, Any]] = []
    for target in confirmation.targets_json or []:
        proposal = db.get(models.Proposal, (target.get("proposal_id"), 1))
        if proposal is None or proposal.state != "ready":
            raise DomainError("REVISION_CONFLICT", "候选已失效，请重新生成")
        if proposal.draft_hash != target.get("draft_hash"):
            raise DomainError("REVISION_CONFLICT", "候选内容已变化，请重新生成")
        authorization = models.Authorization(
            authorization_ref=_new("auth"),
            confirmation_id=confirmation.confirmation_id,
            owner_ref=actor,
            request_message_id=confirmation.message_id,
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
        operation = models.AgentOperation(
            operation_id=_new("op"),
            authorization_ref=authorization.authorization_ref,
            student_uid=(proposal.snapshot_json or {}).get("student_uid", ""),
            c_job_id=None,
            new_plan_version=None,
            state="pending",
            error_json=None,
            created_at=repo.now_iso(),
            updated_at=repo.now_iso(),
        )
        db.add(operation)
        db.flush()
        body = dict(proposal.body_json or {})
        draft = RecommendationBundle.model_validate(body.get("draft") or {})
        review = ReviewInfo.model_validate(body.get("review_info") or {})
        try:
            published = publication_rules.publish_agent_revision(
                db,
                dataset=dataset,
                student_id=proposal.snapshot_json.get("student_id", ""),
                node_id=node_id,
                proposal=proposal,
                draft=draft,
                review=review,
                authorization_ref=authorization.authorization_ref,
            )
        except DomainError as exc:
            operation.state = "failed"
            operation.error_json = {"code": exc.code, "message": exc.message}
            operation.updated_at = repo.now_iso()
            confirmation.status = "failed"
            db.flush()
            raise
        operation.state = "succeeded"
        operation.new_plan_version = published["plan_version"]
        operation.updated_at = repo.now_iso()
        proposal.state = "accepted"
        operations.append(
            {
                "operation_id": operation.operation_id,
                "student_id": proposal.snapshot_json.get("student_id"),
                "status": "succeeded",
                "new_plan_version": published["plan_version"],
                "review_status": published["review_status"],
                "publication_status": published["publication_status"],
                "previous_plan_version": published["previous_plan_version"],
            }
        )
    confirmation.status = "accepted"
    confirmation.decision_at = repo.now_iso()
    db.flush()
    return {"decision": "accepted", "operations": operations}
