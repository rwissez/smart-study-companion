"""FastAPI dependencies: database session, settings and demo scope."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from study_companion.api.sessions import SessionScope, resolve_session
from study_companion.config import Settings, get_settings
from study_companion.contracts import DomainError
from study_companion.db.session import get_sessionmaker


def get_db() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_app_settings() -> Settings:
    return get_settings()


DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]


def get_scope(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> SessionScope:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise DomainError("FORBIDDEN", "缺少会话令牌")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise DomainError("FORBIDDEN", "缺少会话令牌")
    return resolve_session(db, token)


Scope = Annotated[SessionScope, Depends(get_scope)]
