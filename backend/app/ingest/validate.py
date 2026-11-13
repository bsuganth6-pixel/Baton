"""Policy checks that run before a file is read: name, type, emptiness and size. A rejection stores nothing."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

ALLOWED_EXTENSIONS = frozenset({".md", ".markdown", ".txt", ".csv", ".json"})
MAX_NAME_LENGTH = 200


class IngestError(Exception):
    """A user-safe rejection. ``code`` is stable so clients and tests can rely on it."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class ValidatedUpload:
    name: str
    extension: str
    sha256: str
    size: int


def clean_filename(raw: str | None) -> str:
    """Drop any directory part and control characters, and cap the length."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return name[:MAX_NAME_LENGTH] or "upload"


def validate_upload(filename: str | None, data: bytes, max_bytes: int) -> ValidatedUpload:
    name = clean_filename(filename)
    extension = PurePosixPath(name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise IngestError("unsupported_type", "Only .md, .txt, .csv and .json files are supported.", 415)
    if not data:
        raise IngestError("empty_file", "The file is empty.", 400)
    if len(data) > max_bytes:
        raise IngestError("file_too_large", f"The file is larger than the {max_bytes // 1000} KB limit.", 413)
    return ValidatedUpload(name=name, extension=extension, sha256=hashlib.sha256(data).hexdigest(), size=len(data))
