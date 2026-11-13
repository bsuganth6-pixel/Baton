"""Shared fixtures: the ground truth, and migrated databases (SQLite always, Postgres when configured)."""
from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import Settings, normalize_database_url
from app.db import make_sessionmaker
from app.ingest.seed import SeedReport, seed_demo
from app.main import create_app
from app.models import Role, User
from app.protect.auth import create_access_token, hash_password

DEMO_PASSWORD = "demo-password-123"
BACKEND_DIR = Path(__file__).resolve().parents[1]
DEMO_DIR = BACKEND_DIR.parent / "demo-data"


def alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["database_url"] = url
    return cfg


def _wipe_postgres(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's own DATABASE_URL, DEMO_MODE and friends must never leak into a test."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.fixture(scope="session")
def demo_dir() -> Path:
    return DEMO_DIR


@pytest.fixture(scope="session")
def truth() -> dict[str, Any]:
    return json.loads((DEMO_DIR / "ground_truth.json").read_text(encoding="utf-8"))


@pytest.fixture(params=["sqlite", "postgres"])
def db_url(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[str]:
    """A fresh, empty database URL. Postgres runs only when TEST_DATABASE_URL is set."""
    if request.param == "sqlite":
        yield f"sqlite:///{tmp_path / 'test.db'}"
        return
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("TEST_DATABASE_URL is not set")
    url = normalize_database_url(raw)
    if "test" not in (make_url(url).database or ""):
        pytest.fail("TEST_DATABASE_URL must name a database containing 'test': the suite wipes it")
    _wipe_postgres(url)
    yield url
    _wipe_postgres(url)


@pytest.fixture
def alembic_cfg(db_url: str) -> Config:
    return alembic_config(db_url)


@pytest.fixture
def engine(db_url: str) -> Iterator[Engine]:
    """An engine on a database migrated to head, with foreign keys enforced on SQLite."""
    command.upgrade(alembic_config(db_url), "head")
    eng = create_engine(db_url)
    if eng.dialect.name == "sqlite":
        # SQLAlchemy's recipe for pysqlite: enforce foreign keys and make SAVEPOINT behave.
        @event.listens_for(eng, "connect")
        def _sqlite_connect(dbapi_connection: Any, _record: Any) -> None:
            dbapi_connection.isolation_level = None
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        @event.listens_for(eng, "begin")
        def _sqlite_begin(connection: Any) -> None:
            connection.exec_driver_sql("BEGIN")

    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as db:
        yield db
        db.rollback()


# --- the API ------------------------------------------------------------------------------------------

@pytest.fixture
def settings(db_url: str) -> Settings:
    return Settings(_env_file=None, app_env="test", database_url=db_url, secret_key="test-secret-" + "k" * 40,
                    bcrypt_rounds=4, cookie_secure=False, demo_password=DEMO_PASSWORD, max_upload_bytes=2_000)


@pytest.fixture
def client(engine: Engine, settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, engine)) as test_client:
        yield test_client


@pytest.fixture
def users(engine: Engine) -> dict[Role, User]:
    """One user per role. Their password is "pw-<role>"."""
    with Session(engine, expire_on_commit=False) as db:
        made = {role: User(email=f"{role.value}@test.example", name=role.value.title(), role=role,
                           password_hash=hash_password(f"pw-{role.value}", 4)) for role in Role}
        db.add_all(made.values())
        db.commit()
    return made


@pytest.fixture
def bearer(users: dict[Role, User], settings: Settings) -> Callable[[Role], dict[str, str]]:
    def headers(role: Role) -> dict[str, str]:
        token = create_access_token(users[role].id, settings.signing_key, 60)
        return {"Authorization": f"Bearer {token}"}

    return headers


@pytest.fixture
def seeded(engine: Engine, settings: Settings) -> SeedReport:
    """The demo project, users and nine sources, ingested through the real path."""
    demo = settings.model_copy(update={"demo_mode": True, "max_upload_bytes": 1_000_000})
    return seed_demo(make_sessionmaker(engine), demo)
