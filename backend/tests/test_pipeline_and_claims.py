"""Integration tests for the full pipeline: run → extract → verify → classify → store."""
from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.ingest.seed import SeedReport
from app.main import create_app
from app.models import Claim, Conflict, Evidence, Job, JobStatus, Label, Project, Question, QuestionStatus, Role

Headers = Callable[[Role], dict[str, str]]


def _admin(bearer: Headers) -> dict[str, str]:
    return bearer(Role.ADMIN)


def test_pipeline_runs_on_seeded_data(client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    response = client.post("/api/pipeline/run", headers=_admin(bearer))
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["claims"] == 22
    assert body["conflicts"] == 3
    assert body["labels"]["conflicting"] == 6
    assert body["labels"]["stale"] == 1
    assert body["labels"]["missing"] == 1
    assert body["dropped"] == 0


def test_pipeline_idempotent_unless_force(client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    client.post("/api/pipeline/run", headers=_admin(bearer))
    again = client.post("/api/pipeline/run", headers=_admin(bearer))
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "pipeline_already_ran"
    forced = client.post("/api/pipeline/run", headers=_admin(bearer), json={"force": True})
    assert forced.status_code == 200


def test_jobs_list_shows_the_pipeline_job(client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    client.post("/api/pipeline/run", headers=_admin(bearer))
    listed = client.get("/api/pipeline/jobs", headers=_admin(bearer)).json()
    pipeline_jobs = [j for j in listed if j["kind"] == "pipeline"]
    assert pipeline_jobs[0]["status"] == "succeeded"
    detail = client.get(f"/api/pipeline/jobs/{pipeline_jobs[0]['id']}", headers=_admin(bearer)).json()
    assert detail["result"]["claims"] == 22


def test_pipeline_creates_correct_labels_and_counts(session: Session, client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    client.post("/api/pipeline/run", headers=_admin(bearer))
    claims = session.scalars(select(Claim)).all()
    assert len(claims) == 22
    by_label = {}
    for c in claims:
        by_label[c.label.value] = by_label.get(c.label.value, 0) + 1
    assert by_label.get("conflicting", 0) == 6
    assert by_label.get("stale", 0) == 1
    assert by_label.get("missing", 0) == 1


def test_pipeline_creates_conflicts_and_questions(session: Session, client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    client.post("/api/pipeline/run", headers=_admin(bearer))
    conflicts = session.scalars(select(Conflict)).all()
    assert len(conflicts) == 3
    questions = session.scalars(select(Question)).all()
    assert all(q.status == QuestionStatus.OPEN for q in questions)


def test_pipeline_creates_verified_evidence(session: Session, client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    client.post("/api/pipeline/run", headers=_admin(bearer))
    evidence = session.scalars(select(Evidence)).all()
    assert len(evidence) > 0
    for e in evidence:
        assert e.verified is True
        assert e.quote in e.chunk.text


def test_stale_label_applies_to_old_evidence(session: Session, client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    client.post("/api/pipeline/run", headers=_admin(bearer))
    stale_claims = session.scalars(select(Claim).where(Claim.label == Label.STALE)).all()
    assert len(stale_claims) == 1


def test_missing_label_for_absent_value(session: Session, client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    client.post("/api/pipeline/run", headers=_admin(bearer))
    missing_claims = session.scalars(select(Claim).where(Claim.label == Label.MISSING)).all()
    assert len(missing_claims) == 1


def test_conflicting_label_for_distinct_values(session: Session, client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    client.post("/api/pipeline/run", headers=_admin(bearer))
    conflicting = session.scalars(select(Claim).where(Claim.label == Label.CONFLICTING)).all()
    assert len(conflicting) == 6


def test_supported_label_when_two_sources_agree(session: Session, client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    client.post("/api/pipeline/run", headers=_admin(bearer))
    supported = session.scalars(select(Claim).where(Claim.label == Label.SUPPORTED)).all()
    assert len(supported) > 0


def test_decoy_values_do_not_create_conflicts(session: Session, client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    client.post("/api/pipeline/run", headers=_admin(bearer))
    node_claims = session.scalars(select(Claim).where(Claim.subject == "campus-api", Claim.attribute == "runtime")).all()
    assert len(node_claims) == 1
    tls_claims = session.scalars(select(Claim).where(Claim.subject == "tls-certificates", Claim.attribute == "renewal-interval")).all()
    assert len(tls_claims) == 1


def test_pipeline_result_summary_matches_db_counts(client: TestClient, bearer: Headers, seeded: SeedReport, session: Session) -> None:
    body = client.post("/api/pipeline/run", headers=_admin(bearer)).json()
    assert body["claims"] == len(session.scalars(select(Claim)).all())
    assert body["conflicts"] == len(session.scalars(select(Conflict)).all())
    assert body["evidence"] == len(session.scalars(select(Evidence)).all())


def test_claims_start_as_proposed(session: Session, client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    client.post("/api/pipeline/run", headers=_admin(bearer))
    claims = session.scalars(select(Claim)).all()
    assert all(c.review_status.value == "proposed" for c in claims)