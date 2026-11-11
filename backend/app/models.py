"""Database models.

Rules baked into the schema:
* Source text lives in Postgres (Render's disk is ephemeral) and is always the *redacted*
  text. Raw secrets and PII are never persisted.
* A claim is backed by Evidence rows; only ``verified`` evidence may ever be shown.
* ``Source.clearance`` defaults to the most restrictive level, so a forgotten value fails closed.
"""
from __future__ import annotations

import enum
import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    text,
    true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# --- enumerations (stored as short strings; no native Postgres enums, which are painful to migrate) ---

class Role(enum.StrEnum):
    ADMIN = "admin"
    MANAGER = "manager"
    LEAVER = "leaver"
    SUCCESSOR = "successor"
    TEAMMATE = "teammate"


class Clearance(enum.IntEnum):
    """Sources carry a level; a viewer may see a source only at or below their own level."""

    PUBLIC = 0
    INTERNAL = 1
    CONFIDENTIAL = 2
    RESTRICTED = 3


class SourceKind(enum.StrEnum):
    GITHUB = "github"
    NOTES = "notes"
    TASKS = "tasks"
    CHAT = "chat"
    LEAVER_QA = "leaver_qa"


class SourceStatus(enum.StrEnum):
    INGESTED = "ingested"
    FAILED = "failed"


class JobStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ClaimKind(enum.StrEnum):
    FACT = "fact"
    DECISION = "decision"
    OWNER = "owner"


class Label(enum.StrEnum):
    """Confidence labels. Set by rules in pipeline/confidence.py, never by the LLM."""

    SUPPORTED = "supported"
    SINGLE_SOURCE = "single_source"
    CONFLICTING = "conflicting"
    STALE = "stale"
    MISSING = "missing"


class ReviewStatus(enum.StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class VersionAction(enum.StrEnum):
    CREATED = "created"
    EDITED = "edited"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ConflictStatus(enum.StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class QuestionKind(enum.StrEnum):
    CONFLICT = "conflict"
    MISSING = "missing"
    STALE = "stale"
    SINGLE_SOURCE = "single_source"


class QuestionStatus(enum.StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    SKIPPED = "skipped"


def _enum(cls: type[enum.Enum]) -> Enum:
    return Enum(
        cls,
        name=cls.__name__.lower(),
        native_enum=False,
        length=32,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


def utcnow() -> datetime:
    return datetime.now(UTC)


# Server defaults are module-level because several models have a column called "text".
NOW = text("CURRENT_TIMESTAMP")
EMPTY_JSON = text("'{}'")
MOST_RESTRICTIVE = text(str(int(Clearance.RESTRICTED)))

pk = Annotated[int, mapped_column(primary_key=True)]
created = Annotated[datetime, mapped_column(DateTime(timezone=True), default=utcnow, server_default=NOW)]


# --- identity -------------------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)

    id: Mapped[pk]
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[Role] = mapped_column(_enum(Role))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    created_at: Mapped[created]


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[pk]
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    as_of: Mapped[date | None] = mapped_column(Date)  # reference date for staleness; NULL means today
    created_at: Mapped[created]


# --- sources --------------------------------------------------------------------------------------

class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("project_id", "content_sha256", name="uq_sources_project_content"),
        CheckConstraint("clearance BETWEEN 0 AND 3", name="clearance_range"),
        CheckConstraint("length(content_sha256) = 64", name="sha256_length"),
        CheckConstraint("status = 'failed' OR length(text) > 0", name="ingested_has_text"),
        CheckConstraint("status <> 'failed' OR error IS NOT NULL", name="failed_has_error"),
    )

    id: Mapped[pk]
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[SourceKind] = mapped_column(_enum(SourceKind))
    name: Mapped[str] = mapped_column(String(255))
    clearance: Mapped[int] = mapped_column(
        Integer, default=int(Clearance.RESTRICTED), server_default=MOST_RESTRICTIVE
    )
    text: Mapped[str] = mapped_column(Text)  # redacted full text
    content_sha256: Mapped[str] = mapped_column(String(64))  # of the original bytes, for idempotent seeding
    byte_size: Mapped[int] = mapped_column(Integer)
    redaction_counts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, server_default=EMPTY_JSON)
    status: Mapped[SourceStatus] = mapped_column(_enum(SourceStatus), default=SourceStatus.INGESTED)
    error: Mapped[str | None] = mapped_column(String(500))  # user-safe message when ingest failed
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[created]

    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="source", cascade="all, delete-orphan", passive_deletes=True, order_by="Chunk.ordinal"
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("source_id", "ordinal", name="uq_chunks_source_ordinal"),
        CheckConstraint("ordinal >= 0", name="ordinal_not_negative"),
        CheckConstraint("length(trim(text)) > 0", name="text_not_blank"),
    )

    id: Mapped[pk]
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)  # redacted; exactly what the LLM sees and quotes are checked against
    locator: Mapped[str | None] = mapped_column(String(255))  # e.g. "issue #63", "task T-152", "2026-09-02 14:30"
    author: Mapped[str | None] = mapped_column(String(120))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # when it happened, for staleness

    source: Mapped[Source] = relationship(back_populates="chunks")


# --- background jobs ------------------------------------------------------------------------------

class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("status <> 'failed' OR error IS NOT NULL", name="failed_has_error"),
        Index("ix_jobs_project_created", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(40), default="pipeline")
    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus), default=JobStatus.QUEUED)
    stage: Mapped[str | None] = mapped_column(String(60))
    error: Mapped[str | None] = mapped_column(String(500))  # user-safe; details go to the server log
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[created]
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- claims ---------------------------------------------------------------------------------------

class Conflict(Base):
    __tablename__ = "conflicts"
    __table_args__ = (
        CheckConstraint("status <> 'resolved' OR resolved_at IS NOT NULL", name="resolved_has_timestamp"),
    )

    id: Mapped[pk]
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    subject: Mapped[str] = mapped_column(String(200))
    attribute: Mapped[str] = mapped_column(String(200))
    status: Mapped[ConflictStatus] = mapped_column(_enum(ConflictStatus), default=ConflictStatus.OPEN)
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[created]

    claims: Mapped[list[Claim]] = relationship(back_populates="conflict")


class Claim(Base):
    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_claims_project_subject_attribute", "project_id", "subject", "attribute"),
    )

    id: Mapped[pk]
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), index=True)
    conflict_id: Mapped[int | None] = mapped_column(ForeignKey("conflicts.id", ondelete="SET NULL"), index=True)
    kind: Mapped[ClaimKind] = mapped_column(_enum(ClaimKind))
    subject: Mapped[str] = mapped_column(String(200))
    attribute: Mapped[str] = mapped_column(String(200))
    value: Mapped[str | None] = mapped_column(Text)  # NULL when the value is missing
    statement: Mapped[str] = mapped_column(Text)
    label: Mapped[Label] = mapped_column(_enum(Label))  # no default: it must always be computed
    review_status: Mapped[ReviewStatus] = mapped_column(_enum(ReviewStatus), default=ReviewStatus.PROPOSED)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[created]
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=NOW
    )

    conflict: Mapped[Conflict | None] = relationship(back_populates="claims")
    evidence: Mapped[list[Evidence]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", passive_deletes=True
    )
    versions: Mapped[list[ClaimVersion]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", passive_deletes=True, order_by="ClaimVersion.version"
    )


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (CheckConstraint("length(trim(quote)) > 0", name="quote_not_blank"),)

    id: Mapped[pk]
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    # No ON DELETE rule on purpose: a chunk that backs evidence cannot be deleted from under a claim.
    chunk_id: Mapped[int] = mapped_column(ForeignKey("chunks.id"), index=True)
    quote: Mapped[str] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    claim: Mapped[Claim] = relationship(back_populates="evidence")
    chunk: Mapped[Chunk] = relationship()


class ClaimVersion(Base):
    """One row per revision of a claim: created by the pipeline, then edited, accepted or rejected."""

    __tablename__ = "claim_versions"
    __table_args__ = (UniqueConstraint("claim_id", "version", name="uq_claim_versions_claim_version"),)

    id: Mapped[pk]
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    action: Mapped[VersionAction] = mapped_column(_enum(VersionAction))
    statement: Mapped[str] = mapped_column(Text)
    value: Mapped[str | None] = mapped_column(Text)
    review_status: Mapped[ReviewStatus] = mapped_column(_enum(ReviewStatus))
    note: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[created]

    claim: Mapped[Claim] = relationship(back_populates="versions")


# --- interview, handover, audit -------------------------------------------------------------------

class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (
        CheckConstraint("claim_id IS NOT NULL OR conflict_id IS NOT NULL", name="has_target"),
        CheckConstraint("status <> 'answered' OR answer IS NOT NULL", name="answered_has_answer"),
    )

    id: Mapped[pk]
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    claim_id: Mapped[int | None] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    conflict_id: Mapped[int | None] = mapped_column(ForeignKey("conflicts.id", ondelete="CASCADE"), index=True)
    kind: Mapped[QuestionKind] = mapped_column(_enum(QuestionKind))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[QuestionStatus] = mapped_column(_enum(QuestionStatus), default=QuestionStatus.OPEN)
    answer: Mapped[str | None] = mapped_column(Text)
    answered_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answer_source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))
    created_at: Mapped[created]


class Acknowledgement(Base):
    """A reader confirming they read the handover view they were shown (identified by its hash)."""

    __tablename__ = "acknowledgements"
    __table_args__ = (
        UniqueConstraint("user_id", "project_id", "role", "view_sha256", name="uq_acknowledgements_view"),
        CheckConstraint("visible_count >= 0 AND withheld_count >= 0", name="counts_not_negative"),
    )

    id: Mapped[pk]
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[Role] = mapped_column(_enum(Role))
    view_sha256: Mapped[str] = mapped_column(String(64))
    visible_count: Mapped[int] = mapped_column(Integer)
    withheld_count: Mapped[int] = mapped_column(Integer)
    acknowledged_at: Mapped[created]


class AuditLog(Base):
    """Append-only by convention: no code path updates or deletes rows. Never store secrets, PII or traces here."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_project_created", "project_id", "created_at"),
        Index("ix_audit_log_target", "target_type", "target_id"),
    )

    id: Mapped[pk]
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_label: Mapped[str] = mapped_column(String(255))  # email or "system:pipeline"; survives user deletion
    action: Mapped[str] = mapped_column(String(80))
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[created]
