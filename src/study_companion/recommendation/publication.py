"""C 的三条发布路径（04 §8、C-30）。

| 路径 | 审核 | 记录 |
|---|---|---|
| system_auto | 完整审核 validated | basis=automatic_review |
| agent_revision | 完整审核 validated + 真实教师授权 | 记录授权与操作 |
| teacher_direct | 硬门通过 + 教师确认教学警告 | status=human_confirmed, basis=teacher_confirmation |

共同点：都在同一事务里 CAS 切换正式指针，失败不产生半发布；旧版本保留历史，
已完成/进行中的活动身份不因重排而丢失。
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
    Activity,
    Catalog,
    DomainError,
    Material,
    Recommendation,
    RecommendationBundle,
    ReviewCheck,
    ReviewInfo,
    RevisionChanges,
    TaskGroup,
    TeacherPreview,
    ValidationIssue,
)
from study_companion.db import models
from study_companion.db import version_repository as repo
from study_companion.recommendation.leak_check import check_hint

PREVIEW_TTL = timedelta(minutes=10)
MAX_ACTIVITIES_PER_PLAN = 8
MAX_MINUTES_PER_PLAN = 60


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def scope_key_for(dataset_id: str, student_id: str, node_id: str) -> str:
    return _hash([dataset_id, student_id, node_id])[:32]


# --------------------------------------------------------------------------- #
# 纯函数：把教师改动应用到基础方案
# --------------------------------------------------------------------------- #


def apply_changes(
    base: RecommendationBundle,
    changes: RevisionChanges,
    *,
    catalog: Catalog,
    plan_version: str,
) -> RecommendationBundle:
    """只允许改 goal / reason / activities / adjustment_conditions / student_intro / lessons。"""

    materials = {item.material_id: item for item in catalog.materials}
    recommendations: list[Recommendation] = []
    for index, rec in enumerate(base.recommendations, start=1):
        activities = rec.activities
        if changes.activities is not None and index == 1:
            activities = _rebuild_activities(changes.activities, materials, plan_version, index)
        recommendations.append(
            rec.model_copy(
                update={
                    "activities": activities,
                    "goal": changes.goal if changes.goal and index == 1 else rec.goal,
                    "reason": changes.reason if changes.reason and index == 1 else rec.reason,
                    "student_intro": (
                        changes.student_intro if changes.student_intro is not None and index == 1 else rec.student_intro
                    ),
                }
            )
        )
    groups: list[TaskGroup] = []
    for group in base.task_groups:
        rec = next((item for item in recommendations if item.group_id == group.group_id), None)
        groups.append(
            group.model_copy(
                update={
                    "activity_ids": [item.activity_id for item in rec.activities] if rec else group.activity_ids,
                    "required_activity_ids": [
                        item.activity_id for item in rec.activities
                    ]
                    if rec
                    else group.required_activity_ids,
                    "student_intro": rec.student_intro if rec else group.student_intro,
                }
            )
        )
    return base.model_copy(
        update={
            "recommendations": recommendations,
            "task_groups": groups,
            "plan_version": plan_version,
            "parent_plan_version": base.plan_version or None,
            "publication_status": "unpublished",
            "created_at": repo.now_iso(),
        }
    )


def _rebuild_activities(
    requested: list[Activity],
    materials: dict[str, Material],
    plan_version: str,
    index: int,
) -> list[Activity]:
    rebuilt: list[Activity] = []
    for order, activity in enumerate(requested, start=1):
        if not activity.material_id:
            raise DomainError("MATERIAL_INVALID", "活动缺少材料编号")
        material = materials.get(activity.material_id)
        if material is None:
            raise DomainError(
                "MATERIAL_INVALID",
                f"材料 {activity.material_id} 不在该目录版本中，不能加入方案",
                [{"field": "material_id", "reason": "目录中不存在"}],
            )
        if material.material_type == "question" and material.question_type is not None:
            if len(activity.hint_levels) not in (0, 3):
                raise DomainError(
                    "MATERIAL_INVALID",
                    "正式题目必须提供三级提示或完全留空",
                    [{"field": "hint_levels", "reason": "需要 3 条提示"}],
                )
        rebuilt.append(
            activity.model_copy(
                update={
                    "order": order,
                    "activity_id": activity.activity_id or f"act-{plan_version}-{index:02d}-{order:02d}",
                    "group_id": activity.group_id or f"grp-{plan_version}-{index:02d}",
                    "activity_kind": "question" if material.material_type == "question" else "study_material",
                    "state": "not_started",
                }
            )
        )
    return rebuilt


# --------------------------------------------------------------------------- #
# 硬门与教学警告
# --------------------------------------------------------------------------- #


def hard_checks(
    base: RecommendationBundle,
    draft: RecommendationBundle,
    *,
    catalog: Catalog,
    completed_activity_ids: set[str],
) -> list[ValidationIssue]:
    errors: list[ValidationIssue] = []
    materials = {item.material_id: item for item in catalog.materials}

    for group in draft.task_groups:
        unknown = set(group.required_activity_ids) - set(group.activity_ids)
        if unknown:
            errors.append(
                ValidationIssue(
                    code="GROUP_SCOPE_INVALID",
                    message=f"任务组 {group.group_id} 的必需活动不属于本组",
                    severity="error",
                    field=group.group_id,
                )
            )
    for rec in draft.recommendations:
        for activity in rec.activities:
            if activity.material_id and activity.material_id not in materials:
                errors.append(
                    ValidationIssue(
                        code="MATERIAL_INVALID",
                        message=f"材料 {activity.material_id} 不存在于该目录版本",
                        severity="error",
                        field="material_id",
                        activity_id=activity.activity_id,
                    )
                )
            if not activity.hint_levels and (activity.material_id in materials):
                material = materials[activity.material_id]
                if material.question_type is not None:
                    errors.append(
                        ValidationIssue(
                            code="HINT_REQUIRED",
                            message="正式题目必须提供三级提示",
                            severity="error",
                            activity_id=activity.activity_id,
                        )
                    )
            for level, hint in enumerate(activity.hint_levels, start=1):
                material = materials.get(activity.material_id or "")
                if material is None:
                    continue
                leak = check_hint(material.reference_answer, material.reference_explanation, hint)
                if leak:
                    errors.append(
                        ValidationIssue(
                            code="ANSWER_PROTECTION",
                            message=f"第 {level} 级提示存在问题：{leak}",
                            severity="error",
                            activity_id=activity.activity_id,
                        )
                    )
    base_completed = completed_activity_ids & {
        activity.activity_id for rec in base.recommendations for activity in rec.activities
    }
    draft_ids = {activity.activity_id for rec in draft.recommendations for activity in rec.activities}
    missing_completed = base_completed - draft_ids
    if missing_completed:
        errors.append(
            ValidationIssue(
                code="HISTORY_PROTECTED",
                message="不能移除已完成或进行中的活动",
                severity="error",
            )
        )
    total_activities = sum(len(rec.activities) for rec in draft.recommendations)
    if total_activities > MAX_ACTIVITIES_PER_PLAN:
        errors.append(
            ValidationIssue(
                code="LOAD_EXCEEDED",
                message=f"活动总数超过 {MAX_ACTIVITIES_PER_PLAN} 项上限",
                severity="error",
            )
        )
    minutes = sum(
        activity.estimate_minutes for rec in draft.recommendations for activity in rec.activities
    )
    if minutes > MAX_MINUTES_PER_PLAN:
        errors.append(
            ValidationIssue(
                code="LOAD_EXCEEDED",
                message=f"预计总时长 {minutes} 分钟超过 {MAX_MINUTES_PER_PLAN} 分钟上限",
                severity="error",
            )
        )
    return errors


def soft_warnings(draft: RecommendationBundle) -> list[ValidationIssue]:
    warnings: list[ValidationIssue] = []
    for rec in draft.recommendations:
        if not rec.student_intro.strip():
            warnings.append(
                ValidationIssue(
                    code="MISSING_STUDENT_INTRO",
                    message="学生端缺少开场说明，学生可能不知道今天为什么这样安排",
                    severity="warning",
                    recommendation_id=rec.recommendation_id,
                )
            )
        if not rec.activities:
            warnings.append(
                ValidationIssue(
                    code="EMPTY_GROUP",
                    message="该目标没有任何活动，学生端会显示为暂无安排",
                    severity="warning",
                    recommendation_id=rec.recommendation_id,
                )
            )
    minutes = sum(
        activity.estimate_minutes for rec in draft.recommendations for activity in rec.activities
    )
    if minutes >= 40:
        warnings.append(
            ValidationIssue(
                code="HEAVY_LOAD",
                message=f"预计总时长 {minutes} 分钟偏重，建议分次完成",
                severity="warning",
            )
        )
    return warnings


# --------------------------------------------------------------------------- #
# 教师直编：预览 → 确认 → 发布
# --------------------------------------------------------------------------- #


def _plan_version_row(db: Session, plan_version: str) -> models.Version:
    row = db.get(models.Version, plan_version)
    if row is None or row.kind != "plan":
        raise DomainError("NOT_FOUND", "基础学习方案版本不存在")
    return row


def preview_teacher_revision(
    db: Session,
    *,
    dataset: models.Dataset,
    student_id: str,
    node_id: str,
    base_plan_version: str,
    changes: RevisionChanges,
    reason: str,
    actor: str,
) -> TeacherPreview:
    base_row = _plan_version_row(db, base_plan_version)
    if base_row.target_key != student_id or base_row.dataset_id != dataset.id:
        raise DomainError("FORBIDDEN", "该方案不属于此学生或数据集")
    base = RecommendationBundle.model_validate(base_row.payload_json or {})
    catalog_version = base.catalog_version
    from study_companion.library.datasets import load_catalog

    catalog = load_catalog(db, catalog_version, dataset.id)

    draft_version_id = repo.new_id("plan")
    draft = apply_changes(base, changes, catalog=catalog, plan_version=draft_version_id)
    student_status = db.scalars(
        select(models.ActivityStateRow).where(
            models.ActivityStateRow.origin_plan_version == base_plan_version
        )
    ).all()
    protected = {
        row.activity_id for row in student_status if row.state in {"completed", "in_progress"}
    }
    errors = hard_checks(base, draft, catalog=catalog, completed_activity_ids=protected)
    if errors:
        raise DomainError(
            "UNPROCESSABLE",
            "改动未通过硬性检查：" + "；".join(issue.message for issue in errors[:3]),
            [{"field": issue.activity_id or issue.field or "activities", "reason": issue.message} for issue in errors],
        )
    warnings = soft_warnings(draft)
    draft_hash = _hash(draft.model_dump(mode="json"))
    warning_digest = _hash([item.model_dump(mode="json") for item in warnings])

    proposal = models.Proposal(
        proposal_id=repo.new_id("prop"),
        proposal_revision=1,
        owner_ref=actor,
        scope_key=scope_key_for(dataset.id, student_id, node_id),
        base_plan_version=base_plan_version,
        snapshot_json={"dataset_id": dataset.id, "student_id": student_id, "node_id": node_id},
        draft_hash=draft_hash,
        body_json={
            "changes": changes.model_dump(mode="json"),
            "reason": reason,
            "draft": draft.model_dump(mode="json"),
            "warnings": [item.model_dump(mode="json") for item in warnings],
        },
        allowed_candidates_json=[],
        changes_summary_json=[{"kind": "teacher_edit", "reason": reason}],
        feedback_used=0,
        budget_state_json={},
        created_at=repo.now_iso(),
        expires_at=(_now() + PREVIEW_TTL).isoformat(),
        state="ready",
        origin="teacher_editor",
    )
    db.add(proposal)
    db.flush()
    return TeacherPreview(
        preview_id=proposal.proposal_id,
        draft_hash=draft_hash,
        warning_digest=warning_digest,
        warnings=warnings,
        expires_at=proposal.expires_at,
    )


def publish_teacher_revision(
    db: Session,
    *,
    dataset: models.Dataset,
    student_id: str,
    node_id: str,
    preview_id: str,
    warning_digest: str,
    actor: str,
) -> dict[str, Any]:
    proposal = db.get(models.Proposal, (preview_id, 1))
    if proposal is None or proposal.origin != "teacher_editor":
        raise DomainError("NOT_FOUND", "教师编辑预览不存在")
    if proposal.state != "ready":
        raise DomainError("CONFLICT", "该预览已失效或已提交")
    if datetime.fromisoformat(proposal.expires_at) <= _now():
        proposal.state = "expired"
        raise DomainError("REVISION_CONFLICT", "预览已过期，请重新检查并预览")
    body = dict(proposal.body_json or {})
    warnings = [ValidationIssue.model_validate(item) for item in body.get("warnings", [])]
    expected_digest = _hash([item.model_dump(mode="json") for item in warnings])
    if warning_digest != expected_digest:
        raise DomainError("REVISION_CONFLICT", "警告摘要与预览不一致，请重新预览")

    draft = RecommendationBundle.model_validate(body.get("draft") or {})
    base_plan_version = proposal.base_plan_version or ""
    base_row = _plan_version_row(db, base_plan_version)
    key = repo.FormalKey(
        family_id=dataset.family_id or "",
        dataset_id=dataset.id,
        kind="plan",
        node_key=node_id,
        target_key=student_id,
    )
    head = repo.read_head(db, key)
    if head is None or head.version_id != base_plan_version:
        raise DomainError("REVISION_CONFLICT", "基础方案已被更新，请重新预览")

    review = ReviewInfo(
        status="human_confirmed",
        method="human",
        checks=[
            ReviewCheck(code="hard_gate", passed=True, reason="硬性检查通过（材料、历史、提示、负担）"),
            ReviewCheck(
                code="teacher_confirmation",
                passed=True,
                reason=f"教师确认 {len(warnings)} 条教学警告后发布",
            ),
        ],
        reviewed_at=repo.now_iso(),
        reviewer_session_id=actor,
        rule_version="c-teacher-direct",
        issues=warnings,
        draft_hash=proposal.draft_hash,
    )
    plan_version_id = repo.new_id("plan")
    final = draft.model_copy(
        update={
            "plan_version": plan_version_id,
            "parent_plan_version": base_plan_version,
            "review_status": "human_confirmed",
            "publication_status": "published",
            "publication_basis": "teacher_confirmation",
            "publish_path": "teacher_direct",
            "created_at": repo.now_iso(),
            "review_info": review,
        }
    )
    version = repo.create_version(
        db,
        kind="plan",
        key=key,
        payload=final.model_dump(mode="json"),
        rule_version="c-teacher-direct",
        input_digest=proposal.draft_hash,
        job_id=None,
        version_id=plan_version_id,
        origin_kind="teacher",
    )
    base_version = db.get(models.Version, base_plan_version)
    if base_version is not None:
        repo.register_edge(db, version, base_version, role="parent_plan")
        edges = repo.dependencies(db, base_version)
        for edge in edges:
            repo.register_edge(db, version, db.get(models.Version, edge.dep_version_id), role=edge.role)

    repo.publish(
        db,
        key=key,
        version=version,
        path="teacher_direct",
        basis="teacher_confirmation",
        review_id="",
        actor_ref=actor,
        expected_version_id=head.version_id,
        expected_generation=head.generation,
    )
    _write_plan_rows(db, version.version_id, final, student_uid=_student_uid(dataset, db, student_id))
    revision = models.PlanRevision(
        revision_id=repo.new_id("rev"),
        proposal_id=proposal.proposal_id,
        proposal_revision=proposal.proposal_revision,
        scope_key=proposal.scope_key,
        base_plan_version=base_plan_version,
        restore_version=None,
        result_version=version.version_id,
        reason=str(body.get("reason", ""))[:500],
        origin="teacher_direct",
        job_id=None,
        created_at=repo.now_iso(),
    )
    db.add(revision)
    proposal.state = "accepted"
    db.add(
        models.SystemAudit(
            audit_id=repo.new_id("audit"),
            scope_key=proposal.scope_key,
            actor_ref=actor,
            action="plan_teacher_publish",
            subject_ref=student_id,
            before_ref=base_plan_version,
            after_ref=version.version_id,
            details_json={"warnings": [item.model_dump(mode="json") for item in warnings]},
            created_at=repo.now_iso(),
        )
    )
    db.flush()
    return {
        "revision_id": revision.revision_id,
        "plan_version": version.version_id,
        "review_status": "human_confirmed",
        "publication_status": "published",
        "publication_basis": "teacher_confirmation",
        "warnings": [item.model_dump(mode="json") for item in warnings],
    }


def _student_uid(dataset: models.Dataset, db: Session, student_id: str) -> str | None:
    row = db.scalar(
        select(models.StudentIdentity).where(
            models.StudentIdentity.family_id == dataset.family_id,
            models.StudentIdentity.external_id == student_id,
        )
    )
    return row.student_uid if row else None


def _write_plan_rows(
    db: Session, plan_version: str, plan: RecommendationBundle, *, student_uid: str | None
) -> None:
    """写活动/状态查找表；已存在的活动保留原状态。"""

    existing = {
        row.activity_id
        for row in db.scalars(
            select(models.PlanActivity).where(models.PlanActivity.plan_version == plan_version)
        ).all()
    }
    for group in plan.task_groups:
        if db.get(models.PlanGroup, (plan_version, group.group_id)) is None:
            db.add(
                models.PlanGroup(
                    plan_version=plan_version,
                    group_id=group.group_id,
                    position=group.order,
                    goal_key=group.goal_target_key or group.group_id,
                )
            )
        rec = next((item for item in plan.recommendations if item.group_id == group.group_id), None)
        if rec is None:
            continue
        for order, activity in enumerate(rec.activities, start=1):
            if activity.activity_id in existing:
                continue
            db.add(
                models.PlanActivity(
                    plan_version=plan_version,
                    activity_id=activity.activity_id,
                    group_id=group.group_id,
                    origin_plan_version=plan.parent_plan_version,
                    kind=activity.activity_kind,
                    catalog_version=plan.catalog_version,
                    material_id=activity.material_id,
                    content_version=activity.content_version,
                    position=order,
                )
            )
            if student_uid:
                # 继承来的活动沿用它已有的状态行：不覆盖已完成/进行中状态，也不重复插入。
                existing_state = db.get(models.ActivityStateRow, activity.activity_id)
                if existing_state is None:
                    db.add(
                        models.ActivityStateRow(
                            activity_id=activity.activity_id,
                            origin_plan_version=plan_version,
                            student_uid=student_uid,
                            state="not_started",
                            generation=1,
                            updated_at=repo.now_iso(),
                        )
                    )
    db.flush()


def publish_agent_revision(
    db: Session,
    *,
    dataset: models.Dataset,
    student_id: str,
    node_id: str,
    proposal: models.Proposal,
    draft: RecommendationBundle,
    review: ReviewInfo,
    authorization_ref: str,
) -> dict[str, Any]:
    """Agent 候选的正式发布（04 §8 的 ``agent_revision`` 路径）。

    前置：候选已通过完整审核、基础版本仍然当前、活动历史不被删改。
    正式指针的切换与教师直编共用同一 CAS，失败不产生半发布。
    """

    from study_companion.library.datasets import load_catalog

    if review.status != "validated":
        raise DomainError("UNPROCESSABLE", "候选未通过完整审核，不能发布")
    key = repo.FormalKey(
        family_id=dataset.family_id or "",
        dataset_id=dataset.id,
        kind="plan",
        node_key=node_id,
        target_key=student_id,
    )
    head = repo.read_head(db, key)
    current_version = head.version_id if head else None
    if proposal.base_plan_version != current_version:
        raise DomainError("DEPENDENCY_STALE", "基础方案已被更新，候选失效，请重新生成")

    base_row = (
        db.get(models.Version, proposal.base_plan_version)
        if proposal.base_plan_version
        else None
    )
    if base_row is None or not base_row.payload_json:
        raise DomainError("DEPENDENCY_STALE", "基础方案版本不可读")
    base = RecommendationBundle.model_validate(base_row.payload_json)
    catalog = load_catalog(db, draft.catalog_version, dataset.id)
    protected = {
        row.activity_id
        for row in db.scalars(
            select(models.ActivityStateRow).where(
                models.ActivityStateRow.origin_plan_version == proposal.base_plan_version
            )
        ).all()
        if row.state in {"completed", "in_progress"}
    }
    errors = hard_checks(base, draft, catalog=catalog, completed_activity_ids=protected)
    if errors:
        raise DomainError(
            "UNPROCESSABLE",
            "候选未通过硬性检查：" + "；".join(item.message for item in errors[:3]),
            [{"field": item.activity_id or item.field or "activities", "reason": item.message} for item in errors],
        )

    plan_version_id = repo.new_id("plan")
    final = draft.model_copy(
        update={
            "plan_version": plan_version_id,
            "parent_plan_version": proposal.base_plan_version,
            "review_status": "validated",
            "publication_status": "published",
            "publication_basis": "automatic_review",
            "publish_path": "agent_revision",
            "review_info": review,
            "created_at": repo.now_iso(),
        }
    )
    version = repo.create_version(
        db,
        kind="plan",
        key=key,
        payload=final.model_dump(mode="json"),
        rule_version="c-agent-revision",
        input_digest=proposal.draft_hash,
        job_id=None,
        version_id=plan_version_id,
        origin_kind="agent",
    )
    repo.register_edge(db, version, base_row, role="parent_plan")
    for edge in repo.dependencies(db, base_row):
        dep = db.get(models.Version, edge.dep_version_id)
        if dep is not None:
            repo.register_edge(db, version, dep, role=edge.role)

    repo.publish(
        db,
        key=key,
        version=version,
        path="agent_revision",
        basis="automatic_review",
        review_id="",
        actor_ref=authorization_ref,
        expected_version_id=head.version_id if head else None,
        expected_generation=head.generation if head else 0,
    )
    _write_plan_rows(
        db,
        plan_version_id,
        final,
        student_uid=_student_uid(dataset, db, student_id),
    )
    db.add(
        models.PlanRevision(
            revision_id=repo.new_id("rev"),
            proposal_id=proposal.proposal_id,
            proposal_revision=proposal.proposal_revision,
            scope_key=proposal.scope_key,
            base_plan_version=proposal.base_plan_version,
            restore_version=None,
            result_version=plan_version_id,
            reason=str((proposal.body_json or {}).get("intent_summary", ""))[:500],
            origin="agent_revision",
            job_id=None,
            created_at=repo.now_iso(),
        )
    )
    db.add(
        models.SystemAudit(
            audit_id=repo.new_id("audit"),
            scope_key=proposal.scope_key,
            actor_ref=authorization_ref,
            action="plan_agent_publish",
            subject_ref=student_id,
            before_ref=proposal.base_plan_version,
            after_ref=plan_version_id,
            details_json={"proposal_id": proposal.proposal_id, "draft_hash": proposal.draft_hash},
            created_at=repo.now_iso(),
        )
    )
    db.flush()
    return {
        "plan_version": plan_version_id,
        "previous_plan_version": proposal.base_plan_version,
        "review_status": "validated",
        "publication_status": "published",
        "publication_basis": "automatic_review",
        "publish_path": "agent_revision",
    }
