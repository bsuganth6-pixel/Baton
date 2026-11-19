"""Open questions from the pipeline, and the answers that resolve them. Answers cite their source."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.api.deps import current_user, get_db, require_permission
from app.api.errors import ApiError
from app.api.schemas import QuestionAnswerIn, QuestionAnswerOut, QuestionOut
from app.claims import question_out, required_clearance, visible_questions
from app.models import Question, QuestionStatus, Source, SourceStatus, User
from app.protect.acl import Permission, clearance_for

router = APIRouter(prefix="/api/questions", tags=["questions"])


@router.get("", response_model=list[QuestionOut])
def list_questions(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[QuestionOut]:
    return [question_out(q) for q in visible_questions(db, clearance_for(user.role))]


@router.post("/{question_id}/answer", response_model=QuestionAnswerOut)
def answer_question(question_id: int, body: QuestionAnswerIn,
                    user: User = Depends(require_permission(Permission.ANSWER_QUESTIONS)),
                    db: Session = Depends(get_db)) -> QuestionAnswerOut:
    question = _visible_question(db, question_id, user)
    if question.status is QuestionStatus.ANSWERED:
        raise ApiError(409, "already_answered", "This question has already been answered.")
    answer = body.answer.strip()
    source: Source | None = None
    if body.source_id is not None:
        source = db.get(Source, body.source_id)
        if source is None or source.status is not SourceStatus.INGESTED or source.clearance > clearance_for(user.role):
            raise ApiError(404, "not_found", "Source not found.")
    question.answer = answer
    question.status = QuestionStatus.ANSWERED
    question.answered_by_id = user.id
    question.answered_at = datetime.now(UTC)
    question.answer_source_id = source.id if source is not None else None
    db.commit()
    audit.record(db, "question.answered", actor=user, project_id=question.project_id, target_type="question",
                 target_id=question.id, detail={"kind": question.kind.value, "source_id": source.id
                                                if source is not None else None})
    db.commit()
    return QuestionAnswerOut(question=question_out(question))


def _visible_question(db: Session, question_id: int, user: User) -> Question:
    question = db.scalar(select(Question).where(Question.id == question_id))
    if question is None:
        raise ApiError(404, "not_found", "Question not found.")
    clearance = clearance_for(user.role)
    if question.claim is not None:
        if required_clearance(question.claim) > clearance:
            raise ApiError(404, "not_found", "Question not found.")
    elif question.conflict is not None:
        if any(required_clearance(c) > clearance for c in question.conflict.claims):
            raise ApiError(404, "not_found", "Question not found.")
    return question
