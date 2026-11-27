"""唯一学习事件账本（01 §7.1）。

* 事件只追加，不改写；``business_key`` 保证同一业务事实只记一次；
* ``sequence`` 在 (plan_version, group_id, activity_id, student) 作用域内自增；
* ``offset`` 是全局单调高水位，B 的 ``event_cutoff`` 用它；
* 完成、帮助、评分等状态由服务端推导，客户端不能自行申报。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from study_companion.contracts import DomainError, LearningEvent
from study_companion.db import models


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:20]}"


def canonical(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def business_key(
    event_type: str, student_uid: str, activity_id: str | None, extra: dict[str, Any] | None = None
) -> str:
    return hashlib.sha256(
        canonical(
            {"event_type": event_type, "student": student_uid, "activity": activity_id, "extra": extra or {}}
        ).encode("utf-8")
    ).hexdigest()


def current_offset(db: Session, family_id: str, student_uid: str | None = None) -> int:
    query = select(func.max(models.LearningEventRow.offset)).where(
        models.LearningEventRow.family_id == family_id
    )
    if student_uid:
        query = query.where(models.LearningEventRow.student_uid == student_uid)
    return int(db.scalar(query) or 0)


def append(
    db: Session,
    *,
    family_id: str,
    dataset_id: str,
    run_id: str | None,
    student_uid: str,
    node_id: str,
    event_type: str,
    source: str,
    payload: dict[str, Any],
    occurred_at: str,
    plan_version: str | None = None,
    group_id: str | None = None,
    activity_id: str | None = None,
    material_id: str | None = None,
    attempt_id: str | None = None,
    dedupe: dict[str, Any] | None = None,
) -> models.LearningEventRow:
    key = business_key(event_type, student_uid, activity_id, dedupe)
    existing = db.scalar(
        select(models.LearningEventRow).where(models.LearningEventRow.business_key == key)
    )
    if existing is not None:
        return existing

    scope_key = f"{plan_version or ''}|{group_id or ''}|{activity_id or ''}|{student_uid}"
    last = db.scalar(
        select(func.max(models.LearningEventRow.sequence)).where(
            models.LearningEventRow.scope_key == scope_key
        )
    )
    row = models.LearningEventRow(
        event_id=new_id("evt"),
        family_id=family_id,
        dataset_id=dataset_id,
        run_id=run_id,
        student_uid=student_uid,
        node_id=node_id,
        plan_version=plan_version,
        group_id=group_id,
        activity_id=activity_id,
        material_id=material_id,
        attempt_id=attempt_id,
        scope_key=scope_key,
        sequence=int(last or 0) + 1,
        event_type=event_type,
        source=source,
        payload_json=payload,
        occurred_at=occurred_at,
        business_key=key,
    )
    db.add(row)
    db.flush()
    return row


def to_contract(db: Session, row: models.LearningEventRow) -> LearningEvent:
    identity = db.get(models.StudentIdentity, row.student_uid)
    return LearningEvent(
        event_id=row.event_id,
        dataset_family_id=row.family_id,
        dataset_id=row.dataset_id,
        run_id=row.run_id or "",
        student_id=identity.external_id if identity else "",
        node_id=row.node_id,
        plan_version=row.plan_version,
        group_id=row.group_id,
        activity_id=row.activity_id,
        material_id=row.material_id,
        attempt_id=row.attempt_id,
        sequence=row.sequence,
        occurred_at=row.occurred_at,
        source=row.source,  # type: ignore[arg-type]
        event_type=row.event_type,  # type: ignore[arg-type]
        payload=dict(row.payload_json or {}),
    )


def view(row: models.LearningEventRow, student_id: str) -> dict[str, Any]:
    payload = dict(row.payload_json or {})
    summary = {
        "activity_started": "开始活动",
        "study_material_completed": "完成学习材料",
        "answer_submitted": "提交作答",
        "grading_completed": "评分完成",
        "hint_requested": f"请求第 {payload.get('level')} 级提示",
        "assisted_mode_confirmed": "确认转入受助模式",
        "agent_help_used": "使用湛知帮助",
        "activity_completed": "完成活动",
        "micro_lesson_opened": "阅读微课堂",
        "micro_lesson_completed": "完成微课堂",
        "group_completed": "完成任务组",
        "teacher_review": "教师审核",
        "teacher_note": "教师备注",
        "effectiveness_assessed": "教师评估效果",
    }.get(row.event_type, row.event_type)
    return {
        "event_id": row.event_id,
        "event_type": row.event_type,
        "source": row.source,
        "occurred_at": row.occurred_at,
        "sequence": row.sequence,
        "student_id": student_id,
        "node_id": row.node_id,
        "plan_version": row.plan_version,
        "group_id": row.group_id,
        "activity_id": row.activity_id,
        "material_id": row.material_id,
        "attempt_id": row.attempt_id,
        "summary": summary,
        "payload": payload,
    }


def student_uid_for(db: Session, family_id: str, student_id: str) -> str:
    row = db.scalar(
        select(models.StudentIdentity).where(
            models.StudentIdentity.family_id == family_id,
            models.StudentIdentity.external_id == student_id,
        )
    )
    if row is None:
        raise DomainError("NOT_FOUND", "该数据集中不存在此学生")
    return row.student_uid


def group_required_activities(db: Session, plan_version: str, group_id: str) -> list[str]:
    rows = db.scalars(
        select(models.PlanActivity.activity_id).where(
            models.PlanActivity.plan_version == plan_version,
            models.PlanActivity.group_id == group_id,
        )
    ).all()
    return list(rows)


def activity_completed_ids(db: Session, student_uid: str, plan_version: str) -> set[str]:
    rows = db.scalars(
        select(models.ActivityStateRow.activity_id).where(
            models.ActivityStateRow.student_uid == student_uid,
            models.ActivityStateRow.origin_plan_version == plan_version,
            models.ActivityStateRow.state == "completed",
        )
    ).all()
    return set(rows)
