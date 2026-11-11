"""Application settings, read from environment variables (never from code)."""
from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DRIVER_PREFIX = "postgresql+psycopg2://"
LOCAL_DATABASE_URL = DRIVER_PREFIX + "baton:baton@localhost:5432/baton"


def normalize_database_url(url: str) -> str:
    """Return a SQLAlchemy URL that names the psycopg2 driver.

    Render hands out ``postgres://`` URLs, which SQLAlchemy 2 refuses to load.
    URLs that already name a driver, or another database, are left alone.
    """
    cleaned = url.strip()
    if not cleaned:
        raise ValueError("DATABASE_URL is empty")
    for scheme in ("postgres://", "postgresql://"):
        if cleaned.startswith(scheme):
            return DRIVER_PREFIX + cleaned[len(scheme):]
    return cleaned


class Settings(BaseSettings):
    # The closest .env wins: backend/.env overrides the repo-level one.
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    database_url: str = LOCAL_DATABASE_URL

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        return normalize_database_url(value)


@lru_cache
def get_settings() -> Settings:
    return Settings()
