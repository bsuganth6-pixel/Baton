"""Request and response shapes."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models import Role, SourceKind, SourceStatus


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: Role
    clearance: int
    permissions: list[str]


class LoginOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserOut


class DemoCredentialsOut(BaseModel):
    email: str
    password: str


class SourceOut(BaseModel):
    id: int
    name: str
    kind: SourceKind
    clearance: int
    clearance_label: str
    status: SourceStatus
    error: str | None
    byte_size: int
    chunk_count: int
    redactions: dict[str, int]
    created_at: datetime


class UploadOut(BaseModel):
    duplicate: bool
    message: str
    source: SourceOut | None


class ChunkOut(BaseModel):
    id: int
    ordinal: int
    text: str
    locator: str | None
    author: str | None
    occurred_at: datetime | None


class SearchHit(BaseModel):
    chunk_id: int
    source_id: int
    source_name: str
    locator: str | None
    author: str | None
    occurred_at: datetime | None
    snippet: str
