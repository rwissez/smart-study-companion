"""Session factory. One short-lived synchronous Session per request or task."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session, sessionmaker

from study_companion.db.engine import get_engine

_factory: sessionmaker[Session] | None = None


def get_sessionmaker() -> sessionmaker[Session]:
    global _factory
    if _factory is None:
        _factory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _factory


def reset_sessionmaker() -> None:
    """Test hook: forget the factory so a new engine can be picked up."""
    global _factory
    _factory = None


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
