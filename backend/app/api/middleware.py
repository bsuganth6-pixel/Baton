"""Request context: request ids, a body-size cap, security headers, an access log, and logging setup."""
from __future__ import annotations

import logging
import re
import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.context import request_id_var
from app.api.errors import ApiError, error_response, unexpected_error_response

access_log = logging.getLogger("baton.access")
error_log = logging.getLogger("baton.errors")
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


_handler = logging.StreamHandler()
_handler.addFilter(_RequestIdFilter())
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"))


def configure_logging(level: str) -> None:
    logger = logging.getLogger("baton")
    logger.setLevel(level)
    if _handler not in logger.handlers:
        logger.addHandler(_handler)


def _too_large() -> Response:
    return error_response(413, "payload_too_large", "The request is too large.")


class RequestContextMiddleware:
    """Pure ASGI, so it can cap request bodies as they stream in and stamp every response."""

    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = Headers(scope=scope)
        supplied = incoming.get("x-request-id", "")
        request_id = supplied if _VALID_REQUEST_ID.fullmatch(supplied) else uuid.uuid4().hex[:16]
        token = request_id_var.set(request_id)
        started, status, responded, received = time.perf_counter(), 500, False, 0

        async def stamped_send(message: Message) -> None:
            nonlocal status, responded
            if message["type"] == "http.response.start":
                responded, status = True, message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Cache-Control"] = "no-store"
            await send(message)

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise ApiError(413, "payload_too_large", "The request is too large.")
            return message

        declared = incoming.get("content-length", "")
        try:
            if declared.isdigit() and int(declared) > self.max_body_bytes:
                await _too_large()(scope, receive, stamped_send)
            else:
                await self.app(scope, counting_receive, stamped_send)
        except Exception:  # the last line of defence: log everything, show nothing
            error_log.error("Unhandled error on %s %s", scope["method"], scope["path"], exc_info=True)
            if responded:
                raise
            await unexpected_error_response()(scope, receive, stamped_send)
        finally:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            access_log.info("%s %s -> %s in %dms", scope["method"], scope["path"], status, elapsed_ms)
            request_id_var.reset(token)
