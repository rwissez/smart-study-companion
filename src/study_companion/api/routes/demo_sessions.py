"""Demo identity selection and dataset listing.

Teachers may bootstrap without a dataset (list / import / select only);
students must bind to an existing dataset and to themselves.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from study_companion.api.deps import AppSettings, DbSession, Scope
from study_companion.api.envelope import success
from study_companion.api.sessions import create_session
from study_companion.contracts import DomainError
from study_companion.db import models

router = APIRouter(tags=["demo"])


class DemoSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["teacher", "student"]
    dataset_id: str | None = Field(default=None, max_length=64)
    student_id: str | None = Field(default=None, max_length=64)


def _require_demo_mode(settings: AppSettings) -> None:
    if not settings.demo_mode:
        # 404 rather than 403: do not advertise the demo endpoint when it is off.
        raise DomainError("NOT_FOUND", "接口不存在")


@router.post("/demo-sessions", status_code=status.HTTP_201_CREATED)
def create_demo_session(
    payload: DemoSessionRequest, db: DbSession, settings: AppSettings, response: Response
) -> dict:
    _require_demo_mode(settings)
    token, scope, expires_at = create_session(
        db, payload.role, payload.dataset_id, payload.student_id
    )
    response.status_code = status.HTTP_201_CREATED
    return success(
        {
            "session_token": token,
            "expires_at": expires_at.isoformat(),
            "scope": {
                "session_id": scope.session_id,
                "role": scope.role,
                "dataset_id": scope.dataset_id,
                "student_id": scope.student_id,
                "bootstrap": scope.is_bootstrap,
                "capabilities": list(scope.capabilities),
            },
        }
    )


@router.get("/datasets")
def list_datasets(db: DbSession, scope: Scope) -> dict:
    scope.require("list_datasets")
    rows = db.scalars(select(models.Dataset).order_by(models.Dataset.created_at.desc())).all()
    counts = dict(
        db.execute(
            select(models.Student.dataset_id, func.count())
            .group_by(models.Student.dataset_id)
        ).all()
    )
    items = [
        {
            "dataset_id": row.id,
            "status": row.status,
            "created_at": row.created_at,
            "parent_id": row.parent_id,
            "synthetic": bool(row.manifest_json.get("synthetic", False)),
            "student_count": counts.get(row.id, 0),
            "table_row_counts": row.manifest_json.get("table_row_counts", {}),
        }
        for row in rows
    ]
    return success({"items": items, "total": len(items)})


@router.get("/datasets/{dataset_id}/runs")
def list_dataset_runs(dataset_id: str, db: DbSession, scope: Scope) -> dict:
    scope.require("list_datasets")
    if scope.dataset_id is not None:
        scope.require_dataset(dataset_id)
    if db.get(models.Dataset, dataset_id) is None:
        raise DomainError("NOT_FOUND", "数据集不存在")

    rows = db.scalars(
        select(models.AnalysisRun)
        .where(models.AnalysisRun.dataset_id == dataset_id)
        .order_by(models.AnalysisRun.created_at.desc())
    ).all()
    items = [
        {
            "run_id": row.id,
            "dataset_id": row.dataset_id,
            "catalog_version": row.catalog_version,
            "rule_version": row.rule_version,
            "status": row.status,
            "created_at": row.created_at,
        }
        for row in rows
    ]
    return success({"items": items, "total": len(items)})
