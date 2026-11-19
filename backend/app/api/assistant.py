"""Successor assistant: answers only from claims the asker may see, with verified citations.

Every citation is re-checked here: the quote must exist verbatim in the cited chunk, and the chunk must
back a claim that is visible to the asker. Unverifiable answers are replaced by a refusal.
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import audit
from app.api.deps import current_user, get_db, get_settings_dep
from app.api.schemas import AnswerOut, AskIn, CitationOut
from app.claims import required_clearance, visible_claims
from app.config import Settings
from app.ingest.llm import AssistantClient, ExtractionError, client_from_settings
from app.models import Claim, Label, Question, ReviewStatus, User
from app.protect.acl import clearance_for

router = APIRouter(prefix="/api/assistant", tags=["assistant"])
log = logging.getLogger("baton.assistant")

REFUSAL = "I cannot answer this from the project knowledge I am allowed to see."
_MAX_CLAIMS = 6
_WORD = re.compile(r"[a-z0-9]{3,}")
_STOP = frozenset({
    "the", "and", "for", "was", "were", "are", "what", "who", "how", "when", "where", "which", "does", "did",
    "with", "from", "that", "this", "have", "has", "had", "about", "into", "over", "under", "there", "their",
})


def _tokens(text: str) -> set[str]:
    return {t for t in _WORD.findall(text.lower()) if t not in _STOP}


def _claim_context(claim: Claim) -> dict:
    return {"id": claim.id, "statement": claim.statement, "subject": claim.subject, "attribute": claim.attribute,
            "value": claim.value, "label": claim.label.value,
            "evidence": [{"chunk_id": e.chunk_id, "quote": e.quote} for e in claim.evidence if e.verified]}


def _rank(claims: list[Claim], question: str) -> list[Claim]:
    needle = _tokens(question)
    scored: list[tuple[int, Claim]] = []
    for claim in claims:
        if claim.review_status is ReviewStatus.REJECTED:
            continue
        haystack = _tokens(" ".join([claim.subject, claim.attribute, claim.value or "", claim.statement,
                                     *[e.quote for e in claim.evidence]]))
        score = len(needle & haystack)
        if score:
            scored.append((score, claim))
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [claim for _, claim in scored[:_MAX_CLAIMS]]


@router.post("/ask", response_model=AnswerOut)
def ask(body: AskIn, user: User = Depends(current_user), db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings_dep)) -> AnswerOut:
    clearance = clearance_for(user.role)
    matches = _rank(visible_claims(db, clearance), body.question)
    if not matches:
        return AnswerOut(answer=REFUSAL, citations=[], refused=True)

    client = client_from_settings(settings)
    if isinstance(client, AssistantClient):
        try:
            result = client.answer(body.question, [_claim_context(c) for c in matches])
            citations = _verified_citations(db, result.get("citations") or [], matches, clearance)
            if result.get("answer") and citations:
                audit.record(db, "assistant.answered", actor=user, project_id=matches[0].project_id,
                             target_type="assistant", detail={"claims": len(matches), "citations": len(citations),
                                                              "engine": "llm"})
                db.commit()
                return AnswerOut(answer=result["answer"], citations=citations)
            log.info("assistant: LLM answer rejected (no verifiable citations), using the deterministic answer")
        except ExtractionError as exc:
            log.warning("assistant: LLM unavailable (%s), using the deterministic answer", exc)

    answer, citations = _deterministic(db, matches, clearance)
    audit.record(db, "assistant.answered", actor=user, project_id=matches[0].project_id, target_type="assistant",
                 detail={"claims": len(matches), "citations": len(citations), "engine": "rules"})
    db.commit()
    return AnswerOut(answer=answer, citations=citations, refused=not citations)


def _verified_citations(db: Session, citations: list[dict], matches: list[Claim], clearance: int) -> list[CitationOut]:
    """Keep only citations that quote a real chunk behind a visible matched claim."""
    by_id = {claim.id: claim for claim in matches}
    out: list[CitationOut] = []
    seen: set[tuple[int, int, str]] = set()
    for item in citations:
        claim = by_id.get(int(item.get("claim_id", -1)))
        if claim is None or required_clearance(claim) > clearance:
            continue
        evidence = next((e for e in claim.evidence if e.chunk_id == item.get("chunk_id") and e.verified), None)
        quote = str(item.get("quote", ""))
        if evidence is None or not quote or quote not in evidence.chunk.text:
            continue
        key = (claim.id, evidence.chunk_id, quote)
        if key in seen:
            continue
        seen.add(key)
        out.append(_citation_out(claim, evidence))
    return out


def _deterministic(db: Session, matches: list[Claim], clearance: int) -> tuple[str, list[CitationOut]]:
    parts: list[str] = []
    citations: list[CitationOut] = []
    for claim in matches:
        if required_clearance(claim) > clearance:
            continue
        suffix = {"conflicting": " (sources conflict)", "missing": " (recorded as missing)",
                  "stale": " (evidence may be stale)"}.get(claim.label.value, "")
        parts.append(claim.statement + suffix)
        for evidence in claim.evidence:
            if evidence.verified and evidence.quote in evidence.chunk.text:
                citations.append(_citation_out(claim, evidence))
    if not parts:
        return REFUSAL, []
    return "Based on the project documents: " + " ".join(parts), citations


def _citation_out(claim: Claim, evidence) -> CitationOut:
    chunk = evidence.chunk
    return CitationOut(claim_id=claim.id, chunk_id=chunk.id, source_id=chunk.source_id,
                       source_name=chunk.source.name, locator=chunk.locator, quote=evidence.quote)
