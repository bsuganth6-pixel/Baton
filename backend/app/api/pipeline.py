"""The extraction pipeline: load visible chunks, extract claims, verify quotes, classify, store.

Every claim lands with at least one evidence quote the server has checked is an exact substring of the
cited chunk. Claims whose quotes do not verify are dropped and counted. Labels come from confidence.py,
never from the model. Conflicts and open questions are created from the same rules.
"""
from __future__ import annotations

import logging
from datetime import UTC, date

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.api.deps import current_user, get_db, require_permission
from app.api.errors import ApiError
from app.api.schemas import JobOut, PipelineResultOut, PipelineRunIn
from app.config import Settings
from app.ingest.confidence import Candidate, EvidenceRef, classify, distinct_values, merge_candidates
from app.ingest.llm import ChunkView, ExtractionError, RuleBasedClient, client_from_settings
from app.models import (Chunk, Claim, ClaimKind, ClaimVersion, Conflict, Evidence, Job, JobStatus,
                          Project, Question, QuestionKind, QuestionStatus, ReviewStatus, Source,
                          SourceStatus, VersionAction, User)
from app.protect.acl import Permission, clearance_for

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])
log = logging.getLogger("baton.pipeline")


def _job_out(job: Job) -> JobOut:
    return JobOut(id=job.id, kind=job.kind, status=job.status, stage=job.stage, error=job.error,
                  result=job.result, created_at=job.created_at, started_at=job.started_at,
                  finished_at=job.finished_at)


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[JobOut]:
    rows = db.scalars(select(Job).order_by(Job.created_at.desc()).limit(50)).all()
    return [_job_out(job) for job in rows]


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> JobOut:
    job = db.get(Job, job_id)
    if job is None:
        raise ApiError(404, "not_found", "Job not found.")
    return _job_out(job)


@router.post("/run", response_model=PipelineResultOut)
def run_pipeline(
    body: PipelineRunIn | None = None,
    user: User = Depends(require_permission(Permission.RUN_PIPELINE)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(lambda: Settings()),
) -> PipelineResultOut:
    body = body or PipelineRunIn()
    project = db.scalar(select(Project).order_by(Project.id).limit(1))
    if project is None:
        raise ApiError(404, "no_project", "Upload a source first so the pipeline has something to read.")

    already = db.scalar(select(Job).where(Job.kind == "pipeline", Job.status == JobStatus.SUCCEEDED))
    if already is not None and not body.force:
        raise ApiError(409, "pipeline_already_ran",
                       "The pipeline has already run. Use force=true to extract again.",
                       details={"job_id": already.id})

    job = Job(id=_new_job_id(), project_id=project.id, kind="pipeline", status=JobStatus.RUNNING,
              stage="loading", created_by_id=user.id)
    db.add(job)
    db.commit()

    try:
        result = _run(db, job, project, user, settings)
    except Exception as exc:  # fail the job visibly; never leak the traceback to the client
        log.error("pipeline failed on job %s", job.id, exc_info=exc)
        job.status, job.stage, job.error = JobStatus.FAILED, "failed", "Extraction failed."
        job.result = {"engine": "failed", "claims": 0, "evidence": 0, "conflicts": 0,
                      "questions": 0, "dropped": 0, "labels": {}}
        db.commit()
        raise ApiError(500, "pipeline_failed", "Extraction failed. The server log has the details.") from exc
    return result


def _new_job_id() -> str:
    import uuid
    return str(uuid.uuid4())


def _run(db: Session, job: Job, project: Project, user: User, settings: Settings) -> PipelineResultOut:
    # 1. LOAD: chunks from ingested sources within the runner's clearance.
    job.stage = "loading"
    db.commit()
    chunks = _visible_chunks(db, user)
    if not chunks:
        raise ApiError(409, "nothing_to_extract", "No ingested chunks are available at your clearance.")

    # 2. EXTRACT: the model (or the deterministic fallback) proposes claims with citations.
    job.stage = "extracting"
    db.commit()
    client = client_from_settings(settings)
    engine = "rules" if isinstance(client, RuleBasedClient) else "llm"
    try:
        extracted = client.extract(chunks)
    except ExtractionError as exc:
        log.warning("pipeline: LLM extraction failed (%s), falling back to the deterministic extractor", exc)
        extracted = []
    if not extracted and not isinstance(client, RuleBasedClient):
        # The model is unreachable, over quota, slow, or answered with unusable output.
        # The deterministic extractor keeps the workflow functional; it never invents text.
        log.warning("pipeline: LLM returned no usable claims, falling back to the deterministic extractor")
        extracted = RuleBasedClient().extract(chunks)
        engine = "rules"
    log.info("pipeline job=%s engine=%s extracted=%d", job.id, engine, len(extracted))

    # 3. VERIFY: every citation must point at a chunk the runner may see AND be an exact substring
    #    of that chunk, or the claim is dropped. Model-supplied chunk ids are never trusted directly.
    job.stage = "verifying"
    db.commit()
    visible_ids = {chunk.chunk_id for chunk in chunks}
    candidates, dropped = _verify(db, extracted, visible_ids)
    candidates = merge_candidates(candidates)

    # 4. CLASSIFY: deterministic labels from the confidence rules.
    job.stage = "classifying"
    db.commit()
    as_of = project.as_of or date.today()
    labels = classify(candidates, as_of, settings.stale_after_days)

    # 5. STORE: conflicts, claims, evidence, versions, questions.
    job.stage = "storing"
    db.commit()
    counts = _store(db, job, project, candidates, labels, user, settings)

    job.status, job.stage, job.finished_at = JobStatus.SUCCEEDED, "done", _utcnow()
    job.result = {"engine": engine, "claims": counts["claims"], "evidence": counts["evidence"],
                  "conflicts": counts["conflicts"], "questions": counts["questions"], "dropped": dropped,
                  "labels": counts["labels"]}
    db.commit()

    audit.record(db, "pipeline.run", actor=user, project_id=project.id, target_type="job", target_id=job.id,
                 detail=job.result)
    db.commit()

    return PipelineResultOut(job_id=job.id, status=job.status.value, engine=engine, claims=counts["claims"],
                             evidence=counts["evidence"], conflicts=counts["conflicts"],
                             questions=counts["questions"], dropped=dropped, labels=counts["labels"])


def _visible_chunks(db: Session, user: User) -> list[ChunkView]:
    clearance = clearance_for(user.role)
    rows = db.execute(
        select(Chunk, Source)
        .join(Source, Source.id == Chunk.source_id)
        .where(Source.status == SourceStatus.INGESTED, Source.clearance <= clearance)
        .order_by(Source.id, Chunk.ordinal)
    ).all()
    return [ChunkView(chunk_id=chunk.id, source_id=source.id, text=chunk.text, locator=chunk.locator,
                      occurred_at=chunk.occurred_at) for chunk, source in rows]


def _verify(db: Session, extracted: list, visible_chunk_ids: set[int]) -> tuple[list[Candidate], int]:
    """Keep citations whose quote appears verbatim in a VISIBLE chunk; drop claims left with none.

    Model-supplied chunk_ids are validated against the visible set — a malicious or hallucinated
    citation outside the runner's clearance is ignored.
    """
    candidates: list[Candidate] = []
    dropped = 0
    for claim in extracted:
        refs: list[EvidenceRef] = []
        for citation in claim.citations:
            if citation.chunk_id not in visible_chunk_ids:
                log.info("pipeline: ignoring citation to non-visible chunk %s", citation.chunk_id)
                continue
            chunk = db.get(Chunk, citation.chunk_id)
            if chunk is None:
                continue
            if citation.quote not in chunk.text:
                log.info("pipeline: quote does not verify against chunk %s: %r", citation.chunk_id,
                         citation.quote[:80])
                continue
            refs.append(EvidenceRef(chunk_id=chunk.id, source_id=chunk.source_id, quote=citation.quote,
                                    occurred_at=chunk.occurred_at))
        if not refs:
            dropped += 1
            continue
        candidates.append(Candidate(key=(claim.subject, claim.attribute), kind=claim.kind, value=claim.value,
                                    statement=claim.statement, evidence=refs))
    return candidates, dropped


def _store(db: Session, job: Job, project: Project, candidates: list[Candidate], labels: dict[int, str],
           user: User, settings: Settings) -> dict[str, int]:
    from app.ingest.confidence import Label

    groups: dict[tuple[str, str], list[Candidate]] = {}
    for candidate in candidates:
        groups.setdefault(candidate.key, []).append(candidate)

    conflicts = 0
    claims = 0
    evidence_rows = 0
    questions = 0
    label_counts: dict[str, int] = {}

    for key, group in groups.items():
        subject, attribute = key
        conflict: Conflict | None = None
        group_labels = {labels[id(c)] for c in group}
        if Label.CONFLICTING in group_labels:
            conflict = Conflict(project_id=project.id, subject=subject, attribute=attribute, status="open")
            db.add(conflict)
            db.flush()
            conflicts += 1
            audit.record(db, "conflict.created", actor=user, project_id=project.id, target_type="conflict",
                         target_id=conflict.id, detail={"subject": subject, "attribute": attribute})

        for candidate in group:
            label = Label(labels[id(candidate)])
            label_counts[label.value] = label_counts.get(label.value, 0) + 1
            statement = candidate.statement or _statement_for(candidate)
            claim = Claim(project_id=project.id, job_id=job.id, conflict_id=conflict.id if conflict else None,
                          kind=_claim_kind(candidate.kind), subject=subject, attribute=attribute,
                          value=candidate.value, statement=statement, label=label,
                          review_status=ReviewStatus.PROPOSED, version=1)
            db.add(claim)
            db.flush()
            claims += 1

            for ref in candidate.evidence:
                db.add(Evidence(claim_id=claim.id, chunk_id=ref.chunk_id, quote=ref.quote, verified=True))
                evidence_rows += 1
            db.add(ClaimVersion(claim_id=claim.id, version=1, action=VersionAction.CREATED, statement=statement,
                                value=candidate.value, review_status=ReviewStatus.PROPOSED,
                                actor_id=user.id))
            audit.record(db, "claim.created", actor=user, project_id=project.id, target_type="claim",
                         target_id=claim.id, detail={"subject": subject, "attribute": attribute, "label": label.value})

            question = _question_for(label, claim, conflict, subject, attribute, group, settings)
            if question is not None:
                db.add(question)
                questions += 1

    db.flush()
    return {"claims": claims, "evidence": evidence_rows, "conflicts": conflicts, "questions": questions,
            "labels": label_counts}


def _claim_kind(kind: str) -> ClaimKind:
    return ClaimKind(kind) if kind in {k.value for k in ClaimKind} else ClaimKind.FACT


def _statement_for(candidate: Candidate) -> str:
    if candidate.value is None:
        return f"No value is recorded for {candidate.key[0]} {candidate.key[1]}."
    return f"{candidate.key[0]} {candidate.key[1]} is {candidate.value}."


def _question_for(label, claim: Claim, conflict: Conflict | None, subject: str, attribute: str,
                  group: list[Candidate], settings: Settings) -> Question | None:
    if label.value == "conflicting" and conflict is not None:
        values = " vs ".join(sorted(distinct_values(group)))
        text = f"Sources disagree on {subject} {attribute}: {values}. Which value is correct?"
        return Question(project_id=claim.project_id, claim_id=claim.id, conflict_id=conflict.id,
                        kind=QuestionKind.CONFLICT, text=text, status=QuestionStatus.OPEN)
    if label.value == "stale":
        text = (f"Evidence for {subject} {attribute} is older than {settings.stale_after_days} days "
                f"before the project's as-of date. Is it still true?")
        return Question(project_id=claim.project_id, claim_id=claim.id, kind=QuestionKind.STALE, text=text,
                        status=QuestionStatus.OPEN)
    if label.value == "missing":
        if claim.kind is ClaimKind.OWNER:
            text = f"Nobody is recorded as owning {subject}. Who owns it now?"
        else:
            text = f"No value is recorded for {subject} {attribute}. What is it?"
        return Question(project_id=claim.project_id, claim_id=claim.id, kind=QuestionKind.MISSING, text=text,
                        status=QuestionStatus.OPEN)
    return None


def _utcnow():
    from datetime import datetime
    return datetime.now(UTC)
