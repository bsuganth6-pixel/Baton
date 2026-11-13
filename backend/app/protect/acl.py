"""Who may see and do what. Clearance is derived from role; permissions are explicit."""
from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from app.models import Clearance, Role


class Permission(StrEnum):
    UPLOAD_SOURCES = "upload_sources"
    RUN_PIPELINE = "run_pipeline"
    REVIEW_CLAIMS = "review_claims"
    ANSWER_QUESTIONS = "answer_questions"
    VIEW_AUDIT = "view_audit"
    ACKNOWLEDGE = "acknowledge"


ROLE_CLEARANCE: dict[Role, int] = {
    Role.ADMIN: int(Clearance.RESTRICTED),
    Role.MANAGER: int(Clearance.RESTRICTED),
    Role.LEAVER: int(Clearance.CONFIDENTIAL),
    Role.SUCCESSOR: int(Clearance.CONFIDENTIAL),
    Role.TEAMMATE: int(Clearance.INTERNAL),
}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.MANAGER: frozenset(Permission),
    Role.LEAVER: frozenset({Permission.UPLOAD_SOURCES, Permission.REVIEW_CLAIMS, Permission.ANSWER_QUESTIONS}),
    Role.SUCCESSOR: frozenset({Permission.ACKNOWLEDGE}),
    Role.TEAMMATE: frozenset({Permission.ACKNOWLEDGE}),
}


def clearance_for(role: Role) -> int:
    return ROLE_CLEARANCE[role]


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]


def within_clearance(source_clearances: Iterable[int], viewer: int) -> bool:
    """True only if every source is at or below the viewer's level. No sources at all fails closed."""
    levels = list(source_clearances)
    return bool(levels) and all(level <= viewer for level in levels)
