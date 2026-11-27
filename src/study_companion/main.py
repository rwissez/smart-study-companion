"""Application factory. One process, one app, routers under ``/api/v1``."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from study_companion import __version__
from study_companion.api.errors import install_error_handlers
from study_companion.api.middleware import RequestIdMiddleware
from study_companion.api.routes import (
    chat,
    datasets,
    demo_sessions,
    evidence,
    exports,
    health,
    imports,
    jobs,
    knowledge,
    learning,
    lesson,
    materials,
    model_settings,
    observations,
    plan_edit,
    profile,
)
from study_companion.config import get_settings

API_PREFIX = "/api/v1"
DEV_ORIGINS = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:4173",
    "http://localhost:4173",
]

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from study_companion import pipeline

    pipeline.register_handlers()
    settings = get_settings()
    worker = None
    if settings.jobs_worker_enabled:
        from study_companion.jobs.worker import start_worker

        worker = start_worker()
    try:
        yield
    finally:
        if worker is not None:
            from study_companion.jobs.worker import stop_worker

            stop_worker()


def create_app() -> FastAPI:
    app = FastAPI(
        title="湛知",
        version=__version__,
        docs_url="/api/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        lifespan=lifespan,
    )

    # Order matters: the last middleware added runs first on the way in, so the
    # request id is assigned before CORS responds to anything.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)

    for module in (
        health,
        demo_sessions,
        materials,
        imports,
        datasets,
        jobs,
        evidence,
        exports,
        knowledge,
        learning,
        lesson,
        model_settings,
        plan_edit,
        profile,
        chat,
        observations,
    ):
        app.include_router(module.router, prefix=API_PREFIX)
    return app


app = create_app()
