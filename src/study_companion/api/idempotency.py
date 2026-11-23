"""Validate repeat requests against their original contents."""

import hashlib
import json

from study_companion.contracts import DomainError
from study_companion.db.models import IdempotencyRecord


def fingerprint(payload: dict) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def existing_result(db, operation: str, key: str | None, digest: str):
    if not key:
        return None
    record = db.get(IdempotencyRecord, (operation, key))
    if record is None:
        return None
    if record.fingerprint != digest:
        raise DomainError("CONFLICT", "同一个请求标识不能用于不同内容")
    return record.result_json


def remember_result(
    db, operation: str, key: str | None, digest: str, result: dict
) -> None:
    if key:
        db.add(
            IdempotencyRecord(
                operation=operation, key=key, fingerprint=digest, result_json=result
            )
        )
