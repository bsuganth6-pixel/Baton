"""Roles, passwords, session tokens, the login throttle and the audit helper."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy.orm import Session

from app.audit import MAX_DETAIL_TEXT, record
from app.models import Role, User
from app.protect.acl import (
    ROLE_CLEARANCE,
    ROLE_PERMISSIONS,
    Permission,
    clearance_for,
    has_permission,
    within_clearance,
)
from app.protect.auth import create_access_token, hash_password, read_access_token, verify_password
from app.protect.ratelimit import MAX_TRACKED_KEYS, LoginThrottle

KEY = "k" * 40


def holders(permission: Permission) -> set[Role]:
    return {role for role in Role if has_permission(role, permission)}


# --- roles ---------------------------------------------------------------------------------------------

def test_every_role_has_a_clearance_and_a_permission_set() -> None:
    assert set(ROLE_CLEARANCE) == set(Role) == set(ROLE_PERMISSIONS)


@pytest.mark.parametrize(
    ("role", "level"),
    [(Role.ADMIN, 3), (Role.MANAGER, 3), (Role.LEAVER, 2), (Role.SUCCESSOR, 2), (Role.TEAMMATE, 1)],
)
def test_clearance_follows_the_role(role: Role, level: int) -> None:
    assert clearance_for(role) == level


def test_permission_matrix() -> None:
    everyone = set(Role)
    assert holders(Permission.UPLOAD_SOURCES) == {Role.ADMIN, Role.MANAGER, Role.LEAVER}
    assert holders(Permission.REVIEW_CLAIMS) == {Role.ADMIN, Role.MANAGER, Role.LEAVER}
    assert holders(Permission.ANSWER_QUESTIONS) == {Role.ADMIN, Role.MANAGER, Role.LEAVER}
    assert holders(Permission.RUN_PIPELINE) == {Role.ADMIN, Role.MANAGER}
    assert holders(Permission.VIEW_AUDIT) == {Role.ADMIN, Role.MANAGER}
    assert holders(Permission.ACKNOWLEDGE) == everyone - {Role.LEAVER}


def test_successors_and_teammates_can_read_and_acknowledge_but_change_nothing() -> None:
    for role in (Role.SUCCESSOR, Role.TEAMMATE):
        assert ROLE_PERMISSIONS[role] == {Permission.ACKNOWLEDGE}


@pytest.mark.parametrize(
    ("levels", "viewer", "visible"),
    [([1], 1, True), ([1, 2], 2, True), ([1, 3], 2, False), ([3], 3, True), ([0], 0, True), ([1], 0, False),
     ([], 3, False)],
)
def test_a_viewer_needs_clearance_for_every_source_and_no_sources_fails_closed(
    levels: list[int], viewer: int, visible: bool
) -> None:
    assert within_clearance(levels, viewer) is visible
    assert within_clearance(iter(levels), viewer) is visible


# --- passwords and tokens ------------------------------------------------------------------------------

def test_passwords_are_salted_and_verified() -> None:
    hashed = hash_password("correct horse", 4)
    assert verify_password("correct horse", hashed) and not verify_password("Correct horse", hashed)
    assert hashed != hash_password("correct horse", 4) and "correct horse" not in hashed


def test_bcrypt_ignores_everything_past_72_bytes_so_longer_passwords_are_refused() -> None:
    with pytest.raises(ValueError):
        hash_password("x" * 73, 4)
    with pytest.raises(ValueError):
        hash_password("", 4)
    assert verify_password("é" * 36, hash_password("é" * 36, 4))  # 72 bytes exactly
    assert not verify_password("x" * 73, hash_password("x" * 72, 4))
    assert not verify_password("", hash_password("x", 4))


def test_a_garbage_hash_never_verifies() -> None:
    assert not verify_password("anything", "not-a-bcrypt-hash")


def test_token_round_trip() -> None:
    assert read_access_token(create_access_token(42, KEY, 5), KEY) == 42


def test_expired_tampered_and_wrongly_signed_tokens_are_refused() -> None:
    expired = create_access_token(42, KEY, 5, now=datetime.now(UTC) - timedelta(hours=1))
    assert read_access_token(expired, KEY) is None
    token = create_access_token(42, KEY, 5)
    assert read_access_token(token, "another-key-" + "z" * 30) is None
    assert read_access_token(token[:-2] + ("AA" if not token.endswith("AA") else "BB"), KEY) is None


def test_unsigned_incomplete_and_malformed_tokens_are_refused() -> None:
    now = datetime.now(UTC)
    claims = {"sub": "42", "iat": now, "exp": now + timedelta(hours=1), "typ": "access"}
    assert read_access_token(jwt.encode(claims, None, algorithm="none"), KEY) is None
    assert read_access_token(jwt.encode({k: v for k, v in claims.items() if k != "exp"}, KEY, "HS256"), KEY) is None
    assert read_access_token(jwt.encode({**claims, "typ": "refresh"}, KEY, "HS256"), KEY) is None
    assert read_access_token(jwt.encode({**claims, "sub": "abc"}, KEY, "HS256"), KEY) is None
    for junk in ("", "abc", "a.b.c"):
        assert read_access_token(junk, KEY) is None


# --- login throttle ------------------------------------------------------------------------------------

class Clock:
    now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_throttle_blocks_after_the_limit_and_frees_after_the_window() -> None:
    clock = Clock()
    throttle = LoginThrottle(max_failures=3, window_seconds=60, clock=clock)
    for _ in range(2):
        throttle.record_failure("k")
    assert throttle.retry_after("k") == 0
    throttle.record_failure("k")
    assert 1 <= throttle.retry_after("k") <= 61
    clock.now += 61
    assert throttle.retry_after("k") == 0


def test_throttle_ages_failures_out_one_by_one_and_keys_are_independent() -> None:
    clock = Clock()
    throttle = LoginThrottle(max_failures=3, window_seconds=60, clock=clock)
    for offset in (0, 30, 59):
        clock.now = 1000.0 + offset
        throttle.record_failure("a")
    assert throttle.retry_after("a") > 0 and throttle.retry_after("b") == 0
    clock.now = 1061.0  # the first failure has aged out
    assert throttle.retry_after("a") == 0


def test_a_successful_login_clears_the_failures() -> None:
    throttle = LoginThrottle(max_failures=2, window_seconds=60, clock=Clock())
    throttle.record_failure("k")
    throttle.reset("k")
    throttle.record_failure("k")
    assert throttle.retry_after("k") == 0


def test_throttle_memory_is_bounded() -> None:
    throttle = LoginThrottle(clock=Clock())
    for index in range(MAX_TRACKED_KEYS + 50):
        throttle.record_failure(str(index))
    assert len(throttle._failures) <= MAX_TRACKED_KEYS


# --- audit ---------------------------------------------------------------------------------------------

def test_audit_details_are_trimmed_to_small_plain_values(session: Session) -> None:
    entry = record(session, "demo.check", actor_label="system:test", detail={
        "long": "a" * 500, "count": 3, "ok": True, "object": object(), "items": list(range(100)),
        "nested": {"deep": {"deeper": 1}},
    })
    session.flush()
    detail = entry.detail
    assert len(detail["long"]) == MAX_DETAIL_TEXT and detail["count"] == 3 and detail["ok"] is True
    assert isinstance(detail["object"], str) and len(detail["items"]) == 50
    assert isinstance(detail["nested"]["deep"], str)  # nesting stops after two levels


def test_audit_names_the_actor_or_falls_back_to_a_label(session: Session) -> None:
    user = User(email="a@test.example", name="A", role=Role.ADMIN, password_hash="x")
    session.add(user)
    session.flush()
    assert record(session, "x", actor=user).actor_label == "a@test.example"
    assert record(session, "x", actor_label="system:seed").actor_label == "system:seed"
    assert record(session, "x").actor_label == "system"
    assert record(session, "x", target_id=7).target_id == "7"
