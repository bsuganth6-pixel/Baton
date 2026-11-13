"""Signing in and out, session tokens, cookie safety and the login throttle."""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models import AuditLog, Role, User
from app.protect.auth import create_access_token

NOTE = {"file": ("n.md", b"# T\n\nbody text", "text/markdown")}


def login(client: TestClient, role: Role, password: str | None = None) -> object:
    return client.post("/api/auth/login", json={"email": f"{role.value}@test.example",
                                                "password": password or f"pw-{role.value}"})


def test_login_returns_a_token_the_user_and_what_the_role_may_do(client: TestClient, users: dict[Role, User]) -> None:
    response = login(client, Role.LEAVER)  # type: ignore[assignment]
    body = response.json()  # type: ignore[attr-defined]
    assert response.status_code == 200  # type: ignore[attr-defined]
    assert body["token_type"] == "bearer" and body["expires_in"] == 480 * 60
    assert body["user"] == {"id": users[Role.LEAVER].id, "email": "leaver@test.example", "name": "Leaver",
                            "role": "leaver", "clearance": 2,
                            "permissions": ["answer_questions", "review_claims", "upload_sources"]}
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.json() == body["user"]


def test_the_session_cookie_is_httponly_samesite_and_secure_when_configured(
    engine: Engine, settings: Settings, users: dict[Role, User]
) -> None:
    secure = settings.model_copy(update={"cookie_secure": True})
    with TestClient(create_app(secure, engine)) as client:
        header = login(client, Role.MANAGER).headers["set-cookie"].lower()  # type: ignore[attr-defined]
    assert header.startswith("baton_session=")
    assert all(flag in header for flag in ("httponly", "samesite=lax", "path=/", "secure", f"max-age={480 * 60}"))
    with TestClient(create_app(settings, engine)) as client:
        assert "secure" not in login(client, Role.MANAGER).headers["set-cookie"].lower()  # type: ignore[attr-defined]


def test_a_cookie_session_can_read_but_needs_the_csrf_header_to_change_things(
    client: TestClient, users: dict[Role, User]
) -> None:
    login(client, Role.MANAGER)
    assert client.get("/api/auth/me").status_code == 200
    refused = client.post("/api/sources", files=NOTE, data={"clearance": "1"})
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "csrf_header_missing"
    allowed = client.post("/api/sources", files=NOTE, data={"clearance": "1"}, headers={"X-Baton-Client": "web"})
    assert allowed.status_code == 201


def test_bearer_tokens_need_no_csrf_header(client: TestClient, bearer: Callable[[Role], dict[str, str]]) -> None:
    assert client.post("/api/sources", files=NOTE, data={"clearance": "1"}, headers=bearer(Role.MANAGER)).status_code == 201


def test_logout_clears_the_cookie(client: TestClient, users: dict[Role, User]) -> None:
    login(client, Role.MANAGER)
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_wrong_password_and_unknown_email_look_identical(client: TestClient, users: dict[Role, User]) -> None:
    wrong = login(client, Role.ADMIN, "not-the-password")
    unknown = client.post("/api/auth/login", json={"email": "nobody@test.example", "password": "pw-admin"})
    for response in (wrong, unknown):
        assert response.status_code == 401  # type: ignore[attr-defined]
    strip = lambda r: {k: v for k, v in r.json()["error"].items() if k != "request_id"}  # noqa: E731
    assert strip(wrong) == strip(unknown) == {"code": "invalid_credentials", "message": "Incorrect email or password."}


def test_email_is_case_and_space_insensitive_but_the_password_is_not(client: TestClient, users: dict[Role, User]) -> None:
    ok = client.post("/api/auth/login", json={"email": "  ADMIN@Test.Example ", "password": "pw-admin"})
    assert ok.status_code == 200
    assert login(client, Role.ADMIN, "PW-ADMIN").status_code == 401  # type: ignore[attr-defined]
    assert login(client, Role.ADMIN, "x" * 100).status_code == 401  # type: ignore[attr-defined]  # beyond bcrypt's 72 bytes


def test_ten_failures_block_further_attempts_even_with_the_right_password(
    client: TestClient, users: dict[Role, User]
) -> None:
    for _ in range(10):
        assert login(client, Role.ADMIN, "wrong").status_code == 401  # type: ignore[attr-defined]
    blocked = login(client, Role.ADMIN)
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0  # type: ignore[attr-defined]
    assert blocked.json()["error"]["code"] == "too_many_attempts"  # type: ignore[attr-defined]
    assert login(client, Role.MANAGER).status_code == 200  # type: ignore[attr-defined]  # other accounts are unaffected


def test_a_successful_login_resets_the_count(client: TestClient, users: dict[Role, User]) -> None:
    for _ in range(9):
        login(client, Role.ADMIN, "wrong")
    assert login(client, Role.ADMIN).status_code == 200  # type: ignore[attr-defined]
    for _ in range(9):
        login(client, Role.ADMIN, "wrong")
    assert login(client, Role.ADMIN).status_code == 200  # type: ignore[attr-defined]


def test_login_is_audited_and_failures_record_nothing_about_who_tried(
    client: TestClient, users: dict[Role, User], engine: Engine
) -> None:
    login(client, Role.ADMIN, "wrong")
    login(client, Role.ADMIN)
    with Session(engine) as db:
        entries = db.scalars(select(AuditLog)).all()
    assert [(e.action, e.actor_label) for e in entries] == [("auth.login", "admin@test.example")]


# --- tokens ---------------------------------------------------------------------------------------------

def bearer_of(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_missing_and_malformed_credentials_are_401_with_a_challenge(client: TestClient) -> None:
    for headers in ({}, {"Authorization": "Bearer"}, {"Authorization": "Bearer abc"}, {"Authorization": "Basic YTpi"}):
        response = client.get("/api/auth/me", headers=headers)
        assert response.status_code == 401 and response.headers["www-authenticate"] == "Bearer"
        assert response.json()["error"]["code"] == "not_authenticated"


def test_expired_tampered_wrongly_signed_and_unsigned_tokens_are_refused(
    client: TestClient, users: dict[Role, User], settings: Settings
) -> None:
    uid, key = users[Role.ADMIN].id, settings.signing_key
    now = datetime.now(UTC)
    good = create_access_token(uid, key, 5)
    assert client.get("/api/auth/me", headers=bearer_of(good)).status_code == 200
    refused = [
        create_access_token(uid, key, 5, now=now - timedelta(hours=2)),
        good[:-2] + ("AA" if not good.endswith("AA") else "BB"),
        create_access_token(uid, "another-key-" + "z" * 30, 5),
        jwt.encode({"sub": str(uid), "iat": now, "exp": now + timedelta(hours=1), "typ": "access"}, None, algorithm="none"),
        create_access_token(987654, key, 5),  # a user that does not exist
    ]
    for token in refused:
        assert client.get("/api/auth/me", headers=bearer_of(token)).status_code == 401


def test_a_deactivated_user_loses_access_at_once_and_cannot_sign_in(
    client: TestClient, users: dict[Role, User], bearer: Callable[[Role], dict[str, str]], engine: Engine
) -> None:
    assert client.get("/api/auth/me", headers=bearer(Role.TEAMMATE)).status_code == 200
    with Session(engine) as db:
        db.get(User, users[Role.TEAMMATE].id).is_active = False  # type: ignore[union-attr]
        db.commit()
    assert client.get("/api/auth/me", headers=bearer(Role.TEAMMATE)).status_code == 401
    assert login(client, Role.TEAMMATE).status_code == 401  # type: ignore[attr-defined]


def test_every_data_route_demands_a_login(client: TestClient) -> None:
    for method, path in [("get", "/api/sources"), ("get", "/api/sources/1"), ("get", "/api/sources/1/chunks"),
                         ("get", "/api/search?q=abc"), ("post", "/api/sources"), ("get", "/api/auth/me")]:
        assert getattr(client, method)(path).status_code == 401, path


@pytest.mark.parametrize("path", ["/api/docs", "/api/openapi.json"])
def test_api_docs_are_served(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 200
