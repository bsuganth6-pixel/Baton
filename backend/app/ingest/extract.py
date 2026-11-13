"""Turn an uploaded file into segments: pieces of text that each carry who said it, when, and where it was."""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.ingest.chunker import MAX_CHARS, split_markdown
from app.models import SourceKind

MAX_ROWS = 5000
GITHUB_KEYS = {"readme", "issues", "pull_requests", "commits"}
_FRONT_MATTER = re.compile(r"\A---\n(?P<body>.*?)\n---[ \t]*(?:\n|\Z)", re.S)
_QA = re.compile(
    r"\*{0,2}Q(?P<n>\d+)[.)][ \t]*(?P<q>[^\n]*?)\*{0,2}[ \t]*\n[ \t]*A(?P=n)[.)][ \t]*(?P<a>.*?)"
    r"(?=\n[ \t]*\n[ \t]*\*{0,2}Q\d+[.)]|\Z)",
    re.S,
)


class ExtractionError(Exception):
    """The file was read but cannot be turned into text. The message is safe to show to users."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Segment:
    text: str
    locator: str | None = None
    author: str | None = None
    occurred_at: datetime | None = None


# --- reading ---------------------------------------------------------------------------------------

def decode_text(data: bytes) -> str:
    """Decode as text (UTF-8, UTF-16 with a BOM, or Windows-1252) and refuse binary files."""
    try:
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = data.decode("utf-16")
        elif b"\x00" in data:
            raise ExtractionError("This looks like a binary file, not text.")
        else:
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = data.decode("cp1252")
    except UnicodeError:
        raise ExtractionError("The file is not readable text.") from None
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise ExtractionError("The file contains no text.")
    controls = sum(1 for char in text if ord(char) < 32 and char not in "\n\t")
    if controls > max(10, len(text) // 100):
        raise ExtractionError("This looks like a binary file, not text.")
    return text


def parse_when(value: object) -> datetime | None:
    """ISO dates and datetimes, or Unix timestamps. Naive values are taken as UTC."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float) or (isinstance(value, str) and re.fullmatch(r"\d{9,10}(\.\d+)?", value.strip())):
        try:
            return datetime.fromtimestamp(float(value), UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _load_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise ExtractionError("The file is not valid JSON.") from None
    except RecursionError:
        raise ExtractionError("The JSON is nested too deeply.") from None


def split_front_matter(text: str) -> tuple[dict[str, str], str]:
    match = _FRONT_MATTER.match(text)
    if not match:
        return {}, text
    meta: dict[str, str] = {}
    for line in match.group("body").splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip():
            meta[key.strip().lower()] = value.strip()
    return meta, text[match.end():]


# --- kinds -----------------------------------------------------------------------------------------

def detect_kind(extension: str, text: str) -> SourceKind:
    if extension == ".csv":
        return SourceKind.TASKS
    if extension == ".json":
        data = _load_json(text)  # a .json file that is not JSON is a failure, not prose
        if isinstance(data, dict) and GITHUB_KEYS & data.keys():
            return SourceKind.GITHUB
        if isinstance(data, dict) and isinstance(data.get("messages"), list):
            return SourceKind.CHAT
        if isinstance(data, list) and data and all(isinstance(m, dict) and "text" in m for m in data):
            return SourceKind.CHAT
        return SourceKind.NOTES
    return SourceKind.LEAVER_QA if len(_QA.findall(text)) >= 2 else SourceKind.NOTES


def fallback_kind(extension: str) -> SourceKind:
    """The kind to record for a file that could not be read at all."""
    return SourceKind.TASKS if extension == ".csv" else SourceKind.NOTES


def extract_segments(kind: SourceKind, text: str) -> list[Segment]:
    try:
        if kind is SourceKind.TASKS:
            segments = _tasks(text)
        elif kind is SourceKind.GITHUB:
            segments = _github(_load_json(text))
        elif kind is SourceKind.CHAT:
            segments = _chat(_load_json(text))
        elif kind is SourceKind.LEAVER_QA:
            segments = _qa(text)
        else:
            segments = _notes(text)
    except csv.Error:
        raise ExtractionError("The CSV file is malformed.") from None
    segments = [s for s in segments if s.text.strip()]
    if not segments:
        raise ExtractionError("No readable content was found in this file.")
    return segments


# --- one extractor per kind --------------------------------------------------------------------------

def _front_matter_segments(meta: dict[str, str], author: str | None, when: datetime | None) -> list[Segment]:
    if not meta:
        return []
    return [Segment("\n".join(f"{k}: {v}" for k, v in meta.items()), "document header", author, when)]


def _notes(text: str) -> list[Segment]:
    stripped = text.lstrip()
    if stripped[:1] in ("{", "["):
        try:
            return _flatten_json(json.loads(text))
        except (json.JSONDecodeError, RecursionError):
            pass  # not JSON after all: treat it as prose
    meta, body = split_front_matter(text)
    author, when = meta.get("author") or meta.get("leaver"), parse_when(meta.get("date") or meta.get("filled_on"))
    segments = _front_matter_segments(meta, author, when)
    segments += [Segment(t, where, author, when) for where, t in split_markdown(body)]
    return segments


def _qa(text: str) -> list[Segment]:
    meta, body = split_front_matter(text)
    pairs = list(_QA.finditer(body))
    if not pairs:
        return _notes(text)
    author, when = meta.get("leaver") or meta.get("author"), parse_when(meta.get("filled_on") or meta.get("date"))
    segments = _front_matter_segments(meta, author, when)
    for match in pairs:
        number, question, answer = match.group("n"), match.group("q").strip(), match.group("a").strip()
        segments.append(Segment(f"Q{number}. {question}\nA{number}. {answer}", f"question {number}", author, when))
    return segments


def _tasks(text: str) -> list[Segment]:
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 2 or len(rows[0]) < 2:
        raise ExtractionError("The CSV needs a header row and at least one data row.")
    if len(rows) - 1 > MAX_ROWS:
        raise ExtractionError(f"The CSV has more than {MAX_ROWS} rows.")
    header = [h.strip() or f"column{i + 1}" for i, h in enumerate(rows[0])]
    if len({h.lower() for h in header}) != len(header):
        raise ExtractionError("The CSV has duplicate column names.")
    segments = []
    for number, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) > len(header):
            raise ExtractionError(f"Row {number} has more cells than the header.")
        cells = dict(zip(header, [c.strip() for c in row] + [""] * (len(header) - len(row)), strict=True))
        lowered = {k.lower(): v for k, v in cells.items()}
        task_id = lowered.get("id") or cells[header[0]] or f"row {number}"
        title = lowered.get("title") or lowered.get("name") or ""
        lines = [f"Task {task_id}: {title}".rstrip(": ")]
        owner = ""
        for key, value in cells.items():
            if key.lower() in {"id", "title", "name"}:
                continue
            if key.lower() in {"owner", "assignee", "assigned to", "assigned_to"}:
                owner = value
                lines.append(f"{key}: {value or '(not assigned)'}")
            elif value:
                lines.append(f"{key}: {value}")
        when = parse_when(lowered.get("updated")) or parse_when(lowered.get("created"))
        segments.append(Segment("\n".join(lines), f"task {task_id}", owner or None, when))
    return segments


def _chat(data: Any) -> list[Segment]:
    messages = data.get("messages") if isinstance(data, dict) else data
    if not isinstance(messages, list):
        raise ExtractionError("No messages were found in this chat export.")
    channel = _text(data.get("channel")) if isinstance(data, dict) else ""
    segments = []
    for number, message in enumerate(messages, start=1):
        if not isinstance(message, dict):
            continue
        body = _text(message.get("text"))
        if not body:
            continue
        user = _text(message.get("user") or message.get("author") or message.get("username")) or "unknown"
        when = parse_when(message.get("ts") or message.get("timestamp") or message.get("date"))
        stamp = when.strftime("%Y-%m-%d %H:%M") if when else f"message {number}"
        segments.append(Segment(f"{user}: {body}", f"#{channel} {stamp}" if channel else stamp, user, when))
    return segments


def _github(data: Any) -> list[Segment]:
    if not isinstance(data, dict) or not GITHUB_KEYS & data.keys():
        raise ExtractionError("This does not look like a GitHub export.")
    exported = parse_when(data.get("exported_at"))
    segments = [Segment(t, f"README: {where}", None, exported) for where, t in split_markdown(_text(data.get("readme")))]
    for label, key, date_field in (("Issue", "issues", "created_at"), ("Pull request", "pull_requests", "merged_at")):
        for item in data.get(key) or []:
            if isinstance(item, dict):
                segments += _github_item(label, item, date_field)
    for commit in data.get("commits") or []:
        if isinstance(commit, dict) and _text(commit.get("message")):
            sha = _text(commit.get("sha"))
            segments.append(Segment(f"Commit {sha}: {_text(commit.get('message'))}", f"commit {sha}",
                                    _text(commit.get("author")) or None, parse_when(commit.get("date"))))
    return segments


def _github_item(label: str, item: dict[str, Any], date_field: str) -> list[Segment]:
    number, title = _text(item.get("number")), _text(item.get("title"))
    where = f"{'PR' if label == 'Pull request' else 'issue'} #{number}"
    opened = parse_when(item.get(date_field)) or parse_when(item.get("created_at"))
    head = f"{label} #{number}: {title}\nstate: {_text(item.get('state'))}"
    body = _text(item.get("body"))
    segments = [Segment(f"{head}\n\n{body}" if body else head, where, _text(item.get("author")) or None, opened)]
    for index, comment in enumerate(item.get("comments") or [], start=1):
        if isinstance(comment, dict) and _text(comment.get("body")):
            segments.append(Segment(f"Comment on {label.lower()} #{number}:\n{_text(comment.get('body'))}",
                                    f"{where} comment {index}", _text(comment.get("author")) or None,
                                    parse_when(comment.get("created_at"))))
    return segments


def _flatten_json(data: Any) -> list[Segment]:
    """Unrecognised JSON: one "path: value" line per leaf, grouped into chunks in order."""
    lines: list[str] = []

    def walk(node: Any, path: str, depth: int = 0) -> None:
        if isinstance(node, dict) and depth < 8:
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else str(key), depth + 1)
        elif isinstance(node, list) and depth < 8:
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]", depth + 1)
        elif node not in (None, ""):
            lines.append(f"{path}: {node}")

    walk(data, "")
    segments: list[Segment] = []
    current = ""
    for line in lines:
        if current and len(current) + 1 + len(line) > MAX_CHARS:
            segments.append(Segment(current, "json"))
            current = ""
        current = f"{current}\n{line}" if current else line
    if current:
        segments.append(Segment(current, "json"))
    return segments
