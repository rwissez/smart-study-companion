"""本地模拟身份会话与作用域解析。

这是比赛用的本地身份入口，不是生产认证：它的作用是让服务端真正按角色与
数据范围裁剪，并且能测试。明文 token 只返回一次，库里只存 SHA-256。

两个独立维度（01 §10.4）：
* 身份：由 session 绑定角色、数据集、授权班级、本人；
* 数据来源：由前端 ``VITE_DATA_SOURCE`` 与后端显式 demo 数据隔离决定，
  与身份选择无关。
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from study_companion.contracts import DomainError
from study_companion.db import models

Role = Literal["teacher", "student"]

SESSION_TTL = timedelta(hours=12)

BOOTSTRAP_CAPABILITIES = ("list_datasets", "import_dataset", "select_dataset", "model_settings")

TEACHER_CAPABILITIES = (
    "list_datasets",
    "import_dataset",
    "select_dataset",
    "view_class_analysis",
    "view_student_analysis",
    "view_curriculum",
    "view_quality",
    "review_knowledge",
    "edit_recommendations",
    "export_run",
    "chat_teacher",
    "manage_jobs",
    "view_learning_events",
    "resolve_intervention",
    "revise_grading",
    "model_settings",
)

STUDENT_CAPABILITIES = (
    "view_own_analysis",
    "view_own_plan",
    "chat_own",
    "submit_attempt",
    "view_own_events",
)


@dataclass(frozen=True)
class SessionScope:
    session_id: str
    role: Role
    dataset_id: str | None
    student_id: str | None
    capabilities: tuple[str, ...] = field(default=())
    dataset_family_id: str | None = None
    allowed_class_ids: tuple[str, ...] = field(default=())

    @property
    def is_bootstrap(self) -> bool:
        return self.role == "teacher" and self.dataset_id is None

    # -- 能力 ------------------------------------------------------------- #
    def require(self, capability: str) -> None:
        if capability not in self.capabilities:
            raise DomainError("FORBIDDEN", "当前会话没有执行该操作的权限")

    def has(self, capability: str) -> bool:
        return capability in self.capabilities

    # -- 数据范围 --------------------------------------------------------- #
    def require_dataset(self, dataset_id: str) -> None:
        if self.dataset_id is None:
            raise DomainError("FORBIDDEN", "当前会话尚未绑定数据集")
        if self.dataset_id != dataset_id:
            raise DomainError("FORBIDDEN", "当前会话无权访问该数据集")

    def require_student(self, student_id: str) -> None:
        if self.role == "student" and self.student_id != student_id:
            raise DomainError("FORBIDDEN", "学生会话只能访问本人数据")

    def require_class(self, class_id: str) -> None:
        if self.role == "student":
            raise DomainError("FORBIDDEN", "学生会话不能访问班级汇总")
        if self.allowed_class_ids and class_id not in self.allowed_class_ids:
            raise DomainError("FORBIDDEN", "当前会话未获授权该班级")

    def can_read_class(self, class_id: str) -> bool:
        if self.role == "student":
            return False
        return not self.allowed_class_ids or class_id in self.allowed_class_ids

    def require_teacher(self) -> None:
        if self.role != "teacher":
            raise DomainError("FORBIDDEN", "该操作仅教师可用")

    def describe(self) -> dict:
        return {
            "session_id": self.session_id,
            "role": self.role,
            "dataset_id": self.dataset_id,
            "dataset_family_id": self.dataset_family_id,
            "student_id": self.student_id,
            "allowed_class_ids": list(self.allowed_class_ids),
            "bootstrap": self.is_bootstrap,
            "capabilities": list(self.capabilities),
        }


def _now() -> datetime:
    return datetime.now(UTC)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def capabilities_for(role: Role, dataset_id: str | None) -> tuple[str, ...]:
    if role == "student":
        return STUDENT_CAPABILITIES
    return BOOTSTRAP_CAPABILITIES if dataset_id is None else TEACHER_CAPABILITIES


def _class_ids_of(db: Session, dataset_id: str) -> tuple[str, ...]:
    rows = db.execute(
        select(models.Student.class_id)
        .where(models.Student.dataset_id == dataset_id)
        .distinct()
        .order_by(models.Student.class_id)
    ).scalars().all()
    return tuple(rows)


def create_session(
    session: Session,
    role: Role,
    dataset_id: str | None,
    student_id: str | None,
    class_ids: list[str] | None = None,
) -> tuple[str, SessionScope, datetime]:
    if role == "student":
        if not dataset_id or not student_id:
            raise DomainError("VALIDATION_ERROR", "学生会话必须提供 dataset_id 与 student_id")
        if class_ids:
            raise DomainError("VALIDATION_ERROR", "学生会话不接受班级选择")
    if role == "teacher" and student_id:
        raise DomainError("VALIDATION_ERROR", "教师会话不接受 student_id")

    family_id: str | None = None
    allowed: tuple[str, ...] = ()
    if dataset_id is not None:
        dataset = session.get(models.Dataset, dataset_id)
        if dataset is None:
            raise DomainError("NOT_FOUND", "数据集不存在")
        family_id = dataset.family_id

    if role == "student":
        member = session.get(models.Student, (dataset_id, student_id))
        if member is None:
            # Same response as a missing dataset: do not disclose roster contents.
            raise DomainError("NOT_FOUND", "该数据集中不存在此学生")
    elif dataset_id is not None:
        roster_classes = _class_ids_of(session, dataset_id)
        if class_ids:
            unknown = [item for item in class_ids if item not in roster_classes]
            if unknown:
                # 不把浏览器给的列表当授权：只接受该数据集真实存在的班级。
                raise DomainError("FORBIDDEN", "所选班级不在该数据集名册中")
            allowed = tuple(sorted(set(class_ids)))
        else:
            allowed = roster_classes

    token = secrets.token_urlsafe(32)
    expires_at = _now() + SESSION_TTL
    scope_payload = {
        "allowed_class_ids": list(allowed),
        "dataset_family_id": family_id,
    }
    row = models.DemoSession(
        id=f"sess-{uuid.uuid4().hex[:16]}",
        token_hash=hash_token(token),
        role=role,
        dataset_id=dataset_id,
        student_id=student_id,
        expires_at=expires_at.isoformat(),
        scope_json=scope_payload,
        mode="demo",
    )
    session.add(row)
    session.flush()

    scope = SessionScope(
        session_id=row.id,
        role=role,
        dataset_id=dataset_id,
        student_id=student_id,
        capabilities=capabilities_for(role, dataset_id),
        dataset_family_id=family_id,
        allowed_class_ids=allowed,
    )
    return token, scope, expires_at


def resolve_session(session: Session, token: str) -> SessionScope:
    row = session.scalar(
        select(models.DemoSession).where(models.DemoSession.token_hash == hash_token(token))
    )
    if row is None:
        raise DomainError("FORBIDDEN", "会话无效或已失效")
    if datetime.fromisoformat(row.expires_at) <= _now():
        raise DomainError("FORBIDDEN", "会话已过期，请重新选择身份")

    role: Role = "student" if row.role == "student" else "teacher"
    scope_payload = row.scope_json or {}
    family_id = scope_payload.get("dataset_family_id")
    if family_id is None and row.dataset_id:
        dataset = session.get(models.Dataset, row.dataset_id)
        family_id = dataset.family_id if dataset else None
    allowed = tuple(scope_payload.get("allowed_class_ids") or ())
    if role == "teacher" and row.dataset_id and not allowed:
        # 旧会话没有班级快照时按当前名册补齐，仍由服务端计算。
        allowed = _class_ids_of(session, row.dataset_id)
    return SessionScope(
        session_id=row.id,
        role=role,
        dataset_id=row.dataset_id,
        student_id=row.student_id,
        capabilities=capabilities_for(role, row.dataset_id),
        dataset_family_id=family_id,
        allowed_class_ids=allowed,
    )
