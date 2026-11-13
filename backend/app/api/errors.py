"""One error shape for every failure: {"error": {"code", "message", "request_id", "details"?}}.

Messages are written for users. Stack traces, request bodies and secrets never reach the response.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from app.api.context import request_id_var

log = logging.getLogger("baton.errors")
IGNORED_LOCATIONS = {"body", "query", "path", "form"}


class ApiError(HTTPException):
    """A deliberate, user-safe failure. Being an HTTPException means FastAPI re-raises it from anywhere,
    including while a request body is still being read."""

    def __init__(
        self, status: int, code: str, message: str, *, details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status, detail=message, headers=headers)
        self.code, self.message, self.details = code, message, details


def _body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message, "request_id": request_id_var.get()}
    if details:
        error["details"] = details
    return {"error": error}


async def _api_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=exc.status_code, headers=exc.headers)


async def _http_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    message = exc.detail if isinstance(exc.detail, str) else "The request could not be completed."
    return JSONResponse(_body(f"http_{exc.status_code}", message), status_code=exc.status_code,
                        headers=getattr(exc, "headers", None))


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    fields = [
        {"field": ".".join(str(part) for part in err["loc"] if part not in IGNORED_LOCATIONS), "message": err["msg"]}
        for err in exc.errors()  # input values are deliberately left out: they may be sensitive
    ]
    return JSONResponse(_body("validation_error", "Some fields are invalid.", {"fields": fields}), status_code=422)


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(_body(code, message), status_code=status)


def unexpected_error_response() -> JSONResponse:
    return error_response(500, "internal_error", "Something went wrong on our side. Please try again.")


async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
    log.error("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return unexpected_error_response()


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unexpected)
