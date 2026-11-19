"""SQLAlchemy declarative base shared by models and Alembic."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
