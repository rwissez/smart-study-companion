"""Shared fixtures: every test runs against its own throwaway SQLite file."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from study_companion import db as db_package
from study_companion.config import reset_settings_cache


@pytest.fixture
def app_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point settings at a temp database and reset every cached singleton."""
    from study_companion.db.engine import dispose_engine
    from study_companion.db.session import reset_sessionmaker

    sqlite_path = tmp_path / "test.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{sqlite_path.as_posix()}")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("OUTPUT_ROOT", str(tmp_path / "outputs"))
    for name in ("MODEL_PROVIDER", "MODEL_BASE_URL", "MODEL_NAME", "MODEL_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    reset_settings_cache()
    dispose_engine()
    reset_sessionmaker()

    from study_companion.db import models  # noqa: F401  (register metadata)
    from study_companion.db.engine import get_engine

    db_package.Base.metadata.create_all(get_engine())
    yield sqlite_path

    dispose_engine()
    reset_sessionmaker()
    reset_settings_cache()


@pytest.fixture
def client(app_env: Path) -> Iterator[TestClient]:
    from study_companion.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def session(app_env: Path) -> Iterator[Session]:
    from study_companion.db.session import get_sessionmaker

    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def seeded(app_env: Path) -> dict:
    from study_companion.cli import seed_demo

    return seed_demo(force=True)
