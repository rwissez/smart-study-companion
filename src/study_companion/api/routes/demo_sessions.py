"""本地模拟身份入口与数据集列表。

教师可以先以 bootstrap 会话登录（只能列数据集 / 导入 / 选择数据集），
学生必须绑定已存在的成员身份。``DEMO_MODE`` 只控制这个本地身份入口，
不决定前端读真实数据还是预设数据。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Response, status
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
    class_ids: list[str] | None = Field(default=None, max_length=32)


def _require_demo_mode(settings: AppSettings) -> None:
    if not settings.demo_mode:
        # 404 rather than 403: do not advertise the demo endpoint when it is off.
        raise DomainError("NOT_FOUND", "接口不存在")


@router.post("/demo-sessions", status_code=status.HTTP_201_CREATED)
def create_demo_session(
    payload: DemoSessionRequest,
    db: DbSession,
    settings: AppSettings,
    response: Response,
) -> dict:
    _require_demo_mode(settings)
    token, scope, expires_at = create_session(
        db, payload.role, payload.dataset_id, payload.student_id, payload.class_ids
    )
    # 依赖清理阶段的提交发生在响应发出之后；身份入口拿到令牌会立刻用，必须当场落库。
    db.commit()
    response.status_code = status.HTTP_201_CREATED
    return success(
        {
            "session_token": token,
            "expires_at": expires_at.isoformat(),
            "scope": scope.describe(),
        }
    )


@router.get("/demo-sessions/roster")
def demo_roster(
    db: DbSession,
    settings: AppSettings,
    dataset_id: str = Query(min_length=1, max_length=64),
) -> dict:
    """受限本地名册：只在显式 demo 模式开放，供身份入口选择学生。

    只返回编号与班级，不返回成绩、画像或其他学生数据。
    """

    _require_demo_mode(settings)
    if db.get(models.Dataset, dataset_id) is None:
        raise DomainError("NOT_FOUND", "数据集不存在")
    rows = db.execute(
        select(models.Student.class_id, models.Student.student_id)
        .where(models.Student.dataset_id == dataset_id)
        .order_by(models.Student.class_id, models.Student.student_id)
    ).all()
    classes: dict[str, list[str]] = {}
    for class_id, student_id in rows:
        classes.setdefault(class_id, []).append(student_id)
    return success(
        {
            "dataset_id": dataset_id,
            "classes": [
                {"class_id": class_id, "student_ids": students}
                for class_id, students in classes.items()
            ],
        }
    )


@router.get("/datasets")
def list_datasets(db: DbSession, scope: Scope) -> dict:
    scope.require("list_datasets")
    rows = db.scalars(select(models.Dataset).order_by(models.Dataset.created_at.desc())).all()
    counts = dict(
        db.execute(
            select(models.Student.dataset_id, func.count()).group_by(models.Student.dataset_id)
        ).all()
    )
    class_counts = dict(
        db.execute(
            select(models.Student.dataset_id, func.count(func.distinct(models.Student.class_id)))
            .group_by(models.Student.dataset_id)
        ).all()
    )
    catalog_versions = {
        row.dataset_id: row.version
        for row in db.scalars(
            select(models.Catalog).order_by(models.Catalog.created_at)
        ).all()
    }
    items = [
        {
            "dataset_id": row.id,
            "dataset_family_id": row.family_id,
            "status": row.status,
            "state": row.state,
            "created_at": row.created_at,
            "parent_id": row.parent_id,
            "synthetic": bool(row.manifest_json.get("synthetic", False)),
            "display_name": row.display_name,
            "student_count": counts.get(row.id, 0),
            "class_count": class_counts.get(row.id, 0),
            "catalog_version": catalog_versions.get(row.id),
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
            "knowledge_version": row.knowledge_version,
            "rule_version": row.rule_version,
            "status": row.status,
            "created_at": row.created_at,
            "node_ids": list(row.node_ids_json or []),
        }
        for row in rows
    ]
    return success({"items": items, "total": len(items)})
