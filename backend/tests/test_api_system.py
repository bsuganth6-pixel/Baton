"""Health, error shapes, security headers, request ids, body limits and CORS."""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app.config import Settings
from app.db import make_engine
from app.main import create_app


@pytest.mark.parametrize("path", ["/health", "/api/health"])
def test_health_reports_ok_with_a_working_database(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "version": "0.2.0", "environment": "test",
                               "demo_mode": False}


def test_health_is_503_without_leaking_why_when_the_database_is_down(settings: Settings) -> None:
    dead = "postgresql+psycopg2://nobody:hunter2@127.0.0.1:1/none"
    with TestClient(create_app(settings, make_engine(dead))) as client:
        response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unavailable", "version": "0.2.0"}
    assert "nobody" not in response.text and "hunter2" not in response.text and "127.0.0.1" not in response.text


def test_unknown_routes_use_the_standard_error_shape(client: TestClient) -> None:
    response = client.get("/api/nope")
    error = response.json()["error"]
    assert response.status_code == 404 and error["code"] == "http_404" and error["request_id"]


def test_validation_errors_name_fields_but_never_echo_input(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"email": "x", "password": "hunter2hunter2-very-secret"})
    body = response.json()["error"]
    assert response.status_code == 422 and body["code"] == "validation_error"
    assert body["details"]["fields"][0]["field"] == "email"
    assert "hunter2hunter2-very-secret" not in response.text


def test_unexpected_errors_are_logged_in_full_but_shown_as_a_generic_message(
    engine: Engine, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(settings, engine)

    def boom() -> None:
        raise RuntimeError("internal detail: password=hunter2")

    app.add_api_route("/api/boom", boom)
    with TestClient(app, raise_server_exceptions=False) as client, caplog.at_level(logging.ERROR, logger="baton"):
        response = client.get("/api/boom")
    error = response.json()["error"]
    assert response.status_code == 500 and error["code"] == "internal_error" and error["request_id"]
    assert "hunter2" not in response.text and "Traceback" not in response.text
    assert any("hunter2" in (r.exc_text or "") or r.exc_info for r in caplog.records)
    assert error["request_id"] == response.headers["x-request-id"]


def test_every_response_carries_security_headers(client: TestClient) -> None:
    headers = client.get("/health").headers
    assert headers["x-content-type-options"] == "nosniff" and headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer" and headers["cache-control"] == "no-store"


def test_a_sane_request_id_is_kept_and_a_hostile_one_is_replaced(client: TestClient) -> None:
    assert client.get("/health", headers={"X-Request-ID": "trace-abc12345"}).headers["x-request-id"] == "trace-abc12345"
    replaced = client.get("/health", headers={"X-Request-ID": "bad id\twith <script>"}).headers["x-request-id"]
    assert len(replaced) == 16 and replaced.isalnum()


def test_an_oversized_declared_body_is_refused_early(client: TestClient) -> None:
    response = client.post("/api/auth/login", content=b"x" * 100_000, headers={"Content-Type": "application/json"})
    assert response.status_code == 413 and response.json()["error"]["code"] == "payload_too_large"


def test_an_oversized_streamed_body_is_refused_too(client: TestClient) -> None:
    response = client.post("/api/auth/login", content=(b"x" * 1000 for _ in range(100)),
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 413 and response.json()["error"]["code"] == "payload_too_large"


def test_cors_is_off_unless_origins_are_configured(engine: Engine, settings: Settings) -> None:
    preflight = {"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}
    with TestClient(create_app(settings, engine)) as client:
        assert "access-control-allow-origin" not in client.options("/api/auth/login", headers=preflight).headers
    allowed = settings.model_copy(update={"allowed_origins": "https://baton.example"})
    with TestClient(create_app(allowed, engine)) as client:
        good = client.options("/api/auth/login", headers={**preflight, "Origin": "https://baton.example"})
        assert good.headers["access-control-allow-origin"] == "https://baton.example"
        assert good.headers["access-control-allow-credentials"] == "true"
        assert "access-control-allow-origin" not in client.options("/api/auth/login", headers=preflight).headers
