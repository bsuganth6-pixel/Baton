"""Application settings, read from environment variables (never from code)."""
from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, PrivateAttr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DRIVER_PREFIX = "postgresql+psycopg2://"
LOCAL_DATABASE_URL = DRIVER_PREFIX + "baton:baton@localhost:5432/baton"
REPO_DIR = Path(__file__).resolve().parents[2]
PUBLIC_DEMO_EMAIL = "teammate@baton.example"
# Starlette keeps an upload in memory up to 1 MiB and spools anything larger to a temporary file.
# Staying under that ceiling means an original, unredacted upload is never written to disk.
UPLOAD_MEMORY_CEILING = 1_048_576
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


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

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = LOCAL_DATABASE_URL
    secret_key: str = ""
    access_token_minutes: int = Field(480, ge=5, le=1440)
    bcrypt_rounds: int = Field(12, ge=4, le=15)
    cookie_secure: bool = True
    allowed_origins: str = ""
    max_upload_bytes: int = Field(1_000_000, ge=1_000, le=UPLOAD_MEMORY_CEILING)
    show_withheld_count: bool = True
    demo_mode: bool = False
    demo_password: str = ""
    log_level: str = "INFO"

    _ephemeral_key: str = PrivateAttr(default_factory=lambda: secrets.token_urlsafe(48))

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        return normalize_database_url(value)

    @field_validator("log_level")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in LOG_LEVELS:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(LOG_LEVELS)}")
        return level

    @model_validator(mode="after")
    def _require_a_real_secret_in_production(self) -> Settings:
        if self.app_env == "production" and len(self.secret_key) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters in production")
        return self

    @property
    def signing_key(self) -> str:
        """The configured key, or a random one that lasts until the process restarts."""
        return self.secret_key or self._ephemeral_key

    @property
    def origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
