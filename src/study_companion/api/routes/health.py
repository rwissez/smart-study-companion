"""Health probe: liveness plus the facts the UI needs before bootstrapping."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from study_companion import __version__
from study_companion.api.deps import AppSettings, DbSession
from study_companion.api.envelope import SCHEMA_VERSION, success

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: DbSession, settings: AppSettings) -> dict:
    database_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # pragma: no cover - only reachable on a broken database
        database_ok = False

    return success(
        {
            "status": "ok" if database_ok else "degraded",
            "app_version": __version__,
            "schema_version": SCHEMA_VERSION,
            "database_ok": database_ok,
            "demo_mode": settings.demo_mode,
            "model_configured": settings.model_configured,
        }
    )
