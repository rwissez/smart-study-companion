"""统一证据解析接口。"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy.sql import text  # noqa: F401  (保留显式导入，供后续分页查询复用)

from study_companion.api.deps import DbSession, Scope
from study_companion.api.envelope import success
from study_companion.contracts import DomainError, EvidenceQuery, EvidenceRef
from study_companion.db import evidence_repository

router = APIRouter(tags=["evidence"])


@router.post("/evidence/resolve")
def resolve_evidence(payload: EvidenceQuery, db: DbSession, scope: Scope) -> dict:
    detail = evidence_repository.resolve(db, payload.ref, payload.context, scope)
    return success(detail.model_dump(mode="json"))


@router.get("/runs/{run_id}/evidence/{kind}/{evidence_id}")
def resolve_run_evidence(
    run_id: str,
    kind: str,
    evidence_id: str,
    db: DbSession,
    scope: Scope,
    catalog_version: str | None = None,
) -> dict:
    """旧 D 兼容入口：内部同样走统一 resolver，不自建另一套授权。"""

    try:
        ref = EvidenceRef(
            kind=kind,  # type: ignore[arg-type]
            id=evidence_id,
            run_id=run_id,
            catalog_version=catalog_version,
        )
    except Exception as exc:  # pydantic ValidationError
        raise DomainError("VALIDATION_ERROR", "证据引用定位不完整") from exc
    detail = evidence_repository.resolve(db, ref, None, scope)
    return success(detail.model_dump(mode="json"))
