"""FastAPI application factory. Run with: uvicorn app.main:create_app --factory"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import Engine
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from app import __version__
from app.api import audit as audit_api
from app.api import assistant, auth, handover, health, pipeline, questions, review, sources
from app.api.errors import install_error_handlers
from app.api.middleware import RequestContextMiddleware, configure_logging
from app.config import Settings, get_settings
from app.db import make_engine, make_sessionmaker
from app.ingest.seed import SeedError, seed_demo
from app.protect.ratelimit import LoginThrottle

BODY_OVERHEAD_BYTES = 65_536  # multipart framing around the largest allowed file
STATIC_DIR = Path(__file__).with_name("static")


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    log = logging.getLogger("baton.app")
    if not settings.secret_key:
        log.warning("SECRET_KEY is not set: using a random key, so sessions end when the server restarts")
    owned_engine = engine is None
    engine = engine or make_engine(settings.database_url)
    sessions = make_sessionmaker(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if settings.demo_mode:
            try:
                seed_demo(sessions, settings)
            except Exception as exc:  # the app must still start; /health and the log say what happened
                log.error("Demo seeding failed: %s", exc, exc_info=not isinstance(exc, SeedError))
        yield
        if owned_engine:
            engine.dispose()

    app = FastAPI(title="Baton", version=__version__, lifespan=lifespan, docs_url="/api/docs", redoc_url=None,
                  openapi_url="/api/openapi.json")
    app.state.settings = settings
    app.state.sessionmaker = sessions
    app.state.login_throttle = LoginThrottle()
    install_error_handlers(app)
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    if settings.origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=settings.origins, allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Baton-Client"],
        )
    # Added last, so it is outermost: ids, size limit and security headers cover every response.
    app.add_middleware(RequestContextMiddleware, max_body_bytes=settings.max_upload_bytes + BODY_OVERHEAD_BYTES)
    for module in (health, auth, sources, pipeline, audit_api, review, questions, handover, assistant):
        app.include_router(module.router)
    return app
