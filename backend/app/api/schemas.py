"""Request and response shapes."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models import Role, SourceKind, SourceStatus


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: Role
    clearance: int
    permissions: list[str]


class LoginOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserOut


class DemoCredentialsOut(BaseModel):
    email: str
    password: str


class SourceOut(BaseModel):
    id: int
    name: str
    kind: SourceKind
    clearance: int
    clearance_label: str
    status: SourceStatus
    error: str | None
    byte_size: int
    chunk_count: int
    redactions: dict[str, int]
    created_at: datetime


class UploadOut(BaseModel):
    duplicate: bool
    message: str
    source: SourceOut | None


class ChunkOut(BaseModel):
    id: int
    ordinal: int
    text: str
    locator: str | None
    author: str | None
    occurred_at: datetime | None


class SearchHit(BaseModel):
    chunk_id: int
    source_id: int
    source_name: str
    locator: str | None
    author: str | None
    occurred_at: datetime | None
    snippet: str


# --- extraction pipeline ---------------------------------------------------------------------------

class EvidenceOut(BaseModel):
    id: int
    chunk_id: int
    source_id: int
    source_name: str
    locator: str | None
    quote: str
    verified: bool
    occurred_at: datetime | None


class ClaimOut(BaseModel):
    id: int
    kind: str
    subject: str
    attribute: str
    value: str | None
    statement: str
    label: str
    review_status: str
    version: int
    conflict_id: int | None
    required_clearance: int
    evidence: list[EvidenceOut]
    created_at: datetime
    updated_at: datetime


class ConflictOut(BaseModel):
    id: int
    subject: str
    attribute: str
    status: str
    created_at: datetime
    claims: list[ClaimOut]


class QuestionOut(BaseModel):
    id: int
    kind: str
    text: str
    status: str
    claim_id: int | None
    conflict_id: int | None
    answer: str | None
    answer_source_id: int | None
    answered_by_id: int | None
    answered_at: datetime | None


class JobOut(BaseModel):
    id: str
    kind: str
    status: str
    stage: str | None
    error: str | None
    result: dict[str, Any] | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class PipelineRunIn(BaseModel):
    force: bool = False


class PipelineResultOut(BaseModel):
    job_id: str
    status: str
    engine: str  # "llm", "rules", or "failed"
    claims: int
    evidence: int
    conflicts: int
    questions: int
    dropped: int
    labels: dict[str, int]


# --- review ----------------------------------------------------------------------------------------

class ClaimReviewIn(BaseModel):
    statement: str | None = None
    value: str | None = None
    note: str | None = None


class ClaimReviewOut(BaseModel):
    claim: ClaimOut
    version: int


class QuestionAnswerIn(BaseModel):
    answer: str = Field(min_length=1, max_length=2000)
    source_id: int | None = None


class QuestionAnswerOut(BaseModel):
    question: QuestionOut


# --- handover --------------------------------------------------------------------------------------

class HandoverStats(BaseModel):
    sources: int
    chunks: int
    claims: int
    accepted: int
    conflicts_open: int
    conflicts_resolved: int
    questions_open: int
    questions_answered: int
    conflicts: int  # total conflict groups (open + resolved)
    questions: int  # total questions (open + answered)
    redactions: int


class HandoverViewOut(BaseModel):
    project_name: str
    as_of: date | None
    viewer_role: str
    viewer_clearance: int
    stats: HandoverStats
    claims: list[ClaimOut]
    conflicts: list[ConflictOut]
    questions: list[QuestionOut]
    withheld: dict[str, int]
    view_sha256: str  # fingerprint of this exact view; the acknowledgement must echo it


class AuditOut(BaseModel):
    id: int
    action: str
    actor_label: str
    project_id: int | None
    target_type: str | None
    target_id: str | None
    detail: dict[str, Any] | None
    created_at: datetime


class AckIn(BaseModel):
    # Any malformed/short fingerprint is rejected by the comparison below (409 view_changed),
    # not by length validation, so the client always gets the business-level error.
    view_sha256: str = Field(min_length=1)
    visible_count: int = Field(ge=0)
    withheld_count: int = Field(ge=0)


class AckOut(BaseModel):
    acknowledged: bool
    acknowledged_at: datetime


# --- assistant -------------------------------------------------------------------------------------

class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=500)


class CitationOut(BaseModel):
    claim_id: int
    chunk_id: int
    source_id: int
    source_name: str
    locator: str | None
    quote: str


class AnswerOut(BaseModel):
    answer: str
    citations: list[CitationOut]
    refused: bool = False

