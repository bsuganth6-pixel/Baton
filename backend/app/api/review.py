"""Claim review: accept, reject or edit a proposed claim. Every change is versioned and audited."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import audit
from app.api.deps import current_user, get_db, require_permission
from app.api.errors import ApiError
from app.api.schemas import ClaimOut, ClaimReviewIn, ClaimReviewOut
from app.claims import claim_out, visible_claim, visible_claims
from app.models import Claim, ClaimVersion, ReviewStatus, User, VersionAction
from app.protect.acl import Permission, clearance_for

router = APIRouter(prefix="/api/review", tags=["review"])


@router.get("/claims", response_model=list[ClaimOut])
def list_review_claims(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[ClaimOut]:
    """The reviewer's queue: claims the reviewer may see, recently updated first."""
    claims = visible_claims(db, clearance_for(user.role))
    claims.sort(key=lambda c: c.updated_at or c.created_at, reverse=True)
    return [claim_out(c) for c in claims]


def _claim_for_review(db: Session, claim_id: int, user: User) -> Claim:
    """The claim if the reviewer may see it and it is still awaiting review; otherwise an ApiError."""
    claim = visible_claim(db, claim_id, clearance_for(user.role))
    if claim is None:
        raise ApiError(404, "not_found", "Claim not found.")
    if claim.review_status is not ReviewStatus.PROPOSED:
        raise ApiError(409, "already_reviewed", "This claim has already been reviewed.")
    return claim


@router.patch("/claims/{claim_id}", response_model=ClaimReviewOut)
def edit_claim(claim_id: int, body: ClaimReviewIn,
               user: User = Depends(require_permission(Permission.REVIEW_CLAIMS)),
               db: Session = Depends(get_db)) -> ClaimReviewOut:
    claim = _claim_for_review(db, claim_id, user)
    changed = False
    if body.statement is not None and body.statement.strip() and body.statement != claim.statement:
        claim.statement = body.statement.strip()
        changed = True
    if body.value is not None and body.value != claim.value:
        claim.value = body.value.strip() or None
        changed = True
    if not changed:
        raise ApiError(422, "nothing_to_edit", "The proposed edit leaves the claim unchanged.")
    claim.version += 1
    _record_version(db, claim, VersionAction.EDITED, body.note, user.id)
    db.commit()
    audit.record(db, "claim.edited", actor=user, project_id=claim.project_id, target_type="claim",
                 target_id=claim.id, detail={"version": claim.version})
    db.commit()
    return ClaimReviewOut(claim=claim_out(claim), version=claim.version)


@router.post("/claims/{claim_id}/accept", response_model=ClaimReviewOut)
def accept_claim(claim_id: int, body: ClaimReviewIn | None = None,
                 user: User = Depends(require_permission(Permission.REVIEW_CLAIMS)),
                 db: Session = Depends(get_db)) -> ClaimReviewOut:
    claim = _claim_for_review(db, claim_id, user)
    _apply_decision(db, claim, body, ReviewStatus.ACCEPTED, VersionAction.ACCEPTED, user)
    db.commit()
    audit.record(db, "claim.accepted", actor=user, project_id=claim.project_id, target_type="claim",
                 target_id=claim.id, detail={"version": claim.version})
    db.commit()
    return ClaimReviewOut(claim=claim_out(claim), version=claim.version)


@router.post("/claims/{claim_id}/reject", response_model=ClaimReviewOut)
def reject_claim(claim_id: int, body: ClaimReviewIn | None = None,
                 user: User = Depends(require_permission(Permission.REVIEW_CLAIMS)),
                 db: Session = Depends(get_db)) -> ClaimReviewOut:
    claim = _claim_for_review(db, claim_id, user)
    _apply_decision(db, claim, body, ReviewStatus.REJECTED, VersionAction.REJECTED, user)
    db.commit()
    audit.record(db, "claim.rejected", actor=user, project_id=claim.project_id, target_type="claim",
                 target_id=claim.id, detail={"version": claim.version})
    db.commit()
    return ClaimReviewOut(claim=claim_out(claim), version=claim.version)


def _apply_decision(db: Session, claim: Claim, body: ClaimReviewIn | None, status: ReviewStatus,
                    action: VersionAction, user: User) -> None:
    body = body or ClaimReviewIn()
    if body.statement is not None and body.statement.strip() and body.statement != claim.statement:
        claim.statement = body.statement.strip()
    if body.value is not None and body.value != claim.value:
        claim.value = body.value.strip() or None
    claim.version += 1  # the decision itself is a lifecycle event: CREATED v1, ACCEPTED v2, ...
    claim.review_status = status
    _record_version(db, claim, action, body.note, user.id)


def _record_version(db: Session, claim: Claim, action: VersionAction, note: str | None, actor_id: int) -> None:
    db.add(ClaimVersion(claim_id=claim.id, version=claim.version, action=action, statement=claim.statement,
                        value=claim.value, review_status=claim.review_status, note=note, actor_id=actor_id))
    db.flush()
