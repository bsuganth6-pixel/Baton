"""Handover view: the successor's read-only snapshot of visible knowledge, and its acknowledgement."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.api.deps import current_user, get_db, get_settings_dep, require_permission
from app.api.errors import ApiError
from app.api.schemas import AckIn, AckOut, HandoverStats, HandoverViewOut
from app.claims import (
    claim_out,
    conflict_out,
    question_out,
    visible_claims,
    visible_conflicts,
    visible_questions,
)
from app.config import Settings
from app.ingest.service import find_project
from app.models import Acknowledgement, Chunk, Claim, Clearance, Conflict, Question, Role, Source, SourceStatus, User
from app.protect.acl import Permission, clearance_for

router = APIRouter(prefix="/api/handover", tags=["handover"])


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Role):
        return obj.value
    return str(obj)


def _fingerprint(view: dict[str, Any]) -> str:
    payload = json.dumps(view, sort_keys=True, separators=(",", ":"), default=_json_default).encode()
    return hashlib.sha256(payload).hexdigest()


def _summaries(db: Session, clearance: int, project) -> tuple[dict, list, list, list, dict[str, int]]:
    claims = visible_claims(db, clearance)
    conflicts = visible_conflicts(db, clearance)
    questions = visible_questions(db, clearance)

    visible_sources = db.scalars(
        select(Source).where(Source.project_id == project.id, Source.status == SourceStatus.INGESTED,
                             Source.clearance <= clearance)).all()
    all_sources = db.scalars(
        select(Source).where(Source.project_id == project.id, Source.status == SourceStatus.INGESTED)).all()
    visible_source_ids = [s.id for s in visible_sources]
    all_source_ids = [s.id for s in all_sources]

    chunks_visible = db.scalar(select(func.count(Chunk.id)).where(Chunk.source_id.in_(visible_source_ids))) or 0
    chunks_total = db.scalar(select(func.count(Chunk.id)).where(Chunk.source_id.in_(all_source_ids))) or 0

    accepted = len([c for c in claims if c.review_status.value == "accepted"])
    conflicts_open = len([c for c in conflicts if c.status.value == "open"])
    conflicts_resolved = len([c for c in conflicts if c.status.value == "resolved"])
    questions_open = len([q for q in questions if q.status.value == "open"])
    questions_answered = len([q for q in questions if q.status.value == "answered"])
    redactions = sum(sum((s.redaction_counts or {}).values()) for s in visible_sources)

    stats = {
        "sources": len(visible_sources), "chunks": chunks_visible, "claims": len(claims), "accepted": accepted,
        "conflicts_open": conflicts_open, "conflicts_resolved": conflicts_resolved, "questions_open": questions_open,
        "questions_answered": questions_answered, "conflicts": len(conflicts), "questions": len(questions),
        "redactions": redactions,
    }

    withheld = {
        "sources": len(all_sources) - len(visible_sources),
        "chunks": (chunks_total or 0) - (chunks_visible or 0),
        "claims": _all_claims_count(db, project.id) - len(claims),
        "conflicts": _all_conflicts_count(db, project.id) - len(conflicts),
        "questions": _all_questions_count(db, project.id) - len(questions),
    }
    withheld = {k: v for k, v in withheld.items() if v > 0}

    return stats, claims, conflicts, questions, withheld


def _all_claims_count(db: Session, project_id: int) -> int:
    return db.scalar(select(func.count(Claim.id)).where(Claim.project_id == project_id)) or 0


def _all_conflicts_count(db: Session, project_id: int) -> int:
    return db.scalar(select(func.count(Conflict.id)).where(Conflict.project_id == project_id)) or 0


def _all_questions_count(db: Session, project_id: int) -> int:
    return db.scalar(select(func.count(Question.id)).where(Question.project_id == project_id)) or 0


@router.get("", response_model=HandoverViewOut)
def handover_view(user: User = Depends(current_user), db: Session = Depends(get_db),
                  settings: Settings = Depends(get_settings_dep)) -> HandoverViewOut:
    project = find_project(db)
    if project is None:
        raise ApiError(404, "no_project", "No project found. Upload a source first.")
    clearance = clearance_for(user.role)
    stats, claims, conflicts, questions, withheld = _summaries(db, clearance, project)
    withheld = withheld if settings.show_withheld_count else {}

    view: dict[str, Any] = {
        "project_name": project.name, "as_of": project.as_of, "viewer_role": user.role.value,
        "viewer_clearance": clearance, "stats": stats,
        "claims": [_claim_summary(c) for c in claims], "conflicts": [_conflict_summary(c) for c in conflicts],
        "questions": [_question_summary(q) for q in questions], "withheld": withheld,
    }
    view_sha256 = _fingerprint(view)

    return HandoverViewOut(
        project_name=project.name, as_of=project.as_of, viewer_role=user.role.value, viewer_clearance=clearance,
        stats=HandoverStats(**stats), claims=[claim_out(c) for c in claims],
        conflicts=[conflict_out(c) for c in conflicts], questions=[question_out(q) for q in questions],
        withheld=withheld, view_sha256=view_sha256)


@router.post("/acknowledge", response_model=AckOut)
def acknowledge(body: AckIn, user: User = Depends(require_permission(Permission.ACKNOWLEDGE)),
                db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)) -> AckOut:
    project = find_project(db)
    if project is None:
        raise ApiError(404, "no_project", "No project found.")

    clearance = clearance_for(user.role)
    stats, claims, conflicts, questions, withheld = _summaries(db, clearance, project)
    withheld = withheld if settings.show_withheld_count else {}
    expected_visible = len(claims) + len(conflicts) + len(questions)
    expected_withheld = sum(withheld.values())

    view: dict[str, Any] = {
        "project_name": project.name, "as_of": project.as_of, "viewer_role": user.role.value,
        "viewer_clearance": clearance, "stats": stats,
        "claims": [_claim_summary(c) for c in claims], "conflicts": [_conflict_summary(c) for c in conflicts],
        "questions": [_question_summary(q) for q in questions], "withheld": withheld,
    }
    expected_sha256 = _fingerprint(view)

    if body.view_sha256 != expected_sha256:
        raise ApiError(409, "view_changed", "The handover view has changed since it was displayed. Reload and try again.",
                       details={"expected_sha256": expected_sha256})
    if body.visible_count != expected_visible:
        raise ApiError(409, "view_changed", "Visible count mismatch.", details={"expected": expected_visible})
    if body.withheld_count != expected_withheld:
        raise ApiError(409, "view_changed", "Withheld count mismatch.", details={"expected": expected_withheld})

    ack = Acknowledgement(project_id=project.id, user_id=user.id, role=user.role, view_sha256=expected_sha256,
                          visible_count=expected_visible, withheld_count=expected_withheld)
    db.add(ack)
    db.commit()
    audit.record(db, "handover.acknowledged", actor=user, project_id=project.id, target_type="acknowledgement",
                 target_id=ack.id, detail={"view_sha256": expected_sha256, "visible": expected_visible,
                                          "withheld": expected_withheld})
    db.commit()
    return AckOut(acknowledged=True, acknowledged_at=ack.acknowledged_at)


def _claim_summary(c) -> dict[str, Any]:
    return {"id": c.id, "subject": c.subject, "attribute": c.attribute, "value": c.value,
            "label": c.label.value, "review_status": c.review_status.value, "version": c.version,
            "conflict_id": c.conflict_id}


def _conflict_summary(c) -> dict[str, Any]:
    return {"id": c.id, "subject": c.subject, "attribute": c.attribute, "status": c.status.value,
            "claim_ids": [cl.id for cl in c.claims]}


def _question_summary(q) -> dict[str, Any]:
    return {"id": q.id, "kind": q.kind.value, "status": q.status.value, "claim_id": q.claim_id,
            "conflict_id": q.conflict_id}
