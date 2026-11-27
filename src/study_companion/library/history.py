"""学习历史视图：把官方作答、在线尝试、评分与帮助事件整理成 C 可用的历史。

C 只关心四件事：这道题做过没有、做得怎么样、有没有拿过帮助、上次练习距今多久。
这里只做投影，不重算统计，也不改任何原始记录。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.db import models
from study_companion.recommendation.candidates import HistoryItem, LearningHistoryView


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def build_history(
    db: Session,
    *,
    dataset: models.Dataset,
    student_id: str,
    student_uid: str | None,
    prepared,
    now: datetime | None = None,
) -> LearningHistoryView:
    """官方作答 + 在线尝试/评分 + 帮助事件 → 学习历史视图。"""

    _ = now
    items: list[HistoryItem] = []

    for event in prepared.selected_events:
        if event.student_id != student_id:
            continue
        payload: dict[str, Any] = {}
        record_id = event.selected_record_id
        for record in prepared.raw_records:
            if record.record_id == record_id:
                payload = record.payload
                break
        items.append(
            HistoryItem(
                material_id=event.material_id,
                origin="official",
                status=event.status,
                occurred_at=_parse_time(payload.get("模拟提交时间")),
                assistance="unknown",
                score=event.score,
                max_score=event.max_score,
            )
        )

    if student_uid:
        attempts = db.scalars(
            select(models.AttemptRow).where(models.AttemptRow.student_uid == student_uid)
        ).all()
        grades = {
            row.grade_id: row
            for row in db.scalars(
                select(models.Grade).where(
                    models.Grade.attempt_id.in_([item.attempt_id for item in attempts])
                )
            ).all()
        }
        for attempt in attempts:
            grade = grades.get(attempt.latest_grade_id or "")
            items.append(
                HistoryItem(
                    material_id=attempt.material_id,
                    origin="online",
                    status=grade.status if grade else attempt.status,
                    occurred_at=_parse_time(attempt.submitted_at or attempt.created_at),
                    assistance=attempt.assistance_status,
                    attempt_id=attempt.attempt_id,
                    score=grade.score if grade else None,
                    max_score=grade.max_score if grade else None,
                )
            )

        helped = db.scalars(
            select(models.LearningEventRow).where(
                models.LearningEventRow.student_uid == student_uid,
                models.LearningEventRow.event_type.in_(("hint_requested", "agent_help_used")),
            )
        ).all()
        for event in helped:
            if not event.material_id:
                continue
            items.append(
                HistoryItem(
                    material_id=event.material_id,
                    origin="online",
                    status="hint",
                    occurred_at=_parse_time(event.occurred_at),
                    assistance="assisted",
                    attempt_id=event.attempt_id,
                )
            )

    return LearningHistoryView(items=items)
