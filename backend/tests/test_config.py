"""DATABASE_URL handling. Render hands out postgres:// URLs; SQLAlchemy 2 only knows postgresql://."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.exc import NoSuchModuleError

from app.config import LOCAL_DATABASE_URL, Settings, normalize_database_url

REPO_DIR = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgres://u:p@host:5432/db", "postgresql+psycopg2://u:p@host:5432/db"),
        ("postgresql://u:p@host/db?sslmode=require", "postgresql+psycopg2://u:p@host/db?sslmode=require"),
        ("postgres://u:p%40ss%2Fword@host/db", "postgresql+psycopg2://u:p%40ss%2Fword@host/db"),
        ("  postgres://u:p@host/db \n", "postgresql+psycopg2://u:p@host/db"),
    ],
)
def test_postgres_urls_get_the_psycopg2_driver(raw: str, expected: str) -> None:
    assert normalize_database_url(raw) == expected


@pytest.mark.parametrize(
    "url",
    ["postgresql+psycopg2://u:p@h/db", "postgresql+psycopg://u:p@h/db", "sqlite:///x.db", "sqlite://"],
)
def test_urls_that_name_a_driver_are_left_alone(url: str) -> None:
    assert normalize_database_url(url) == url


@pytest.mark.parametrize("blank", ["", "   ", "\n"])
def test_blank_url_is_rejected(blank: str) -> None:
    with pytest.raises(ValueError, match="empty"):
        normalize_database_url(blank)


def test_raw_render_url_is_refused_by_sqlalchemy() -> None:
    """The root cause of the Render failure: SQLAlchemy 2 has no 'postgres' dialect."""
    with pytest.raises(NoSuchModuleError):
        create_engine("postgres://u:p@localhost/db")


def test_normalized_render_url_builds_a_psycopg2_engine() -> None:
    engine = create_engine(normalize_database_url("postgres://u:p@localhost/db"))
    assert (engine.dialect.name, engine.dialect.driver) == ("postgresql", "psycopg2")


def test_settings_normalize_the_environment_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host/db")
    assert Settings(_env_file=None).database_url == "postgresql+psycopg2://u:p@host/db"


def test_settings_default_to_local_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert Settings(_env_file=None).database_url == LOCAL_DATABASE_URL


def test_settings_reject_an_empty_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_env_example_lists_exactly_the_settings() -> None:
    lines = (REPO_DIR / ".env.example").read_text(encoding="utf-8").splitlines()
    keys = {line.split("=", 1)[0] for line in lines if line.strip() and not line.startswith("#")}
    assert keys == {name.upper() for name in Settings.model_fields}
