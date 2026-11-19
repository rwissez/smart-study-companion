"""Demo session creation and scope resolution.

These are local demo identities, not production authentication: they exist so
that role trimming and per-student data limits are enforced server-side and can
be tested. The plaintext token is returned once and only its SHA-256 is stored.
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

BOOTSTRAP_CAPABILITIES = ("list_datasets", "import_dataset", "select_dataset")
TEACHER_CAPABILITIES = (
    "list_datasets",
    "import_dataset",
    "select_dataset",
    "view_class_analysis",
    "view_student_analysis",
    "view_curriculum",
    "view_quality",
    "edit_recommendations",
    "export_run",
)
STUDENT_CAPABILITIES = ("view_own_analysis", "view_own_plan", "chat_own")


@dataclass(frozen=True)
class SessionScope:
    session_id: str
    role: Role
    dataset_id: str | None
    student_id: str | None
    capabilities: tuple[str, ...] = field(default=())

    @property
    def is_bootstrap(self) -> bool:
        return self.role == "teacher" and self.dataset_id is None

    def require(self, capability: str) -> None:
        if capability not in self.capabilities:
            raise DomainError("FORBIDDEN", "当前会话没有执行该操作的权限")

    def require_dataset(self, dataset_id: str) -> None:
        if self.dataset_id is None:
            raise DomainError("FORBIDDEN", "当前会话尚未绑定数据集")
        if self.dataset_id != dataset_id:
            raise DomainError("FORBIDDEN", "当前会话无权访问该数据集")

    def require_student(self, student_id: str) -> None:
        if self.role == "student" and self.student_id != student_id:
            raise DomainError("FORBIDDEN", "学生会话只能访问本人数据")


def _now() -> datetime:
    return datetime.now(UTC)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def capabilities_for(role: Role, dataset_id: str | None) -> tuple[str, ...]:
    if role == "student":
        return STUDENT_CAPABILITIES
    return BOOTSTRAP_CAPABILITIES if dataset_id is None else TEACHER_CAPABILITIES


def create_session(
    session: Session, role: Role, dataset_id: str | None, student_id: str | None
) -> tuple[str, SessionScope, datetime]:
    if role == "student":
        if not dataset_id or not student_id:
            raise DomainError("VALIDATION_ERROR", "学生会话必须提供 dataset_id 与 student_id")
    if role == "teacher" and student_id:
        raise DomainError("VALIDATION_ERROR", "教师会话不接受 student_id")

    if dataset_id is not None:
        dataset = session.get(models.Dataset, dataset_id)
        if dataset is None:
            raise DomainError("NOT_FOUND", "数据集不存在")

    if role == "student":
        member = session.get(models.Student, (dataset_id, student_id))
        if member is None:
            # Same response as a missing dataset: do not disclose roster contents.
            raise DomainError("NOT_FOUND", "该数据集中不存在此学生")

    token = secrets.token_urlsafe(32)
    expires_at = _now() + SESSION_TTL
    row = models.DemoSession(
        id=f"sess-{uuid.uuid4().hex[:16]}",
        token_hash=hash_token(token),
        role=role,
        dataset_id=dataset_id,
        student_id=student_id,
        expires_at=expires_at.isoformat(),
    )
    session.add(row)
    session.flush()

    scope = SessionScope(
        session_id=row.id,
        role=role,
        dataset_id=dataset_id,
        student_id=student_id,
        capabilities=capabilities_for(role, dataset_id),
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
    return SessionScope(
        session_id=row.id,
        role=role,
        dataset_id=row.dataset_id,
        student_id=row.student_id,
        capabilities=capabilities_for(role, row.dataset_id),
    )
