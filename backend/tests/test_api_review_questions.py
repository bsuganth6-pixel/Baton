"""Review and question APIs: accept/reject/edit claims, answer questions."""
from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.ingest.seed import SeedReport
from app.main import create_app
from app.models import AuditLog, Claim, Question, QuestionStatus, ReviewStatus, Role, VersionAction

Headers = Callable[[Role], dict[str, str]]


def _manager(bearer: Headers) -> dict[str, str]:
    return bearer(Role.MANAGER)


def _leaver(bearer: Headers) -> dict[str, str]:
    return bearer(Role.LEAVER)


def _run_pipeline(client: TestClient, headers: dict[str, str]) -> dict:
    response = client.post("/api/pipeline/run", headers=headers)
    assert response.status_code == 200
    return response.json()


def _get_claim_id(db: Session) -> int:
    claim = db.scalars(select(Claim).limit(1)).first()
    assert claim is not None, "no claims seeded"
    return claim.id


def test_review_requires_review_permission(client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    for role in (Role.TEAMMATE, Role.SUCCESSOR):
        response = client.patch("/api/review/claims/1", json={"statement": "x"}, headers=bearer(role))
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"


def test_accept_marks_claim_accepted(session: Session, client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    _run_pipeline(client, _manager(bearer))
    claim_id = _get_claim_id(session)
    response = client.post(f"/api/review/claims/{claim_id}/accept", headers=_manager(bearer))
    assert response.status_code == 200, response.json()
    assert response.json()["claim"]["review_status"] == "accepted"


def test_reject_marks_claim_rejected(session: Session, client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    _run_pipeline(client, _manager(bearer))
    claim_id = _get_claim_id(session)
    response = client.post(f"/api/review/claims/{claim_id}/reject", headers=_manager(bearer))
    assert response.status_code == 200, response.json()
    assert response.json()["claim"]["review_status"] == "rejected"


def test_edit_requires_change(session: Session, client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    _run_pipeline(client, _manager(bearer))
    claim = session.scalars(select(Claim).limit(1)).first()
    claim_id = claim.id
    response = client.patch(f"/api/review/claims/{claim_id}", json={"statement": claim.statement},
                            headers=_manager(bearer))
    assert response.status_code == 422
    response = client.patch(f"/api/review/claims/{claim_id}", json={"statement": "New text."}, headers=_manager(bearer))
    assert response.status_code == 200
    assert response.json()["claim"]["statement"] == "New text."


def test_version_increments_on_edit(session: Session, client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    _run_pipeline(client, _manager(bearer))
    claim = session.scalars(select(Claim).limit(1)).first()
    claim_id = claim.id
    v0 = claim.version
    client.patch(f"/api/review/claims/{claim_id}", json={"statement": "Different."}, headers=_manager(bearer))
    session.rollback()  # end this session's read snapshot so it sees the API session's committed bump
    refreshed = session.get(Claim, claim_id)
    assert refreshed.version == v0 + 1


def test_question_answer_requires_answer_permission(client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    for role in (Role.TEAMMATE, Role.SUCCESSOR):
        response = client.post("/api/questions/1/answer", json={"answer": "x"}, headers=bearer(role))
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"


def test_answer_then_question_is_answered(session: Session, client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    _run_pipeline(client, _manager(bearer))
    open_q = session.scalars(select(Question).where(Question.status == QuestionStatus.OPEN)).first()
    if open_q is None:
        pytest.skip("no open questions in seeded demo")
    answer = client.post(f"/api/questions/{open_q.id}/answer", json={"answer": "Yes, it is true."},
                         headers=_manager(bearer))
    assert answer.status_code == 200, answer.json()
    body = answer.json()["question"]
    assert body["status"] == "answered"
    assert body["answer"] == "Yes, it is true."


def test_audit_log_records_pipeline_run(session: Session, client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    _run_pipeline(client, _manager(bearer))
    rows = session.scalars(select(AuditLog)).all()
    actions = [r.action for r in rows]
    assert "pipeline.run" in actions


def test_review_list_claims(session: Session, client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    _run_pipeline(client, _manager(bearer))
    response = client.get("/api/review/claims", headers=_manager(bearer))
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    for claim in data:
        assert set(claim.keys()).issuperset({"id", "kind", "subject", "attribute", "value", "statement", "label", "review_status", "version", "conflict_id", "required_clearance", "evidence", "created_at", "updated_at"})