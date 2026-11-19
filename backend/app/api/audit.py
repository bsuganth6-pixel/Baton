"""Append-only audit trail: recent entries for the current project, newest first."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_db, require_permission
from app.api.schemas import AuditOut
from app.config import Settings
from app.ingest.service import find_project
from app.models import AuditLog, User
from app.protect.acl import Permission

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("", response_model=list[AuditOut])
def list_audit(action: str | None = Query(None, max_length=80), limit: int = Query(100, ge=1, le=500),
               user: User = Depends(require_permission(Permission.VIEW_AUDIT)),
               db: Session = Depends(get_db), settings: Settings = Depends(lambda: Settings())) -> list[AuditOut]:
    project = find_project(db)
    if project is None:
        return []
    stmt = select(AuditLog).where(AuditLog.project_id == project.id).order_by(AuditLog.created_at.desc()).limit(limit)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    rows = db.scalars(stmt).all()
    return [_audit_out(r) for r in rows]


def _audit_out(row: AuditLog) -> AuditOut:
    return AuditOut(id=row.id, action=row.action, actor_label=row.actor_label, project_id=row.project_id,
                    target_type=row.target_type, target_id=row.target_id, detail=row.detail, created_at=row.created_at)
