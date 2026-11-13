"""Redaction: planted secrets and PII never survive, and what the pipeline needs to quote does."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from app.ingest.extract import decode_text, extract_segments
from app.models import SourceKind
from app.protect.redact import redact

DEMO = Path(__file__).resolve().parents[2] / "demo-data"
TRUTH: dict[str, Any] = json.loads((DEMO / "ground_truth.json").read_text(encoding="utf-8"))
SOURCES = {s["id"]: s for s in TRUTH["sources"]}


def redacted_chunks(source_id: str) -> list[str]:
    source = SOURCES[source_id]
    text = decode_text((DEMO / source["path"]).read_bytes())
    return [redact(segment.text).text for segment in extract_segments(SourceKind(source["kind"]), text)]


# --- the oracle: what must go, and what must stay ---------------------------------------------------

@pytest.mark.parametrize("item", TRUTH["planted_secrets"], ids=lambda i: i["id"])
def test_planted_secrets_never_survive(item: dict[str, Any]) -> None:
    assert not any(item["secret"] in chunk for chunk in redacted_chunks(item["source"]))


@pytest.mark.parametrize("item", TRUTH["planted_pii"], ids=lambda i: i["id"])
def test_planted_pii_never_survives(item: dict[str, Any]) -> None:
    needle = item["value"].lower()
    assert not any(needle in chunk.lower() for chunk in redacted_chunks(item["source"]))


@pytest.mark.parametrize("claim", TRUTH["claims"], ids=lambda c: c["id"])
def test_every_oracle_quote_survives_redaction_inside_a_single_chunk(claim: dict[str, Any]) -> None:
    """If redaction or chunking broke a quote, the pipeline could never produce verifiable evidence for it."""
    for evidence in claim["evidence"]:
        assert any(evidence["quote"] in chunk for chunk in redacted_chunks(evidence["source"])), evidence


def test_prompt_injection_text_is_kept_as_data() -> None:
    for item in TRUTH["planted_injections"]:
        assert any(item["text"] in chunk for chunk in redacted_chunks(item["source"]))


def test_redacting_twice_changes_nothing() -> None:
    for source_id in SOURCES:
        for chunk in redacted_chunks(source_id):
            assert redact(chunk).text == chunk


def test_people_are_not_redacted() -> None:
    text = "Arjun Mehta hands the API to Priya Nair. Meera Iyer approves."
    assert redact(text).text == text


# --- individual rules ---------------------------------------------------------------------------------

CASES = [
    ("smtp_password: hunter22", "smtp_password: [REDACTED:secret]"),
    ("API_KEY=abc123def456", "API_KEY=[REDACTED:secret]"),
    ('token = "abcDEF123456"', 'token = "[REDACTED:secret]"'),
    ("the admin password is Campus@2026! ok", "the admin password is [REDACTED:secret] ok"),
    ("postgres://u:p4ss@host:5432/db", "postgres://u:[REDACTED:password]@host:5432/db"),
    ("Authorization: Bearer abcdefghijklmnop1234", "Authorization: Bearer [REDACTED:token]"),
    ("ghp_" + "a1" * 20, "[REDACTED:api_key]"),
    ("AKIAABCDEFGHIJKLMNOP", "[REDACTED:api_key]"),
    ("write to a.b@example.org today", "write to [REDACTED:email] today"),
    ("call +1 415 555 0101 now", "call [REDACTED:phone] now"),
    ("call 9876543210 now", "call [REDACTED:phone] now"),
    ("call (415) 555-0101 now", "call [REDACTED:phone] now"),
    ("card 4111 1111 1111 1111 ok", "card [REDACTED:card] ok"),
    ("SSN 123-45-6789", "SSN [REDACTED:national_id]"),
    ("PAN ABCDE1234F", "PAN [REDACTED:pan]"),
    ("IFSC SBIN0001234", "IFSC [REDACTED:ifsc]"),
    ("A/C no. 123456789012", "A/C no. [REDACTED:bank_account]"),
    ("born on July 14, 2004", "born on [REDACTED:date_of_birth]"),
    ("roll no 24CS1087", "roll no [REDACTED:student_id]"),
    ("pay me at ravi@okbank.", "pay me at [REDACTED:upi_id]."),
    ("-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----", "[REDACTED:private_key]"),
]
UNTOUCHED = [
    "The reset token is signed with the staging secret.",
    "Credentials are rotated every January.",
    "http://localhost:3000/path is the dev server",
    "Expires 2026-12-31 at 02:00 IST, build 4021, 9,400 students, INR 3,800 a month.",
    "commit " + "a1b2c3d4e5" * 4,
    "sha256 " + "0123456789abcdef" * 4,
    "campus-api rate limiter, 60 requests per minute per user",
]


@pytest.mark.parametrize(("raw", "expected"), CASES)
def test_rule_replaces_what_it_should(raw: str, expected: str) -> None:
    assert redact(raw).text == expected


@pytest.mark.parametrize("text", UNTOUCHED)
def test_ordinary_text_is_left_alone(text: str) -> None:
    assert redact(text).text == text


def test_counts_say_what_was_replaced() -> None:
    result = redact("a@b.example and c@d.example, api_key=value123")
    assert result.counts == {"email": 2, "secret": 1}


def test_a_luhn_invalid_number_is_not_called_a_card() -> None:
    assert "card" not in redact("order 4111 1111 1111 1112 shipped").counts


@pytest.mark.parametrize("hostile", ["a-" * 500_000, "1 " * 500_000, "." * 1_000_000, "password" * 100_000, "a@" * 500_000])
def test_hostile_input_cannot_stall_the_redactor(hostile: str) -> None:
    started = time.perf_counter()
    redact(hostile)
    assert time.perf_counter() - started < 5
