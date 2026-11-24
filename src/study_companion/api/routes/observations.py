"""Post-publication learning observation events.

Observations are evidence for a future analysis run; they never mutate official
AnswerEvent scores or findings in place.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from study_companion.api.deps import DbSession, Scope
from study_companion.api.envelope import success
from study_companion.api.sessions import SessionScope
from study_companion.contracts import DomainError, LearningObservation
from study_companion.db import models

router = APIRouter(tags=["observations"])


class ObservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=64)
    student_id: str = Field(min_length=1, max_length=64)
    node_id: str = Field(min_length=1, max_length=64)
    activity_id: str = Field(min_length=1, max_length=64)
    material_id: str | None = Field(default=None, max_length=64)
    event_type: str = Field(pattern="^(answer_submitted|hint_requested|activity_completed|teacher_review)$")
    sequence: int = Field(ge=1)
    hint_level: int | None = Field(default=None, ge=1, le=3)
    payload: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


def _scope_student(scope: SessionScope, student_id: str) -> None:
    if scope.role == "student":
        if scope.student_id != student_id:
            raise DomainError("NOT_FOUND", "学习对象不存在")
    elif scope.role != "teacher":
        raise DomainError("FORBIDDEN", "当前会话不能提交学习观察")


def _run(db: DbSession, scope: SessionScope, run_id: str) -> models.AnalysisRun:
    run = db.get(models.AnalysisRun, run_id)
    if run is None or scope.dataset_id != run.dataset_id or run.status != "succeeded":
        raise DomainError("NOT_FOUND", "分析运行不存在")
    return run


@router.post("/runs/{run_id}/observations", status_code=201)
def create_observation(
    run_id: str, payload: ObservationRequest, db: DbSession, scope: Scope
) -> dict[str, Any]:
    if payload.run_id != run_id:
        raise DomainError("VALIDATION_ERROR", "路径 run_id 与请求 run_id 不一致")
    _run(db, scope, run_id)
    _scope_student(scope, payload.student_id)
    student = db.get(models.Student, (scope.dataset_id, payload.student_id))
    if student is None:
        raise DomainError("NOT_FOUND", "学习对象不存在")
    node = db.get(models.Node, (scope.dataset_id, payload.node_id))
    if node is None:
        raise DomainError("NOT_FOUND", "教学节点不存在")
    existing = db.scalar(
        select(models.LearningObservation).where(
            models.LearningObservation.run_id == run_id,
            models.LearningObservation.student_id == payload.student_id,
            models.LearningObservation.activity_id == payload.activity_id,
            models.LearningObservation.sequence == payload.sequence,
        )
    )
    if existing is not None:
        raise DomainError("CONFLICT", "该活动序号已经提交")
    observation_id = f"obs-{uuid4().hex}"
    observed_at = datetime.now(timezone.utc).isoformat()
    observation = LearningObservation(
        observation_id=observation_id,
        dataset_id=scope.dataset_id,
        run_id=run_id,
        student_id=payload.student_id,
        node_id=payload.node_id,
        activity_id=payload.activity_id,
        material_id=payload.material_id,
        event_type=payload.event_type,
        sequence=payload.sequence,
        hint_level=payload.hint_level,
        payload=payload.payload,
        observed_at=observed_at,
        source="student" if scope.role == "student" else "teacher",
    )
    db.add(models.LearningObservation(
        id=observation_id,
        dataset_id=observation.dataset_id,
        run_id=observation.run_id,
        student_id=observation.student_id,
        node_id=observation.node_id,
        activity_id=observation.activity_id,
        material_id=observation.material_id,
        event_type=observation.event_type,
        sequence=observation.sequence,
        hint_level=observation.hint_level,
        payload_json=observation.payload,
        observed_at=observation.observed_at,
        source=observation.source,
    ))
    return success(observation.model_dump())


@router.get("/runs/{run_id}/observations")
def list_observations(
    run_id: str, db: DbSession, scope: Scope, student_id: str | None = None
) -> dict[str, Any]:
    _run(db, scope, run_id)
    if scope.role == "student":
        requested = scope.student_id
    else:
        requested = student_id
    if scope.role == "student" and student_id not in (None, scope.student_id):
        raise DomainError("NOT_FOUND", "学习对象不存在")
    query = select(models.LearningObservation).where(models.LearningObservation.run_id == run_id)
    if requested is not None:
        query = query.where(models.LearningObservation.student_id == requested)
    rows = db.scalars(query.order_by(models.LearningObservation.observed_at, models.LearningObservation.sequence)).all()
    items = [LearningObservation(
        observation_id=row.id, dataset_id=row.dataset_id, run_id=row.run_id,
        student_id=row.student_id, node_id=row.node_id, activity_id=row.activity_id,
        material_id=row.material_id, event_type=row.event_type, sequence=row.sequence,
        hint_level=row.hint_level, payload=row.payload_json, observed_at=row.observed_at,
        source=row.source,
    ).model_dump() for row in rows]
    return success({"items": items, "total": len(items)})
