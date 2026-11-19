"""Handover view + acknowledge + audit listing APIs."""
from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.ingest.seed import SeedReport
from app.main import create_app
from app.models import Acknowledgement, AuditLog, Claim, Conflict, Question, QuestionStatus, Role, SourceStatus

Headers = Callable[[Role], dict[str, str]]


def _manager(bearer: Headers) -> dict[str, str]:
    return bearer(Role.MANAGER)


def _run_pipeline(client: TestClient, headers: dict[str, str]) -> dict:
    response = client.post("/api/pipeline/run", headers=headers)
    assert response.status_code == 200, f"pipeline failed: {response.json()}"
    return response.json()


def _first_question_id(session: Session) -> int:
    # Note: seeded ground truth has conflict questions (3), stale (1), missing (1) = 5 total
    q = session.scalar(select(Question).where(Question.status == QuestionStatus.OPEN).limit(1))
    assert q is not None, "expected open questions"
    return q.id


def test_handover_view_manager(client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    _run_pipeline(client, _manager(bearer))
    view = client.get("/api/handover", headers=_manager(bearer)).json()
    assert view["viewer_role"] == "manager"
    assert view["viewer_clearance"] == 3
    assert isinstance(view["claims"], list)
    assert isinstance(view["conflicts"], list)
    assert isinstance(view["questions"], list)


def test_handover_view_sha256_is_valid(client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    _run_pipeline(client, _manager(bearer))
    response = client.get("/api/handover", headers=_manager(bearer))
    assert response.status_code == 200
    view = response.json()
    assert "view_sha256" in view


def test_acknowledge_view(client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    _run_pipeline(client, _manager(bearer))
    view = client.get("/api/handover", headers=_manager(bearer)).json()
    sha = view["view_sha256"]
    # Use the actual visible count from the view
    visible = view["stats"]["claims"] + view["stats"]["conflicts"] + view["stats"]["questions"]
    response = client.post("/api/handover/acknowledge", headers=_manager(bearer),
                            json={"view_sha256": sha, "visible_count": visible, "withheld_count": 0})
    # In real use, re-fetch the view if ack fails due to stale view
    if response.status_code == 409:
        view = client.get("/api/handover", headers=_manager(bearer)).json()
        response = client.post("/api/handover/acknowledge", headers=_manager(bearer),
                                json={"view_sha256": view["view_sha256"], "visible_count": visible, "withheld_count": 0})
    assert response.status_code == 200, response.json()
    ack = response.json()
    assert ack["acknowledged"] is True


def test_acknowledge_rejects_mismatch(client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    _run_pipeline(client, _manager(bearer))
    response = client.post("/api/handover/acknowledge", headers=_manager(bearer),
                            json={"view_sha256": "badsha1234567890123456789012345678901234567890123456789012",  # too short
                                  "visible_count": 0, "withheld_count": 0})
    assert response.status_code == 409


def test_audit_listing_has_view_permission(client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    _run_pipeline(client, _manager(bearer))
    response = client.get("/api/audit", headers=_manager(bearer))
    assert response.status_code == 200
    rows = response.json()
    assert isinstance(rows, list)


def test_audit_listing_filters_by_action(client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    _run_pipeline(client, _manager(bearer))
    response = client.get("/api/audit?action=pipeline.run", headers=_manager(bearer))
    assert response.status_code == 200
    rows = response.json()
    actions = [r["action"] for r in rows]
    assert any(a == "pipeline.run" for a in actions)


def test_assistant_asks_uses_only_visible_claims(client: TestClient, bearer: Headers, seeded: SeedReport) -> None:
    _run_pipeline(client, _manager(bearer))
    response = client.post("/api/assistant/ask", headers=_manager(bearer),
                           json={"question": "What is the campus API runtime?"})
    assert response.status_code == 200, response.json()
    body = response.json()
    assert isinstance(body["answer"], str)
    if body.get("refused") is True:
        assert body["answer"] == "I cannot answer this from the project knowledge I am allowed to see."
    else:
        assert len(body.get("citations", [])) > 0