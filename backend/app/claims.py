"""Claim visibility: a claim is visible only if every source behind it is within the viewer's clearance.

That includes the claim's own evidence plus, for a conflicting claim, the evidence of every other claim
in the same conflict — otherwise the "Conflicting" label would reveal that a hidden source exists.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import ClaimOut, ConflictOut, EvidenceOut, QuestionOut
from app.models import Claim, Clearance, Conflict, Question


def required_clearance(claim: Claim) -> int:
    """Highest source clearance behind this claim, counting its whole conflict group."""
    levels: list[int] = []
    for evidence in claim.evidence:
        levels.append(evidence.chunk.source.clearance)
    conflict = claim.conflict
    if conflict is not None:
        for other in conflict.claims:
            for evidence in other.evidence:
                levels.append(evidence.chunk.source.clearance)
    return max(levels) if levels else int(Clearance.PUBLIC)


def visible_claims(db: Session, viewer_clearance: int) -> list[Claim]:
    """All claims the viewer may see, ordered by subject then attribute."""
    claims = db.scalars(select(Claim).order_by(Claim.subject, Claim.attribute)).all()
    return [claim for claim in claims if required_clearance(claim) <= viewer_clearance]


def visible_claim(db: Session, claim_id: int, viewer_clearance: int) -> Claim | None:
    claim = db.get(Claim, claim_id)
    if claim is None or required_clearance(claim) > viewer_clearance:
        return None
    return claim


# --- serializers shared by the review, questions and handover APIs ----------------------------------


def evidence_out(evidence) -> EvidenceOut:
    chunk = evidence.chunk
    return EvidenceOut(id=evidence.id, chunk_id=chunk.id, source_id=chunk.source_id,
                       source_name=chunk.source.name, locator=chunk.locator, quote=evidence.quote,
                       verified=evidence.verified, occurred_at=chunk.occurred_at)


def claim_out(claim: Claim) -> ClaimOut:
    return ClaimOut(
        id=claim.id, kind=claim.kind.value, subject=claim.subject, attribute=claim.attribute,
        value=claim.value, statement=claim.statement, label=claim.label.value,
        review_status=claim.review_status.value, version=claim.version, conflict_id=claim.conflict_id,
        required_clearance=required_clearance(claim),
        evidence=[evidence_out(e) for e in claim.evidence],
        created_at=claim.created_at, updated_at=claim.updated_at)


def conflict_out(conflict: Conflict) -> ConflictOut:
    return ConflictOut(id=conflict.id, subject=conflict.subject, attribute=conflict.attribute,
                       status=conflict.status.value, created_at=conflict.created_at,
                       claims=[claim_out(c) for c in conflict.claims])


def question_out(question: Question) -> QuestionOut:
    return QuestionOut(id=question.id, kind=question.kind.value, text=question.text,
                       status=question.status.value, claim_id=question.claim_id, conflict_id=question.conflict_id,
                       answer=question.answer, answer_source_id=question.answer_source_id,
                       answered_by_id=question.answered_by_id, answered_at=question.answered_at)


def visible_questions(db: Session, viewer_clearance: int) -> list[Question]:
    """Questions whose claim (or, for conflict questions, whose conflict) the viewer may see."""
    rows = db.scalars(select(Question).order_by(Question.status, Question.id)).all()
    visible = []
    for question in rows:
        if question.claim is not None:
            ok = required_clearance(question.claim) <= viewer_clearance
        elif question.conflict is not None:
            ok = all(required_clearance(c) <= viewer_clearance for c in question.conflict.claims)
        else:
            ok = False
        if ok:
            visible.append(question)
    return visible


def visible_conflicts(db: Session, viewer_clearance: int) -> list[Conflict]:
    rows = db.scalars(select(Conflict).order_by(Conflict.id)).all()
    return [c for c in rows if all(required_clearance(cl) <= viewer_clearance for cl in c.claims)]

