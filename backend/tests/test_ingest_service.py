"""Ingesting files into the database: nothing sensitive is stored, failures are visible, seeding is idempotent."""
from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import LargeBinary, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import PUBLIC_DEMO_EMAIL, Settings
from app.db import make_sessionmaker
from app.ingest import service
from app.ingest.seed import DEMO_USERS, SeedError, SeedReport, seed_demo
from app.ingest.service import get_or_create_project, ingest_upload
from app.ingest.validate import IngestError
from app.models import AuditLog, Chunk, Project, Role, Source, SourceKind, SourceStatus, User
from app.protect.auth import verify_password
from tests.conftest import DEMO_PASSWORD

LIMIT = 1_000_000


def demo_settings(settings: Settings, **changes: Any) -> Settings:
    return settings.model_copy(update={"demo_mode": True, "max_upload_bytes": LIMIT, **changes})


def put(db: Session, name: str, data: bytes, clearance: int = 1, **kwargs: Any) -> service.IngestOutcome:
    return ingest_upload(db, project=get_or_create_project(db), filename=name, data=data, clearance=clearance,
                         max_bytes=LIMIT, **kwargs)


def everything_stored(db: Session) -> str:
    """Every text a source, chunk or audit row holds, as one lowercase blob to search."""
    parts: list[str] = []
    for source in db.scalars(select(Source)):
        parts += [source.text, source.name, source.error or "", json.dumps(source.redaction_counts)]
    for chunk in db.scalars(select(Chunk)):
        parts += [chunk.text, chunk.locator or "", chunk.author or ""]
    for entry in db.scalars(select(AuditLog)):
        parts += [entry.actor_label, entry.action, entry.target_id or "", json.dumps(entry.detail)]
    return "\n".join(parts).lower()


# --- the demo data, through the real path --------------------------------------------------------------

def test_no_planted_secret_or_personal_detail_reaches_the_database(
    seeded: SeedReport, engine: Engine, truth: dict[str, Any]
) -> None:
    assert (seeded.sources_created, seeded.users_created) == (9, 5)
    forbidden = [s["secret"] for s in truth["planted_secrets"]] + [p["value"] for p in truth["planted_pii"]]
    with Session(engine) as db:
        blob = everything_stored(db)
    for value in forbidden:
        assert value.lower() not in blob, value


def test_stored_sources_match_the_manifest(seeded: SeedReport, engine: Engine) -> None:
    manifest = json.loads((__import__("pathlib").Path(__file__).resolve().parents[2]
                           / "demo-data" / "manifest.json").read_text(encoding="utf-8"))
    with Session(engine) as db:
        stored = {s.name: (s.kind.value, s.clearance, s.status) for s in db.scalars(select(Source))}
        project = db.scalar(select(Project))
    expected = {e["name"]: (e["kind"], e["clearance"], SourceStatus.INGESTED) for e in manifest["sources"]}
    assert stored == expected
    assert project is not None and project.slug == "campusconnect" and str(project.as_of) == "2026-10-20"


def test_chunks_are_ordered_dated_and_redactions_are_counted(seeded: SeedReport, engine: Engine) -> None:
    with Session(engine) as db:
        chat = db.scalar(select(Source).where(Source.name == "#dev chat export"))
        assert chat is not None
        chunks = db.scalars(select(Chunk).where(Chunk.source_id == chat.id).order_by(Chunk.ordinal)).all()
        github = db.scalar(select(Source).where(Source.name == "campus-api GitHub export"))
    assert [c.ordinal for c in chunks] == list(range(17))
    assert all(c.occurred_at is not None and c.author for c in chunks)
    assert chat.redaction_counts["phone"] == 2 and chat.redaction_counts["national_id"] == 1
    assert github is not None and github.redaction_counts["token"] == 1 and github.redaction_counts["api_key"] == 1


def test_seeding_twice_changes_nothing(seeded: SeedReport, engine: Engine, settings: Settings) -> None:
    with Session(engine) as db:
        before = (db.scalar(select(func.count(Source.id))), db.scalar(select(func.count(Chunk.id))),
                  db.scalar(select(func.count(AuditLog.id))), db.scalar(select(func.count(User.id))))
    again = seed_demo(make_sessionmaker(engine), demo_settings(settings))
    assert (again.sources_created, again.sources_existing, again.users_created) == (0, 9, 0)
    with Session(engine) as db:
        after = (db.scalar(select(func.count(Source.id))), db.scalar(select(func.count(Chunk.id))),
                 db.scalar(select(func.count(AuditLog.id))), db.scalar(select(func.count(User.id))))
    assert after == before


def test_seeding_needs_demo_mode_and_a_password(engine: Engine, settings: Settings) -> None:
    sessions = make_sessionmaker(engine)
    assert seed_demo(sessions, settings) == SeedReport()  # demo mode is off
    with pytest.raises(SeedError):
        seed_demo(sessions, demo_settings(settings, demo_password=""))
    with Session(engine) as db:
        assert db.scalar(select(func.count(User.id))) == 0


def test_demo_users_get_their_roles_and_a_changed_password_is_applied(
    seeded: SeedReport, engine: Engine, settings: Settings
) -> None:
    with Session(engine) as db:
        stored = {u.email: u for u in db.scalars(select(User))}
        assert {e: u.role for e, u in stored.items()} == {email: role for email, _, role in DEMO_USERS}
        assert all(verify_password(DEMO_PASSWORD, u.password_hash) for u in stored.values())
    changed = seed_demo(make_sessionmaker(engine), demo_settings(settings, demo_password="a-new-demo-password"))
    assert changed.users_updated == 5
    with Session(engine) as db:
        assert all(verify_password("a-new-demo-password", u.password_hash) for u in db.scalars(select(User)))


def test_production_demo_seed_creates_only_the_limited_demo_user(
    engine: Engine, settings: Settings
) -> None:
    production = demo_settings(settings).model_copy(update={"app_env": "production"})
    seed_demo(make_sessionmaker(engine), production)
    with Session(engine) as db:
        stored = db.scalars(select(User)).all()
    assert [(user.email, user.role) for user in stored] == [(PUBLIC_DEMO_EMAIL, Role.TEAMMATE)]
    assert verify_password(settings.demo_password, stored[0].password_hash)


def test_a_missing_manifest_is_reported_not_swallowed(engine: Engine, settings: Settings, tmp_path: Any) -> None:
    with pytest.raises(SeedError):
        seed_demo(make_sessionmaker(engine), demo_settings(settings), data_dir=tmp_path)


# --- single uploads --------------------------------------------------------------------------------------

def test_a_password_in_an_upload_is_stored_nowhere(session: Session) -> None:
    outcome = put(session, "setup.md", b"# Setup\n\nThe admin password is Zq8!unique-pass for now.\n")
    session.flush()
    assert outcome.created and outcome.source.redaction_counts == {"secret": 1}
    assert "zq8!unique-pass" not in everything_stored(session)
    assert "[redacted:secret]" in everything_stored(session)


def test_originals_are_never_persisted() -> None:
    assert not [c for c in Source.__table__.columns if isinstance(c.type, LargeBinary)]


def test_the_filename_is_cleaned_and_redacted_too(session: Session) -> None:
    source = put(session, "../../alice.smith@mailbox.example notes.md", b"# T\n\nbody").source
    assert source.name == "[REDACTED:email] notes.md"


def test_an_unreadable_file_is_stored_as_failed_without_content(session: Session) -> None:
    outcome = put(session, "export.json", b'{"issues": [oops')
    session.flush()
    source = outcome.source
    assert outcome.created and source.status is SourceStatus.FAILED and source.text == ""
    assert source.error == "The file is not valid JSON." and source.chunks == []
    assert session.scalar(select(AuditLog.action).where(AuditLog.action == "source.ingest_failed")) is not None


def test_a_binary_file_is_stored_as_failed(session: Session) -> None:
    source = put(session, "data.txt", b"\x00\x01\x02 binary").source
    assert source.status is SourceStatus.FAILED and "binary" in (source.error or "")


def test_the_same_bytes_are_ingested_once(session: Session) -> None:
    first = put(session, "a.md", b"# A\n\nsome text")
    second = put(session, "renamed.md", b"# A\n\nsome text")
    assert first.created and not second.created and second.source.id == first.source.id
    assert session.scalar(select(func.count(Source.id))) == 1
    assert session.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "source.duplicate")) == 1


def test_losing_a_race_to_an_identical_upload_is_a_duplicate_not_an_error(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = put(session, "a.md", b"# A\n\nsome text")
    real = service._existing
    calls: list[int] = []

    def blind_the_first_lookup(db: Session, project_id: int, sha256: str) -> Source | None:
        calls.append(1)
        return None if len(calls) == 1 else real(db, project_id, sha256)

    monkeypatch.setattr(service, "_existing", blind_the_first_lookup)
    second = put(session, "a.md", b"# A\n\nsome text")
    assert not second.created and second.source.id == first.source.id


@pytest.mark.parametrize("clearance", [-1, 4, 99])
def test_clearance_outside_the_levels_is_refused_before_anything_is_stored(session: Session, clearance: int) -> None:
    with pytest.raises(IngestError) as caught:
        put(session, "a.md", b"# A\n\ntext", clearance=clearance)
    assert caught.value.code == "invalid_clearance"
    assert session.scalar(select(func.count(Source.id))) == 0


def test_an_explicit_kind_overrides_detection(session: Session) -> None:
    source = put(session, "answers.txt", b"just words here", kind=SourceKind.NOTES).source
    assert source.kind is SourceKind.NOTES and source.status is SourceStatus.INGESTED
    wrong = put(session, "also.txt", b"just other words", kind=SourceKind.TASKS).source
    assert wrong.status is SourceStatus.FAILED  # a CSV that is not a CSV
    assert Role.ADMIN  # (roles are unaffected by ingestion)
