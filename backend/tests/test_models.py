"""Constraints and cascades the schema promises. Runs on SQLite and, when configured, real Postgres."""
from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from app.models import (
    Acknowledgement,
    AuditLog,
    Chunk,
    Claim,
    ClaimKind,
    ClaimVersion,
    Clearance,
    Conflict,
    ConflictStatus,
    Evidence,
    Job,
    JobStatus,
    Label,
    Project,
    Question,
    QuestionKind,
    QuestionStatus,
    ReviewStatus,
    Role,
    Source,
    SourceKind,
    SourceStatus,
    User,
    VersionAction,
)

QUOTE = "The public API is rate limited to 100 requests per minute per user."


# --- helpers --------------------------------------------------------------------------------------

@contextmanager
def refused(db: Session) -> Iterator[None]:
    """Assert the database refuses whatever the block adds. Rows from earlier in the test survive."""
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            yield
            db.flush()


def count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


def make_project(db: Session, slug: str = "campusconnect") -> Project:
    project = Project(slug=slug, name="CampusConnect handover")
    db.add(project)
    db.flush()
    return project


def make_user(db: Session, email: str = "priya@campusconnect.example", role: Role = Role.SUCCESSOR) -> User:
    user = User(email=email, name="Priya Nair", role=role, password_hash="not-a-real-hash")
    db.add(user)
    db.flush()
    return user


def make_source(db: Session, project: Project, name: str = "notes.md", **overrides: Any) -> Source:
    values: dict[str, Any] = {
        "project_id": project.id, "kind": SourceKind.NOTES, "name": name, "clearance": 1,
        "text": QUOTE, "content_sha256": hashlib.sha256(name.encode()).hexdigest(), "byte_size": len(QUOTE),
    }
    values.update(overrides)
    source = Source(**values)
    db.add(source)
    db.flush()
    return source


def make_chunk(db: Session, source: Source, ordinal: int = 0, body: str = QUOTE) -> Chunk:
    chunk = Chunk(source_id=source.id, ordinal=ordinal, text=body)
    db.add(chunk)
    db.flush()
    return chunk


def make_claim(db: Session, project: Project, **overrides: Any) -> Claim:
    values: dict[str, Any] = {
        "project_id": project.id, "kind": ClaimKind.FACT, "subject": "campus-api", "attribute": "rate-limit",
        "value": "100 requests per minute per user", "statement": QUOTE, "label": Label.SINGLE_SOURCE,
    }
    values.update(overrides)
    claim = Claim(**values)
    db.add(claim)
    db.flush()
    return claim


def make_evidence(db: Session, claim: Claim, chunk: Chunk, **overrides: Any) -> Evidence:
    values: dict[str, Any] = {"claim_id": claim.id, "chunk_id": chunk.id, "quote": QUOTE}
    values.update(overrides)
    evidence = Evidence(**values)
    db.add(evidence)
    db.flush()
    return evidence


# --- the ground truth fits the schema -------------------------------------------------------------

def test_ground_truth_uses_only_values_the_schema_allows(truth: dict[str, Any]) -> None:
    assert truth["clearance_levels"] == {c.name.lower(): int(c) for c in Clearance}
    assert {s["kind"] for s in truth["sources"]} <= {k.value for k in SourceKind}
    assert {s["clearance"] for s in truth["sources"]} <= {int(c) for c in Clearance}
    assert {c["expected_label"] for c in truth["claims"]} <= {label.value for label in Label}
    assert {c["kind"] for c in truth["claims"]} <= {k.value for k in ClaimKind}
    assert {q["kind"] for q in truth["expected_questions"]} <= {k.value for k in QuestionKind}


# --- identity -------------------------------------------------------------------------------------

def test_user_email_must_be_lowercase_and_unique(session: Session) -> None:
    make_user(session)
    with refused(session):
        session.add(User(email="Boss@Campusconnect.example", name="B", role=Role.ADMIN, password_hash="x"))
    with refused(session):
        session.add(User(email="priya@campusconnect.example", name="P", role=Role.TEAMMATE, password_hash="x"))


def test_unknown_enum_values_are_refused_and_values_are_stored_lowercase(session: Session) -> None:
    with pytest.raises(StatementError):
        with session.begin_nested():
            session.add(User(email="a@campusconnect.example", name="A", role="boss", password_hash="x"))
            session.flush()
    make_user(session, email="b@campusconnect.example", role=Role.ADMIN)
    assert session.scalar(text("SELECT role FROM users WHERE email = 'b@campusconnect.example'")) == "admin"


# --- sources and chunks ---------------------------------------------------------------------------

def test_source_clearance_fails_closed_in_the_orm_and_in_the_database(session: Session) -> None:
    project = make_project(session)
    values = {"project_id": project.id, "kind": SourceKind.NOTES, "name": "n", "text": "t",
              "content_sha256": "a" * 64, "byte_size": 1}
    orm_source = Source(**values)
    session.add(orm_source)
    session.flush()
    assert orm_source.clearance == int(Clearance.RESTRICTED)
    session.execute(
        text("INSERT INTO sources (project_id, kind, name, text, content_sha256, byte_size, status) "
             "VALUES (:p, 'notes', 'raw', 't', :h, 1, 'ingested')"),
        {"p": project.id, "h": "b" * 64},
    )
    assert session.scalar(text("SELECT clearance FROM sources WHERE name = 'raw'")) == 3
    assert session.scalar(text("SELECT redaction_counts FROM sources WHERE name = 'raw'")) in ("{}", {})


@pytest.mark.parametrize("level", [-1, 4])
def test_source_clearance_outside_the_levels_is_refused(session: Session, level: int) -> None:
    project = make_project(session)
    with refused(session):
        make_source(session, project, clearance=level)


def test_identical_content_is_ingested_once_per_project(session: Session) -> None:
    first, second = make_project(session), make_project(session, slug="other")
    make_source(session, first, name="a.md", content_sha256="c" * 64)
    with refused(session):
        make_source(session, first, name="renamed.md", content_sha256="c" * 64)
    make_source(session, second, name="a.md", content_sha256="c" * 64)  # another project is fine


def test_a_failed_source_needs_an_error_and_an_ingested_one_needs_text(session: Session) -> None:
    project = make_project(session)
    with refused(session):
        make_source(session, project, name="bad.bin", status=SourceStatus.FAILED, text="", error=None)
    make_source(session, project, name="bad2.bin", status=SourceStatus.FAILED, text="", error="Unreadable file")
    with refused(session):
        make_source(session, project, name="empty.md", status=SourceStatus.INGESTED, text="")


def test_chunk_ordinals_are_unique_per_source_and_chunks_are_not_blank(session: Session) -> None:
    source = make_source(session, make_project(session))
    make_chunk(session, source, ordinal=0)
    with refused(session):
        make_chunk(session, source, ordinal=0)
    with refused(session):
        make_chunk(session, source, ordinal=1, body="   ")
    with refused(session):
        make_chunk(session, source, ordinal=-1)


def test_deleting_a_source_deletes_its_chunks(session: Session) -> None:
    source = make_source(session, make_project(session))
    make_chunk(session, source, 0)
    make_chunk(session, source, 1)
    session.delete(source)
    session.flush()
    assert count(session, Chunk) == 0


# --- claims and evidence --------------------------------------------------------------------------

def test_claim_defaults_and_a_label_is_always_required(session: Session) -> None:
    project = make_project(session)
    claim = make_claim(session, project)
    assert (claim.version, claim.review_status) == (1, ReviewStatus.PROPOSED)
    with refused(session):
        session.add(Claim(project_id=project.id, kind=ClaimKind.FACT, subject="s", attribute="a", statement="x"))
    with pytest.raises(StatementError):
        with session.begin_nested():
            make_claim(session, project, label="certain")


def test_evidence_must_have_a_quote_and_starts_unverified(session: Session) -> None:
    project = make_project(session)
    chunk = make_chunk(session, make_source(session, project))
    claim = make_claim(session, project)
    assert make_evidence(session, claim, chunk).verified is False
    with refused(session):
        make_evidence(session, claim, chunk, quote="  ")


def test_a_chunk_that_backs_evidence_cannot_be_deleted(session: Session) -> None:
    project = make_project(session)
    source = make_source(session, project)
    chunk = make_chunk(session, source)
    make_evidence(session, make_claim(session, project), chunk)
    with refused(session):
        session.execute(delete(Chunk).where(Chunk.id == chunk.id))
    with refused(session):
        session.execute(delete(Source).where(Source.id == source.id))
    assert count(session, Chunk) == 1


def test_deleting_a_claim_deletes_its_evidence_and_versions(session: Session) -> None:
    project = make_project(session)
    chunk = make_chunk(session, make_source(session, project))
    claim = make_claim(session, project)
    make_evidence(session, claim, chunk)
    session.add(ClaimVersion(claim_id=claim.id, version=1, action=VersionAction.CREATED,
                             statement=claim.statement, review_status=ReviewStatus.PROPOSED))
    session.flush()
    session.execute(delete(Claim).where(Claim.id == claim.id))
    assert (count(session, Evidence), count(session, ClaimVersion), count(session, Chunk)) == (0, 0, 1)


def test_claim_version_numbers_are_unique_per_claim(session: Session) -> None:
    project = make_project(session)
    claim, other = make_claim(session, project), make_claim(session, project, attribute="other")

    def version(target: Claim, number: int) -> ClaimVersion:
        return ClaimVersion(claim_id=target.id, version=number, action=VersionAction.EDITED,
                            statement="s", review_status=ReviewStatus.PROPOSED)

    session.add_all([version(claim, 1), version(claim, 2), version(other, 1)])
    session.flush()
    with refused(session):
        session.add(version(claim, 2))


def test_claims_in_a_conflict_are_grouped_and_a_resolved_conflict_needs_a_timestamp(session: Session) -> None:
    project = make_project(session)
    conflict = Conflict(project_id=project.id, subject="campus-api", attribute="rate-limit")
    session.add(conflict)
    session.flush()
    make_claim(session, project, conflict_id=conflict.id, label=Label.CONFLICTING)
    make_claim(session, project, conflict_id=conflict.id, label=Label.CONFLICTING, value="60")
    session.refresh(conflict)
    assert len(conflict.claims) == 2 and conflict.status is ConflictStatus.OPEN
    with refused(session):
        session.execute(text("UPDATE conflicts SET status = 'resolved' WHERE id = :i"), {"i": conflict.id})


# --- interview, jobs, handover, audit -------------------------------------------------------------

def test_a_question_needs_a_target_and_an_answer_once_answered(session: Session) -> None:
    project = make_project(session)
    claim = make_claim(session, project)
    with refused(session):
        session.add(Question(project_id=project.id, kind=QuestionKind.MISSING, text="Who owns it?"))
    with refused(session):
        session.add(Question(project_id=project.id, claim_id=claim.id, kind=QuestionKind.MISSING,
                             text="Who owns it?", status=QuestionStatus.ANSWERED))
    session.add(Question(project_id=project.id, claim_id=claim.id, kind=QuestionKind.MISSING,
                         text="Who owns it?", status=QuestionStatus.ANSWERED, answer="Nobody."))
    session.flush()


def test_job_defaults_and_a_failed_job_must_explain_itself(session: Session) -> None:
    project = make_project(session)
    job = Job(project_id=project.id)
    session.add(job)
    session.flush()
    session.refresh(job)
    assert uuid.UUID(job.id).version == 4
    assert (job.kind, job.status, job.created_at is not None) == ("pipeline", JobStatus.QUEUED, True)
    with refused(session):
        session.add(Job(project_id=project.id, status=JobStatus.FAILED, error=None))
    session.add(Job(project_id=project.id, status=JobStatus.FAILED, error="The language model timed out."))
    session.flush()


def test_acknowledgement_is_unique_per_view(session: Session) -> None:
    project, user = make_project(session), make_user(session)

    def ack(digest: str) -> Acknowledgement:
        return Acknowledgement(project_id=project.id, user_id=user.id, role=Role.SUCCESSOR,
                               view_sha256=digest, visible_count=12, withheld_count=3)

    session.add_all([ack("a" * 64), ack("b" * 64)])  # a changed view can be acknowledged again
    session.flush()
    with refused(session):
        session.add(ack("a" * 64))
    with refused(session):
        session.add(Acknowledgement(project_id=project.id, user_id=user.id, role=Role.TEAMMATE,
                                    view_sha256="c" * 64, visible_count=-1, withheld_count=0))


def test_audit_log_outlives_the_user_and_the_project(session: Session) -> None:
    project, user = make_project(session), make_user(session)
    session.add(AuditLog(project_id=project.id, actor_id=user.id, actor_label=user.email, action="claim.accept"))
    session.flush()
    session.execute(delete(User).where(User.id == user.id))
    session.execute(delete(Project).where(Project.id == project.id))
    row = session.execute(select(AuditLog.actor_id, AuditLog.project_id, AuditLog.actor_label)).one()
    assert tuple(row) == (None, None, "priya@campusconnect.example")


def test_deleting_a_project_removes_everything_under_it(session: Session) -> None:
    project, user = make_project(session), make_user(session)
    source = make_source(session, project)
    chunk = make_chunk(session, source)
    job = Job(project_id=project.id, created_by_id=user.id)
    conflict = Conflict(project_id=project.id, subject="s", attribute="a")
    session.add_all([job, conflict])
    session.flush()
    claim = make_claim(session, project, job_id=job.id, conflict_id=conflict.id)
    make_evidence(session, claim, chunk)
    session.add_all([
        ClaimVersion(claim_id=claim.id, version=1, action=VersionAction.CREATED, statement="s",
                     review_status=ReviewStatus.PROPOSED),
        Question(project_id=project.id, conflict_id=conflict.id, kind=QuestionKind.CONFLICT, text="Which one?",
                 answer_source_id=source.id),
        Acknowledgement(project_id=project.id, user_id=user.id, role=Role.SUCCESSOR, view_sha256="d" * 64,
                        visible_count=1, withheld_count=0),
    ])
    session.flush()
    session.execute(delete(Project).where(Project.id == project.id))
    for model in (Source, Chunk, Job, Conflict, Claim, Evidence, ClaimVersion, Question, Acknowledgement):
        assert count(session, model) == 0, model.__name__
    assert count(session, User) == 1
