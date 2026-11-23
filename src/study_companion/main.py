"""Application factory. One process, one app, routers under ``/api/v1``."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from study_companion import __version__
from study_companion.api.errors import install_error_handlers
from study_companion.api.middleware import RequestIdMiddleware
from study_companion.api.routes import catalog, chat, datasets, demo_sessions, exports, health, recommendations, runs

API_PREFIX = "/api/v1"
DEV_ORIGINS = ["http://127.0.0.1:5173", "http://localhost:5173"]


def create_app() -> FastAPI:
    app = FastAPI(
        title="智慧学伴",
        version=__version__,
        docs_url="/api/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
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

    for module in (health, demo_sessions, datasets, runs, recommendations, catalog, chat, exports):
        app.include_router(module.router, prefix=API_PREFIX)
    return app


app = create_app()
