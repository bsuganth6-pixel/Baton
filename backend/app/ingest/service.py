"""Ingest one uploaded file: validate, read, extract, redact, chunk, store. Originals are never persisted."""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit
from app.ingest.extract import ExtractionError, Segment, decode_text, detect_kind, extract_segments, fallback_kind
from app.ingest.validate import IngestError, ValidatedUpload, clean_filename, validate_upload
from app.models import Chunk, Clearance, Project, Source, SourceKind, SourceStatus, User
from app.protect.redact import redact

log = logging.getLogger("baton.ingest")
DEFAULT_PROJECT_SLUG = "default"


@dataclass
class IngestOutcome:
    source: Source
    created: bool


def find_project(db: Session) -> Project | None:
    return db.scalar(select(Project).order_by(Project.id).limit(1))


def get_or_create_project(db: Session) -> Project:
    project = find_project(db)
    if project is None:
        project = Project(slug=DEFAULT_PROJECT_SLUG, name="Handover project")
        db.add(project)
        db.flush()
    return project


def _existing(db: Session, project_id: int, sha256: str) -> Source | None:
    return db.scalar(select(Source).where(Source.project_id == project_id, Source.content_sha256 == sha256))


def _chunks(segments: list[Segment]) -> tuple[list[Chunk], Counter[str]]:
    """Redact every segment and turn it into a chunk. Nothing unredacted leaves this function."""
    counts: Counter[str] = Counter()
    chunks: list[Chunk] = []
    for segment in segments:
        body = redact(segment.text)
        if not body.text.strip():
            continue
        counts.update(body.counts)
        locator = redact(segment.locator).text[:255] if segment.locator else None
        author = redact(segment.author).text[:120] if segment.author else None
        chunks.append(Chunk(ordinal=len(chunks), text=body.text, locator=locator, author=author,
                            occurred_at=segment.occurred_at))
    return chunks, counts


def _source(project: Project, upload: ValidatedUpload, name: str, clearance: int, kind: SourceKind,
            actor: User | None, **fields: object) -> Source:
    return Source(project_id=project.id, kind=kind, name=name, clearance=clearance,
                  content_sha256=upload.sha256, byte_size=upload.size,
                  created_by_id=actor.id if actor else None, **fields)


def ingest_upload(
    db: Session,
    *,
    project: Project,
    filename: str | None,
    data: bytes,
    clearance: int,
    max_bytes: int,
    kind: SourceKind | None = None,
    actor: User | None = None,
    display_name: str | None = None,
    audit_duplicates: bool = True,
) -> IngestOutcome:
    """Store a file as a redacted source. Raises IngestError for policy rejections (nothing is stored).

    A readable-but-unusable file (binary, malformed JSON or CSV) is stored as a failed source so the
    Sources page can show what is missing instead of hiding it.
    """
    if clearance not in {int(level) for level in Clearance}:
        raise IngestError("invalid_clearance", "Clearance must be between 0 and 3.", 422)
    upload = validate_upload(filename, data, max_bytes)

    def duplicate_of(source: Source) -> IngestOutcome:
        if audit_duplicates:
            audit.record(db, "source.duplicate", actor=actor, project_id=project.id, target_type="source",
                         target_id=source.id)
        return IngestOutcome(source, False)

    if (found := _existing(db, project.id, upload.sha256)) is not None:
        return duplicate_of(found)

    name = redact(clean_filename(display_name or upload.name)).text
    try:
        text = decode_text(data)
        resolved = kind or detect_kind(upload.extension, text)
        chunks, counts = _chunks(extract_segments(resolved, text))
        if not chunks:
            raise ExtractionError("No readable content was found in this file.")
        source = _source(project, upload, name, clearance, resolved, actor, status=SourceStatus.INGESTED,
                         text="\n\n".join(c.text for c in chunks), redaction_counts=dict(counts))
        source.chunks = chunks
        action, detail = "source.ingest", {"kind": resolved.value, "clearance": clearance, "chunks": len(chunks),
                                           "bytes": upload.size, "redactions": dict(counts)}
    except ExtractionError as exc:
        source = _source(project, upload, name, clearance, kind or fallback_kind(upload.extension), actor,
                         status=SourceStatus.FAILED, text="", error=exc.message, redaction_counts={})
        action, detail = "source.ingest_failed", {"clearance": clearance, "bytes": upload.size, "reason": exc.message}

    try:
        with db.begin_nested():
            db.add(source)
            db.flush()
    except IntegrityError:
        if (found := _existing(db, project.id, upload.sha256)) is None:
            raise  # not a duplicate: a real constraint failure must not be hidden
        return duplicate_of(found)  # lost a race with an identical upload
    audit.record(db, action, actor=actor, project_id=project.id, target_type="source", target_id=source.id,
                 detail=detail)
    log.info("%s source=%s kind=%s", action, source.id, source.kind.value)
    return IngestOutcome(source, True)
