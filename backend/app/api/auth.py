"""Sign in, sign out, and who am I."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.api.deps import SESSION_COOKIE, current_user, get_db, get_settings_dep
from app.api.errors import ApiError
from app.api.schemas import DemoCredentialsOut, LoginIn, LoginOut, UserOut
from app.config import PUBLIC_DEMO_EMAIL, Settings
from app.models import User
from app.protect.acl import ROLE_PERMISSIONS, clearance_for
from app.protect.auth import create_access_token, spend_the_same_time, verify_password
from app.protect.ratelimit import LoginThrottle

router = APIRouter(prefix="/api/auth", tags=["auth"])


def user_out(user: User) -> UserOut:
    return UserOut(id=user.id, email=user.email, name=user.name, role=user.role, clearance=clearance_for(user.role),
                   permissions=sorted(p.value for p in ROLE_PERMISSIONS[user.role]))


@router.post("/login", response_model=LoginOut)
def login(
    body: LoginIn, request: Request, response: Response,
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep),
) -> LoginOut:
    email = body.email.strip().lower()
    throttle: LoginThrottle = request.app.state.login_throttle
    key = f"{request.client.host if request.client else 'unknown'}|{email}"
    if wait := throttle.retry_after(key):
        raise ApiError(429, "too_many_attempts", "Too many failed sign-in attempts. Try again in a few minutes.",
                       headers={"Retry-After": str(wait)})
    user = db.scalar(select(User).where(User.email == email))
    if user is not None and user.is_active:
        valid = verify_password(body.password, user.password_hash)
    else:
        spend_the_same_time(body.password, settings.bcrypt_rounds)
        valid = False
    if not valid or user is None:
        throttle.record_failure(key)
        raise ApiError(401, "invalid_credentials", "Incorrect email or password.")  # same answer for every cause
    throttle.reset(key)
    token = create_access_token(user.id, settings.signing_key, settings.access_token_minutes)
    seconds = settings.access_token_minutes * 60
    response.set_cookie(SESSION_COOKIE, token, max_age=seconds, httponly=True, secure=settings.cookie_secure,
                        samesite="lax", path="/")
    audit.record(db, "auth.login", actor=user)
    db.commit()
    return LoginOut(access_token=token, expires_in=seconds, user=user_out(user))


@router.post("/logout", status_code=204)
def logout(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/demo-credentials", response_model=DemoCredentialsOut)
def demo_credentials(settings: Settings = Depends(get_settings_dep)) -> DemoCredentialsOut:
    if not settings.demo_mode or not settings.demo_password:
        raise ApiError(404, "demo_unavailable", "Demo credentials are unavailable.")
    return DemoCredentialsOut(email=PUBLIC_DEMO_EMAIL, password=settings.demo_password)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> UserOut:
    return user_out(user)
