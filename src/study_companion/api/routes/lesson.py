"""微课堂读取与完成（04 §6、§9.1）。

* 只返回已发布、已审核的安全正文；
* 完成只记"接触"，不产生分数、不推断掌握；
* 学生只能读本人方案内仍可访问的活动；教师可按 preview 读取草稿（当前仅正式版）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from study_companion.api.deps import DbSession, Scope
from study_companion.api.envelope import success
from study_companion.contracts import DomainError, RecommendationBundle
from study_companion.db import models
from study_companion.db import version_repository as repo
from study_companion.library import learning_events as ledger
from study_companion.recommendation import lesson as lesson_service

router = APIRouter(tags=["lesson"])
ENDPOINT_COMPLETE = "lesson.complete"


class CompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_version: str = Field(min_length=1, max_length=128)
    content_version: str = Field(min_length=1, max_length=128)


def _activity_of(
    db: DbSession, plan_version: str, activity_id: str
) -> tuple[models.Version, models.PlanActivity]:
    version = db.get(models.Version, plan_version)
    if version is None or version.kind != "plan" or version.payload_json is None:
        raise DomainError("NOT_FOUND", "学习方案版本不存在")
    activity = db.get(models.PlanActivity, (plan_version, activity_id))
    if activity is None:
        raise DomainError("NOT_FOUND", "该方案版本中没有此活动")
    return version, activity


@router.get("/runs/{run_id}/students/{student_id}/activities/{activity_id}/micro-lesson")
def get_micro_lesson(
    run_id: str,
    student_id: str,
    activity_id: str,
    db: DbSession,
    scope: Scope,
    plan_version: str = Query(min_length=1, max_length=128),
) -> dict:
    run = db.get(models.AnalysisRun, run_id)
    if run is None:
        raise DomainError("NOT_FOUND", "分析运行不存在")
    dataset = db.get(models.Dataset, run.dataset_id)
    if dataset is None:
        raise DomainError("NOT_FOUND", "数据集不存在")
    if scope.role == "student" and scope.student_id != student_id:
        raise DomainError("FORBIDDEN", "学生会话只能访问本人微课堂")
    version, activity = _activity_of(db, plan_version, activity_id)
    if version.target_key != student_id:
        raise DomainError("FORBIDDEN", "该方案不属于此学生")
    if activity.kind != "micro_lesson":
        raise DomainError("UNPROCESSABLE", "该活动不是微课堂")

    content_version = activity.content_version
    if not content_version:
        raise DomainError("RESULT_NOT_READY", "微课堂正文版本缺失")
    lesson_version = db.get(models.Version, content_version)
    if lesson_version is None or lesson_version.kind != "lesson":
        raise DomainError("RESULT_NOT_READY", "微课堂正文不可读")
    payload = dict(lesson_version.payload_json or {})
    if scope.role == "student" and payload.get("publication_status") != "published":
        raise DomainError("RESULT_NOT_READY", "该微课堂尚未正式发布")
    view = lesson_service.lesson_view(payload)
    view.update({"activity_id": activity_id, "plan_version": plan_version})
    return success(view)


@router.post("/runs/{run_id}/students/{student_id}/activities/{activity_id}/micro-lesson/complete")
def complete_micro_lesson(
    run_id: str,
    student_id: str,
    activity_id: str,
    payload: CompleteRequest,
    db: DbSession,
    scope: Scope,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=128)] = None,
) -> dict:
    run = db.get(models.AnalysisRun, run_id)
    if run is None:
        raise DomainError("NOT_FOUND", "分析运行不存在")
    dataset = db.get(models.Dataset, run.dataset_id)
    if dataset is None or not dataset.family_id:
        raise DomainError("NOT_FOUND", "数据集不存在")
    if scope.role == "student" and scope.student_id != student_id:
        raise DomainError("FORBIDDEN", "学生会话只能完成本人活动")
    version, activity = _activity_of(db, payload.plan_version, activity_id)
    if version.target_key != student_id:
        raise DomainError("FORBIDDEN", "该方案不属于此学生")
    if activity.kind != "micro_lesson":
        raise DomainError("UNPROCESSABLE", "该活动不是微课堂")
    if activity.content_version != payload.content_version:
        raise DomainError("REVISION_CONFLICT", "微课堂正文版本已变化，请重新读取")
    student_uid = ledger.student_uid_for(db, dataset.family_id, student_id)
    state = db.get(models.ActivityStateRow, activity_id)
    if state is None or state.student_uid != student_uid:
        raise DomainError("FORBIDDEN", "当前学生没有该活动的学习记录")

    event = ledger.append(
        db,
        family_id=dataset.family_id,
        dataset_id=dataset.id,
        run_id=repo.active_run_id(db, dataset.id, version.node_key),
        student_uid=student_uid,
        node_id=version.node_key,
        plan_version=payload.plan_version,
        group_id=activity.group_id,
        activity_id=activity_id,
        event_type="micro_lesson_completed",
        source="student",
        payload={"content_id": (activity.content_version or ""), "content_version": payload.content_version},
        occurred_at=ledger.now_iso(),
        dedupe={"content_version": payload.content_version},
    )
    if state.state != "completed":
        state.state = "completed"
        state.generation += 1
        state.updated_at = ledger.now_iso()
        ledger.append(
            db,
            family_id=dataset.family_id,
            dataset_id=dataset.id,
            run_id=repo.active_run_id(db, dataset.id, version.node_key),
            student_uid=student_uid,
            node_id=version.node_key,
            plan_version=payload.plan_version,
            group_id=activity.group_id,
            activity_id=activity_id,
            event_type="activity_completed",
            source="system",
            payload={"completion_status": "completed", "attempt_id": None},
            occurred_at=ledger.now_iso(),
        )
        ledger.finalize_group_completion(
            db,
            dataset=dataset,
            plan_version=payload.plan_version,
            node_id=version.node_key,
            group_id=activity.group_id,
            student_uid=student_uid,
            student_id=student_id,
            run_id=repo.active_run_id(db, dataset.id, version.node_key),
        )
        from study_companion.jobs.worker import ensure_worker_started

        ensure_worker_started()
    db.flush()
    return success({"event_id": event.event_id, "activity_id": activity_id, "state": "completed"})
