"""帮助披露状态机（01 §7.1、06 §5.1、05 §8）。

为什么需要它：学生"有没有被帮到"不能靠前端申报。每次实际要给提示或 Agent 帮助前，
先落一条 intent 记录；真正释放后写 confirmed 与事件；确认不了（超时、消息丢失、
模型未答）记 unknown，不能默认成 independent。

独立模式（``exposure_mode=independent``）下，释放帮助前必须有学生本人确认；
拒绝则记 not_sent，不产生任何帮助事件。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.contracts import DomainError, HelpDisclosure
from study_companion.db import models

CONFIRMATION_TTL = timedelta(minutes=10)


def _now() -> datetime:
    return datetime.now(UTC)


def _now_iso() -> str:
    return _now().isoformat(timespec="milliseconds")


def _id() -> str:
    return f"hd-{uuid.uuid4().hex[:20]}"


def protection_digest(*, material_id: str, answer: str | None, level: int) -> str:
    payload = f"{material_id}|{level}|{answer or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def next_release_order(db: Session, attempt_id: str) -> int:
    rows = db.scalars(
        select(models.HelpDisclosureRow.release_order).where(
            models.HelpDisclosureRow.attempt_id == attempt_id
        )
    ).all()
    return (max(rows) if rows else 0) + 1


def open_intent(
    db: Session,
    *,
    attempt_id: str,
    channel: str,
    message_key: str,
    protection: str,
    submission_no: int,
    reply_ref: str | None = None,
) -> models.HelpDisclosureRow:
    """落一条帮助意图；同一尝试同一消息键只保留一条。"""

    existing = db.scalar(
        select(models.HelpDisclosureRow).where(
            models.HelpDisclosureRow.attempt_id == attempt_id,
            models.HelpDisclosureRow.channel == channel,
            models.HelpDisclosureRow.message_key == message_key,
        )
    )
    if existing is not None:
        return existing
    row = models.HelpDisclosureRow(
        disclosure_id=_id(),
        attempt_id=attempt_id,
        channel=channel,
        message_key=message_key,
        release_order=next_release_order(db, attempt_id),
        submission_no=submission_no,
        protection_digest=protection,
        reply_ref=reply_ref,
        content_digest=None,
        state="intent",
        intent_at=_now_iso(),
        released_at=None,
        event_id=None,
    )
    db.add(row)
    db.flush()
    return row


def mark_confirmed(
    db: Session, row: models.HelpDisclosureRow, *, event_id: str | None, content_digest: str | None = None
) -> models.HelpDisclosureRow:
    if row.state in {"confirmed", "not_sent"}:
        return row
    row.state = "confirmed"
    row.released_at = _now_iso()
    row.event_id = event_id
    row.content_digest = content_digest
    db.flush()
    return row


def mark_unknown(db: Session, row: models.HelpDisclosureRow) -> models.HelpDisclosureRow:
    if row.state == "confirmed":
        return row
    row.state = "unknown"
    db.flush()
    return row


def mark_not_sent(db: Session, row: models.HelpDisclosureRow) -> models.HelpDisclosureRow:
    if row.state == "confirmed":
        return row
    row.state = "not_sent"
    db.flush()
    return row


def pending_for_attempt(db: Session, attempt_id: str) -> list[models.HelpDisclosureRow]:
    return list(
        db.scalars(
            select(models.HelpDisclosureRow).where(
                models.HelpDisclosureRow.attempt_id == attempt_id,
                models.HelpDisclosureRow.state == "intent",
            )
        ).all()
    )


def expired(db: Session, attempt_id: str) -> list[models.HelpDisclosureRow]:
    """超时仍未确认的意图：标记 unknown，不当作已受助也不当作独立完成。"""

    changed: list[models.HelpDisclosureRow] = []
    for row in pending_for_attempt(db, attempt_id):
        try:
            intent_at = datetime.fromisoformat(row.intent_at)
        except ValueError:
            continue
        if intent_at.tzinfo is None:
            intent_at = intent_at.replace(tzinfo=UTC)
        if _now() - intent_at > CONFIRMATION_TTL:
            mark_unknown(db, row)
            changed.append(row)
    return changed


def view(row: models.HelpDisclosureRow) -> HelpDisclosure:
    return HelpDisclosure(
        disclosure_id=row.disclosure_id,
        attempt_id=row.attempt_id,
        channel=row.channel,  # type: ignore[arg-type]
        message_key=row.message_key,
        release_order=row.release_order,
        submission_no=row.submission_no,
        protection_digest=row.protection_digest,
        content_digest=row.content_digest,
        state=row.state,  # type: ignore[arg-type]
        intent_at=row.intent_at,
        released_at=row.released_at,
        event_id=row.event_id,
        reply_ref=row.reply_ref,
    )


def requires_confirmation(activity_exposure: str | None) -> bool:
    """独立模式下，任何帮助都要学生先确认。"""

    return activity_exposure == "independent"


def summary_for_attempt(db: Session, attempt_id: str) -> dict[str, Any]:
    rows = db.scalars(
        select(models.HelpDisclosureRow).where(models.HelpDisclosureRow.attempt_id == attempt_id)
    ).all()
    return {
        "intent": sum(1 for row in rows if row.state == "intent"),
        "confirmed": sum(1 for row in rows if row.state == "confirmed"),
        "unknown": sum(1 for row in rows if row.state == "unknown"),
        "not_sent": sum(1 for row in rows if row.state == "not_sent"),
    }
