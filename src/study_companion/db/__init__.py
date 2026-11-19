"""Database layer: engine, sessions and ORM tables."""

from study_companion.db.base import Base
from study_companion.db.engine import build_engine, dispose_engine, get_engine
from study_companion.db.session import get_sessionmaker, session_scope

__all__ = [
    "Base",
    "build_engine",
    "dispose_engine",
    "get_engine",
    "get_sessionmaker",
    "session_scope",
]
