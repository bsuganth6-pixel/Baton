"""Shared dependencies: settings, database session, the signed-in user, and permission checks."""
from __future__ import annotations

from collections.abc import Callable, Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.config import Settings
from app.models import User
from app.protect.acl import Permission, has_permission
from app.protect.auth import read_access_token

SESSION_COOKIE = "baton_session"
CSRF_HEADER = "x-baton-client"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.sessionmaker() as db:
        yield db


def _not_signed_in() -> ApiError:
    return ApiError(401, "not_authenticated", "Sign in to continue.", headers={"WWW-Authenticate": "Bearer"})


def current_user(
    request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)
) -> User:
    """The signed-in user, from a bearer token or the session cookie. Cookie sessions must prove the request
    came from our own page (a custom header a cross-site form cannot send) before they may change anything."""
    header = request.headers.get("authorization", "")
    from_cookie = not header.lower().startswith("bearer ")
    token = request.cookies.get(SESSION_COOKIE) if from_cookie else header[7:].strip()
    if not token:
        raise _not_signed_in()
    user_id = read_access_token(token, settings.signing_key)
    user = db.get(User, user_id) if user_id is not None else None
    if user is None or not user.is_active:
        raise _not_signed_in()
    if from_cookie and request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != "web":
        raise ApiError(403, "csrf_header_missing", "This request must come from the Baton web app.")
    return user


def require_permission(permission: Permission) -> Callable[..., User]:
    def dependency(user: User = Depends(current_user)) -> User:
        if not has_permission(user.role, permission):
            raise ApiError(403, "forbidden", "Your role cannot do that.")
        return user

    return dependency
