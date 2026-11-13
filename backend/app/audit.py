"""Append-only audit trail. Details hold ids, counts and codes only: never content, secrets or PII."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog, User

MAX_DETAIL_TEXT = 200


def _clean(value: Any, depth: int = 0) -> Any:
    if isinstance(value, str):
        return value[:MAX_DETAIL_TEXT]
    if isinstance(value, bool | int | float) or value is None:
        return value
    if depth < 2 and isinstance(value, dict):
        return {str(k)[:60]: _clean(v, depth + 1) for k, v in value.items()}
    if depth < 2 and isinstance(value, list | tuple):
        return [_clean(v, depth + 1) for v in value[:50]]
    return str(value)[:MAX_DETAIL_TEXT]


def record(
    db: Session,
    action: str,
    *,
    actor: User | None = None,
    actor_label: str | None = None,
    project_id: int | None = None,
    target_type: str | None = None,
    target_id: int | str | None = None,
    detail: dict[str, Any] | None = None,
) -> AuditLog:
    """Add one audit row to the session (the caller commits)."""
    entry = AuditLog(
        project_id=project_id,
        actor_id=actor.id if actor else None,
        actor_label=actor.email if actor else (actor_label or "system"),
        action=action,
        target_type=target_type,
        target_id=None if target_id is None else str(target_id),
        detail=_clean(detail) if detail else None,
    )
    db.add(entry)
    return entry
