"""Connect: uploading, listing, reading and searching sources, always filtered by the viewer's clearance."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.ingest.seed import SeedReport
from app.main import create_app
from app.models import AuditLog, Role, Source

Headers = Callable[[Role], dict[str, str]]
NOTES = b"# Notes\n\nThe API is rate limited to 100 requests per minute per user.\n"


def upload(client: TestClient, headers: dict[str, str], name: str = "notes.md", data: bytes = NOTES,
           clearance: int | str = 1, **form: str) -> Any:
    return client.post("/api/sources", headers=headers, files={"file": (name, data, "application/octet-stream")},
                       data={"clearance": str(clearance), **form})


# --- who may upload ----------------------------------------------------------------------------------------

@pytest.mark.parametrize(("role", "allowed"), [(Role.ADMIN, True), (Role.MANAGER, True), (Role.LEAVER, True),
                                               (Role.SUCCESSOR, False), (Role.TEAMMATE, False)])
def test_only_roles_with_the_permission_can_upload(client: TestClient, bearer: Headers, role: Role, allowed: bool) -> None:
    response = upload(client, bearer(role))
    assert response.status_code == (201 if allowed else 403)
    if not allowed:
        assert response.json()["error"]["code"] == "forbidden"


def test_forbidden_uploads_store_nothing(client: TestClient, bearer: Headers, engine: Engine) -> None:
    upload(client, bearer(Role.TEAMMATE))
    with Session(engine) as db:
        assert db.scalar(select(func.count(Source.id))) == 0


# --- uploading -----------------------------------------------------------------------------------------------

def test_an_upload_is_chunked_labelled_and_described(client: TestClient, bearer: Headers) -> None:
    response = upload(client, bearer(Role.MANAGER), clearance=2)
    body = response.json()
    assert response.status_code == 201 and body["duplicate"] is False
    source = body["source"]
    assert (source["name"], source["kind"], source["clearance"], source["clearance_label"], source["status"]) == (
        "notes.md", "notes", 2, "confidential", "ingested")
    assert source["chunk_count"] == 1 and source["redactions"] == {} and source["byte_size"] == len(NOTES)
    chunks = client.get(f"/api/sources/{source['id']}/chunks", headers=bearer(Role.MANAGER)).json()
    assert [(c["ordinal"], c["locator"]) for c in chunks] == [(0, "Notes")]
    assert "rate limited to 100 requests" in chunks[0]["text"]


def test_secrets_never_come_back_out_of_the_api(client: TestClient, bearer: Headers) -> None:
    data = b"# Setup\n\nDB_PASSWORD=Zq8-unique-secret and mail ops@mailbox.example or call +91 55501 00199.\n"
    source = upload(client, bearer(Role.MANAGER), name="setup.md", data=data).json()["source"]
    assert source["redactions"] == {"secret": 1, "email": 1, "phone": 1}
    served = [client.get(f"/api/sources/{source['id']}", headers=bearer(Role.MANAGER)).text,
              client.get(f"/api/sources/{source['id']}/chunks", headers=bearer(Role.MANAGER)).text,
              client.get("/api/sources", headers=bearer(Role.MANAGER)).text,
              client.get("/api/search", params={"q": "setup"}, headers=bearer(Role.MANAGER)).text]
    for text in served:
        assert "Zq8-unique-secret" not in text and "ops@mailbox.example" not in text and "55501 00199" not in text
    assert "[REDACTED:secret]" in served[1]


def test_the_same_file_twice_is_a_duplicate(client: TestClient, bearer: Headers, engine: Engine) -> None:
    first = upload(client, bearer(Role.MANAGER)).json()["source"]
    again = upload(client, bearer(Role.MANAGER), name="other-name.md")
    assert again.status_code == 200 and again.json()["duplicate"] is True and again.json()["source"]["id"] == first["id"]
    with Session(engine) as db:
        assert db.scalar(select(func.count(Source.id))) == 1


def test_a_duplicate_of_a_source_the_uploader_cannot_see_reveals_nothing(client: TestClient, bearer: Headers) -> None:
    upload(client, bearer(Role.ADMIN), clearance=3)
    again = upload(client, bearer(Role.LEAVER), clearance=1)  # a leaver sees up to level 2
    assert again.status_code == 200 and again.json() == {"duplicate": True, "source": None,
                                                         "message": "This file has already been ingested."}


@pytest.mark.parametrize(
    ("name", "data", "status", "code"),
    [("virus.exe", b"MZ", 415, "unsupported_type"), ("empty.md", b"", 400, "empty_file"),
     ("big.md", b"x" * 2001, 413, "file_too_large")],
)
def test_policy_rejections_use_the_right_status_and_store_nothing(
    client: TestClient, bearer: Headers, engine: Engine, name: str, data: bytes, status: int, code: str
) -> None:
    response = upload(client, bearer(Role.MANAGER), name=name, data=data)
    assert response.status_code == status and response.json()["error"]["code"] == code
    with Session(engine) as db:
        assert db.scalar(select(func.count(Source.id))) == 0
        assert db.scalar(select(AuditLog.detail).where(AuditLog.action == "source.rejected")) == {"reason": code}


def test_a_huge_multipart_stream_is_cut_off_by_the_body_cap(client: TestClient, bearer: Headers) -> None:
    """Regression: FastAPI turns any error raised while reading a body into a 400, which hid the 413."""
    def stream() -> Any:
        yield b'--x\r\nContent-Disposition: form-data; name="file"; filename="a.md"\r\n\r\n'
        for _ in range(100):
            yield b"a" * 1000

    response = client.post("/api/sources", content=stream(),
                           headers={**bearer(Role.MANAGER), "Content-Type": "multipart/form-data; boundary=x"})
    assert response.status_code == 413 and response.json()["error"]["code"] == "payload_too_large"


@pytest.mark.parametrize("form", [{"clearance": "7"}, {"clearance": "-1"}, {"clearance": "high"}, {}])
def test_clearance_is_required_and_must_be_a_level(client: TestClient, bearer: Headers, form: dict[str, str]) -> None:
    response = client.post("/api/sources", headers=bearer(Role.MANAGER),
                           files={"file": ("a.md", NOTES, "text/markdown")}, data=form)
    assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error"


def test_a_missing_file_or_an_unknown_kind_is_a_validation_error(client: TestClient, bearer: Headers) -> None:
    assert client.post("/api/sources", headers=bearer(Role.MANAGER), data={"clearance": "1"}).status_code == 422
    assert upload(client, bearer(Role.MANAGER), kind="spreadsheet").status_code == 422


def test_an_unreadable_file_is_422_but_is_listed_as_failed(client: TestClient, bearer: Headers) -> None:
    response = upload(client, bearer(Role.MANAGER), name="export.json", data=b'{"issues": [oops')
    error = response.json()["error"]
    assert response.status_code == 422 and error["code"] == "unreadable_content"
    listed = client.get("/api/sources", headers=bearer(Role.MANAGER)).json()
    failed = [s for s in listed if s["id"] == error["details"]["source_id"]]
    assert failed[0]["status"] == "failed" and failed[0]["error"] == "The file is not valid JSON."
    assert failed[0]["chunk_count"] == 0
    assert upload(client, bearer(Role.MANAGER), name="export.json", data=b'{"issues": [oops').json()["duplicate"] is True


def test_an_explicit_kind_wins_over_detection(client: TestClient, bearer: Headers) -> None:
    qa = b"Q1. What breaks?\nA1. The cron job.\n\nQ2. Who knows?\nA2. Nobody."
    assert upload(client, bearer(Role.MANAGER), name="a.txt", data=qa).json()["source"]["kind"] == "leaver_qa"
    forced = upload(client, bearer(Role.MANAGER), name="b.txt", data=qa + b" ", kind="notes")
    assert forced.json()["source"]["kind"] == "notes"


def test_uploads_are_audited_without_their_content(client: TestClient, bearer: Headers, engine: Engine) -> None:
    upload(client, bearer(Role.MANAGER), data=b"# Secrets\n\npassword is Zq8!audit-me-not\n")
    upload(client, bearer(Role.MANAGER), data=b"# Secrets\n\npassword is Zq8!audit-me-not\n")
    with Session(engine) as db:
        rows = db.execute(select(AuditLog.action, AuditLog.actor_label, AuditLog.detail)).all()
    assert [r.action for r in rows] == ["source.ingest", "source.duplicate"]
    assert rows[0].detail["redactions"] == {"secret": 1} and rows[0].actor_label == "manager@test.example"
    assert "audit-me-not" not in str(rows)


# --- seeing only what you are cleared for ---------------------------------------------------------------------

@pytest.mark.parametrize(("role", "visible"), [(Role.ADMIN, 9), (Role.MANAGER, 9), (Role.LEAVER, 7),
                                               (Role.SUCCESSOR, 7), (Role.TEAMMATE, 5)])
def test_the_list_shows_only_sources_within_clearance(
    client: TestClient, bearer: Headers, seeded: SeedReport, role: Role, visible: int
) -> None:
    listed = client.get("/api/sources", headers=bearer(role)).json()
    assert len(listed) == visible
    ceiling = {Role.ADMIN: 3, Role.MANAGER: 3, Role.LEAVER: 2, Role.SUCCESSOR: 2, Role.TEAMMATE: 1}[role]
    assert all(s["clearance"] <= ceiling for s in listed)


def test_restricted_sources_look_exactly_like_missing_ones(client: TestClient, bearer: Headers, seeded: SeedReport,
                                                           engine: Engine) -> None:
    with Session(engine) as db:
        restricted = db.scalar(select(Source.id).where(Source.clearance == 3).limit(1))
    for path in (f"/api/sources/{restricted}", f"/api/sources/{restricted}/chunks"):
        hidden = client.get(path, headers=bearer(Role.TEAMMATE))
        missing = client.get(path.replace(str(restricted), "99999"), headers=bearer(Role.TEAMMATE))
        assert hidden.status_code == missing.status_code == 404
        assert hidden.json()["error"]["code"] == missing.json()["error"]["code"] == "not_found"
        assert hidden.json()["error"]["message"] == missing.json()["error"]["message"]
        assert client.get(path, headers=bearer(Role.ADMIN)).status_code == 200


def test_search_finds_text_with_a_snippet_and_where_it_came_from(client: TestClient, bearer: Headers,
                                                                 seeded: SeedReport) -> None:
    hits = client.get("/api/search", params={"q": "REGISTRAR login"}, headers=bearer(Role.MANAGER)).json()
    assert hits and all("registrar login" in h["snippet"].lower() for h in hits)
    chat = next(h for h in hits if h["source_name"] == "#dev chat export")
    assert chat["author"] == "arjun" and chat["locator"] == "#dev 2026-09-02 14:41"


def test_search_never_returns_text_from_sources_above_clearance(client: TestClient, bearer: Headers,
                                                                seeded: SeedReport) -> None:
    assert client.get("/api/search", params={"q": "INR 3,800"}, headers=bearer(Role.MANAGER)).json()
    assert client.get("/api/search", params={"q": "INR 3,800"}, headers=bearer(Role.TEAMMATE)).json() == []
    leader_only = client.get("/api/search", params={"q": "council approved"}, headers=bearer(Role.SUCCESSOR))
    assert leader_only.json() == []
    visible_ids = {s["id"] for s in client.get("/api/sources", headers=bearer(Role.TEAMMATE)).json()}
    everything = client.get("/api/search", params={"q": "the", "limit": 50}, headers=bearer(Role.TEAMMATE)).json()
    assert everything and {h["source_id"] for h in everything} <= visible_ids


def test_search_treats_wildcards_literally_and_validates_the_query(client: TestClient, bearer: Headers,
                                                                   seeded: SeedReport) -> None:
    manager = bearer(Role.MANAGER)
    assert client.get("/api/search", params={"q": "%%"}, headers=manager).json() == []
    assert client.get("/api/search", params={"q": "__"}, headers=manager).json() == []
    for bad in ("a", "  ", "x" * 201):
        assert client.get("/api/search", params={"q": bad}, headers=manager).status_code == 422
    assert len(client.get("/api/search", params={"q": "the", "limit": 3}, headers=manager).json()) == 3


def test_an_empty_deployment_lists_nothing(client: TestClient, bearer: Headers) -> None:
    assert client.get("/api/sources", headers=bearer(Role.ADMIN)).json() == []
    assert client.get("/api/search", params={"q": "anything"}, headers=bearer(Role.ADMIN)).json() == []


def test_demo_mode_seeds_on_startup_and_a_restart_changes_nothing(engine: Engine, settings: Settings) -> None:
    demo = settings.model_copy(update={"demo_mode": True, "max_upload_bytes": 1_000_000})
    for _ in range(2):  # starting the app twice is how a Render restart looks
        with TestClient(create_app(demo, engine)) as client:
            token = client.post("/api/auth/login", json={"email": "manager@baton.example",
                                                         "password": demo.demo_password}).json()["access_token"]
            assert len(client.get("/api/sources", headers={"Authorization": f"Bearer {token}"}).json()) == 9
    with Session(engine) as db:
        assert db.scalar(select(func.count(Source.id))) == 9
