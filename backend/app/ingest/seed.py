"""Demo mode: create the demo users and ingest demo-data/ through the real ingest path. Safe to run repeatedly.

Seeding reads demo-data/manifest.json (file, kind, clearance). It never reads ground_truth.json: that file
is a test oracle, and the live app must earn every claim by processing documents.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import audit
from app.config import PUBLIC_DEMO_EMAIL, REPO_DIR, Settings
from app.ingest.service import ingest_upload
from app.models import Project, Role, SourceKind, User
from app.protect.auth import hash_password, verify_password

log = logging.getLogger("baton.seed")

DEMO_USERS: tuple[tuple[str, str, Role], ...] = (
    ("admin@baton.example", "Demo Admin", Role.ADMIN),
    ("manager@baton.example", "Meera Iyer", Role.MANAGER),
    ("leaver@baton.example", "Arjun Mehta", Role.LEAVER),
    ("successor@baton.example", "Priya Nair", Role.SUCCESSOR),
    (PUBLIC_DEMO_EMAIL, "Rohan Desai", Role.TEAMMATE),
)


class SeedError(Exception):
    pass


@dataclass
class SeedReport:
    users_created: int = 0
    users_updated: int = 0
    sources_created: int = 0
    sources_existing: int = 0


def _ensure_project(db: Session, spec: dict[str, Any]) -> Project:
    project = db.scalar(select(Project).where(Project.slug == spec["slug"]))
    if project is None:
        project = Project(slug=spec["slug"], name=spec["name"], description=spec.get("description"),
                          as_of=date.fromisoformat(spec["as_of"]))
        db.add(project)
        db.flush()
    return project


def _ensure_users(db: Session, settings: Settings, report: SeedReport) -> None:
    users = DEMO_USERS
    if settings.app_env == "production":
        users = tuple(user for user in DEMO_USERS if user[0] == PUBLIC_DEMO_EMAIL and user[2] == Role.TEAMMATE)
    for email, name, role in users:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            db.add(User(email=email, name=name, role=role,
                        password_hash=hash_password(settings.demo_password, settings.bcrypt_rounds)))
            report.users_created += 1
        elif not verify_password(settings.demo_password, user.password_hash) or user.role != role or not user.is_active:
            user.password_hash = hash_password(settings.demo_password, settings.bcrypt_rounds)
            user.role, user.is_active = role, True
            report.users_updated += 1
    db.flush()


def seed_demo(factory: sessionmaker[Session], settings: Settings, data_dir: Path | None = None) -> SeedReport:
    """Create the demo project, users and sources. Does nothing unless DEMO_MODE is on."""
    report = SeedReport()
    if not settings.demo_mode:
        return report
    if not settings.demo_password:
        raise SeedError("DEMO_MODE needs DEMO_PASSWORD to be set")
    directory = data_dir or REPO_DIR / "demo-data"
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        with factory() as db:
            project = _ensure_project(db, manifest["project"])
            _ensure_users(db, settings, report)
            for entry in manifest["sources"]:
                outcome = ingest_upload(
                    db, project=project, filename=Path(entry["path"]).name,
                    data=(directory / entry["path"]).read_bytes(), clearance=entry["clearance"],
                    kind=SourceKind(entry["kind"]), display_name=entry.get("name"),
                    max_bytes=settings.max_upload_bytes, audit_duplicates=False,
                )
                if outcome.created:
                    report.sources_created += 1
                else:
                    report.sources_existing += 1
            if report.sources_created or report.users_created or report.users_updated:
                audit.record(db, "demo.seed", actor_label="system:seed", project_id=project.id,
                             detail={"users_created": report.users_created, "users_updated": report.users_updated,
                                     "sources_created": report.sources_created})
            db.commit()
    except (OSError, KeyError, ValueError) as exc:
        raise SeedError(f"Could not seed the demo data: {type(exc).__name__}") from exc
    log.info("demo seed: %s", report)
    return report
