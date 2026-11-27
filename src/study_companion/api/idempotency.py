"""请求幂等（01 §10.2）。

键范围 ``(session_id, endpoint, key)``，同时保存规范化请求摘要：

* 同键同摘要 → 返回原回执，不重复执行业务；
* 同键不同摘要 → 409 ``IDEMPOTENCY_CONFLICT``；
* 并发同键 → 由主键唯一约束裁决，冲突后重读原记录，不会先查后写重复创建。

传输幂等之外，业务层仍有唯一键（事件、尝试、评分修订、任务高水位）兜底。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from study_companion.contracts import DomainError, canonical_json
from study_companion.db import models


def request_digest(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def find_receipt(
    db: Session, *, session_id: str, endpoint: str, key: str, digest: str
) -> dict[str, Any] | None:
    row = db.get(models.IdempotencyRecord, (session_id, endpoint, key))
    if row is None:
        return None
    if row.request_hash != digest:
        raise DomainError(
            "IDEMPOTENCY_CONFLICT", "同一个 Idempotency-Key 不能用于不同的请求内容"
        )
    return dict(row.receipt_json or {})


def store_receipt(
    db: Session,
    *,
    session_id: str,
    endpoint: str,
    key: str,
    digest: str,
    receipt: dict[str, Any],
    result_ref: str | None = None,
) -> dict[str, Any]:
    row = models.IdempotencyRecord(
        scope_session=session_id,
        endpoint=endpoint,
        idem_key=key,
        request_hash=digest,
        result_ref=result_ref,
        receipt_json=receipt,
        created_at=_now(),
    )
    # 用保存点隔离并发冲突，避免把同请求里已完成的业务写入一起回滚。
    savepoint = db.begin_nested()
    try:
        db.add(row)
        db.flush()
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        existing = db.get(models.IdempotencyRecord, (session_id, endpoint, key))
        if existing is None:
            raise
        if existing.request_hash != digest:
            raise DomainError(
                "IDEMPOTENCY_CONFLICT", "同一个 Idempotency-Key 不能用于不同的请求内容"
            )
        return dict(existing.receipt_json or {})
    return receipt


def optional_key(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > 128:
        raise DomainError("VALIDATION_ERROR", "Idempotency-Key 过长", [{"field": "Idempotency-Key", "reason": "最多 128 字符"}])
    return cleaned


def digest_of_json(payload: str | None) -> str:
    """对原始 JSON 文本取摘要；解析失败也稳定，不抛业务错误。"""

    raw = (payload or "").strip()
    try:
        return request_digest(json.loads(raw))
    except json.JSONDecodeError:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def latest_for_session(db: Session, session_id: str, endpoint: str) -> models.IdempotencyRecord | None:
    return db.scalar(
        select(models.IdempotencyRecord)
        .where(
            models.IdempotencyRecord.scope_session == session_id,
            models.IdempotencyRecord.endpoint == endpoint,
        )
        .order_by(models.IdempotencyRecord.created_at.desc())
    )
