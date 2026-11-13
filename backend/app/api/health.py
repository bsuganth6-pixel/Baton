"""Liveness and database connectivity. Also served at /health for Render's health check."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from app import __version__
from app.api.deps import get_db

log = logging.getLogger("baton.health")
router = APIRouter(tags=["system"])


@router.get("/health", response_model=None)
@router.get("/api/health", response_model=None)
def health(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        log.error("Health check: the database is unavailable")  # details stay out of the response
        return JSONResponse({"status": "degraded", "database": "unavailable", "version": __version__}, status_code=503)
    settings = request.app.state.settings
    return JSONResponse({"status": "ok", "database": "ok", "version": __version__,
                         "environment": settings.app_env, "demo_mode": settings.demo_mode})
