"""Shared fixtures: the ground truth, and migrated databases (SQLite always, Postgres when configured)."""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import normalize_database_url

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
