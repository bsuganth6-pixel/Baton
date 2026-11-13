"""Password hashing and signed session tokens."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache

import bcrypt
import jwt

ALGORITHM = "HS256"
MAX_PASSWORD_BYTES = 72  # bcrypt ignores everything after 72 bytes, so longer passwords are refused outright


def hash_password(password: str, rounds: int = 12) -> str:
    raw = password.encode()
    if not raw or len(raw) > MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must be 1 to {MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds)).decode()


def verify_password(password: str, hashed: str) -> bool:
    raw = password.encode()
    if not raw or len(raw) > MAX_PASSWORD_BYTES:
        return False
    try:
        return bcrypt.checkpw(raw, hashed.encode())
    except ValueError:
        return False


@lru_cache
def _decoy_hash(rounds: int) -> str:
    return hash_password("decoy-password-for-timing", rounds)


def spend_the_same_time(password: str, rounds: int) -> None:
    """Hash once even when the account does not exist, so response time does not reveal which emails exist."""
    verify_password(password, _decoy_hash(rounds))


def create_access_token(user_id: int, key: str, minutes: int, now: datetime | None = None) -> str:
    issued = now or datetime.now(UTC)
    claims = {"sub": str(user_id), "iat": issued, "exp": issued + timedelta(minutes=minutes), "typ": "access"}
    return jwt.encode(claims, key, algorithm=ALGORITHM)


def read_access_token(token: str, key: str) -> int | None:
    """The user id inside a valid, unexpired token; None for anything else."""
    try:
        claims = jwt.decode(token, key, algorithms=[ALGORITHM], options={"require": ["exp", "iat", "sub"]})
    except jwt.PyJWTError:
        return None
    if claims.get("typ") != "access":
        return None
    try:
        return int(claims["sub"])
    except (KeyError, TypeError, ValueError):
        return None
