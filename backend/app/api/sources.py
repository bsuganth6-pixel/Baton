"""Connect: upload sources, list them, read their chunks, and search. Everything is filtered by clearance."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.api.deps import current_user, get_db, get_settings_dep, require_permission
from app.api.errors import ApiError
from app.api.schemas import ChunkOut, SearchHit, SourceOut, UploadOut
from app.config import Settings
from app.ingest.service import find_project, get_or_create_project, ingest_upload
from app.ingest.validate import IngestError
from app.models import Chunk, Clearance, Role, Source, SourceKind, SourceStatus, User
from app.protect.acl import Permission, clearance_for

router = APIRouter(prefix="/api", tags=["sources"])
SNIPPET_RADIUS = 90


def _enforced_clearance(requested_clearance: int, user: User) -> int:
    """Enforce clearance rules based on role. Admin/Manager may assign explicit level.
    Leaver uploads must receive CONFIDENTIAL (2); lower values are raised up to 2.
    Successor uploads must receive CONFIDENTIAL (2); lower values are raised up to 2.
    Teammate uploads must receive INTERNAL (1); lower values are raised up to 1.
    Frontend-supplied clearance is never trusted without checking the user's role."""
    role_clearance_map = {
        Role.ADMIN: 3,
        Role.MANAGER: 3,
        Role.LEAVER: 2,     # leaver may assign up to CONFIDENTIAL; lower → raise to 2
        Role.SUCCESSOR: 2,  # successor may assign up to CONFIDENTIAL; lower → raise to 2
        Role.TEAMMATE: 1,   # teammate may assign up to INTERNAL; lower → raise to 1
    }
    if user.role in (Role.ADMIN, Role.MANAGER):
        return requested_clearance  # admin/manager retain explicit control
    max_allowed = role_clearance_map.get(user.role, 0)
    enforced = max(requested_clearance, max_allowed)
    return enforced


def _source_out(source: Source, chunk_count: int) -> SourceOut:
    return SourceOut(
        id=source.id, name=source.name, kind=source.kind, clearance=source.clearance,
        clearance_label=Clearance(source.clearance).name.lower(), status=source.status, error=source.error,
        byte_size=source.byte_size, chunk_count=chunk_count, redactions=source.redaction_counts or {},
        created_at=source.created_at,
    )


def _chunk_count(db: Session, source_id: int) -> int:
    return db.scalar(select(func.count(Chunk.id)).where(Chunk.source_id == source_id)) or 0


def _visible_source(db: Session, source_id: int, user: User) -> Source:
    """A source the user may see. A restricted one answers 404, so its existence is not revealed."""
    source = db.get(Source, source_id)
    if source is None or source.clearance > clearance_for(user.role):
        raise ApiError(404, "not_found", "Source not found.")
    return source


@router.get("/sources", response_model=list[SourceOut])
def list_sources(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[SourceOut]:
    project = find_project(db)
    if project is None:
        return []
    counts = select(func.count(Chunk.id)).where(Chunk.source_id == Source.id).correlate(Source).scalar_subquery()
    rows = db.execute(
        select(Source, counts)
        .where(Source.project_id == project.id, Source.clearance <= clearance_for(user.role))
        .order_by(Source.id)
    ).all()
    return [_source_out(source, count) for source, count in rows]


@router.post("/sources", response_model=UploadOut, status_code=201)
def upload_source(
    response: Response,
    file: UploadFile = File(...),
    clearance: int = Form(..., ge=0, le=3),
    kind: SourceKind | None = Form(None),
    user: User = Depends(require_permission(Permission.UPLOAD_SOURCES)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> UploadOut:
    clearance = _enforced_clearance(clearance, user)
    data = file.file.read(settings.max_upload_bytes + 1)  # never read more than the limit allows
    project = get_or_create_project(db)
    try:
        outcome = ingest_upload(db, project=project, filename=file.filename, data=data, clearance=clearance,
                                kind=kind, actor=user, max_bytes=settings.max_upload_bytes)
    except IngestError as exc:
        db.rollback()
        audit.record(db, "source.rejected", actor=user, detail={"reason": exc.code})
        db.commit()
        raise ApiError(exc.status, exc.code, exc.message) from exc
    source = outcome.source
    db.commit()
    if outcome.created and source.status is SourceStatus.FAILED:
        raise ApiError(422, "unreadable_content", source.error or "The file could not be read.",
                       details={"source_id": source.id})
    if outcome.created:
        return UploadOut(duplicate=False, message="Source added.", source=_source_out(source, len(source.chunks)))
    response.status_code = 200
    if source.clearance > clearance_for(user.role):  # do not describe a source the uploader may not see
        return UploadOut(duplicate=True, message="This file has already been ingested.", source=None)
    return UploadOut(duplicate=True, message="This file has already been ingested.",
                     source=_source_out(source, _chunk_count(db, source.id)))


@router.get("/sources/{source_id}", response_model=SourceOut)
def get_source(source_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> SourceOut:
    source = _visible_source(db, source_id, user)
    return _source_out(source, _chunk_count(db, source.id))


@router.get("/sources/{source_id}/chunks", response_model=list[ChunkOut])
def get_chunks(source_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[ChunkOut]:
    source = _visible_source(db, source_id, user)
    rows = db.scalars(select(Chunk).where(Chunk.source_id == source.id).order_by(Chunk.ordinal)).all()
    return [ChunkOut(id=c.id, ordinal=c.ordinal, text=c.text, locator=c.locator, author=c.author,
                     occurred_at=c.occurred_at) for c in rows]


def _snippet(text: str, needle: str) -> str:
    at = text.lower().find(needle)
    start = max(0, at - SNIPPET_RADIUS)
    end = min(len(text), at + len(needle) + SNIPPET_RADIUS)
    return ("..." if start else "") + text[start:end].replace("\n", " ").strip() + ("..." if end < len(text) else "")


@router.get("/search", response_model=list[SearchHit])
def search(
    q: str = Query(..., min_length=2, max_length=200), limit: int = Query(20, ge=1, le=50),
    user: User = Depends(current_user), db: Session = Depends(get_db),
) -> list[SearchHit]:
    needle = q.strip().lower()
    if len(needle) < 2:
        raise ApiError(422, "query_too_short", "Type at least two characters to search.")
    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = db.execute(
        select(Chunk, Source).join(Source, Source.id == Chunk.source_id)
        .where(func.lower(Chunk.text).like(f"%{escaped}%", escape="\\"), Source.status == SourceStatus.INGESTED,
               Source.clearance <= clearance_for(user.role))
        .order_by(Source.id, Chunk.ordinal).limit(limit)
    ).all()
    return [SearchHit(chunk_id=c.id, source_id=s.id, source_name=s.name, locator=c.locator, author=c.author,
                      occurred_at=c.occurred_at, snippet=_snippet(c.text, needle)) for c, s in rows]
