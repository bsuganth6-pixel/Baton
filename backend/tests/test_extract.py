"""Turning files into segments, and segments into well-formed chunks."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.ingest.chunker import MAX_CHARS, pack_paragraphs, split_markdown
from app.ingest.extract import (
    ExtractionError,
    Segment,
    decode_text,
    detect_kind,
    extract_segments,
    parse_when,
    split_front_matter,
)
from app.ingest.validate import IngestError, clean_filename, validate_upload
from app.models import SourceKind

DEMO = Path(__file__).resolve().parents[2] / "demo-data"


def demo(path: str, kind: SourceKind) -> list[Segment]:
    return extract_segments(kind, decode_text((DEMO / path).read_bytes()))


def find(segments: list[Segment], needle: str) -> Segment:
    matches = [s for s in segments if needle in s.text]
    assert len(matches) == 1, f"{len(matches)} segments contain {needle!r}"
    return matches[0]


# --- reading ----------------------------------------------------------------------------------------

def test_decode_handles_utf8_bom_utf16_and_windows_1252() -> None:
    assert decode_text("\ufeffhello\r\nworld".encode()) == "hello\nworld"
    assert decode_text("héllo".encode("utf-16")) == "héllo"
    assert decode_text("caf\xe9 d\xe9j\xe0".encode("cp1252")) == "café déjà"


@pytest.mark.parametrize("data", [b"\x00\x01\x02binary", b"  \n\t ", bytes(range(1, 31)) * 40, b"\xff\xfe\xff"])
def test_decode_refuses_binary_and_blank_files(data: bytes) -> None:
    with pytest.raises(ExtractionError):
        decode_text(data)


def test_parse_when_understands_dates_offsets_and_unix_time() -> None:
    assert parse_when("2026-06-02").date() == date(2026, 6, 2)  # type: ignore[union-attr]
    assert parse_when("2026-09-02T14:41:00+05:30").isoformat() == "2026-09-02T14:41:00+05:30"  # type: ignore[union-attr]
    assert parse_when("1700000000").year == 2023  # type: ignore[union-attr]
    assert parse_when("not a date") is None and parse_when(None) is None and parse_when(True) is None


def test_front_matter_is_split_from_the_body() -> None:
    meta, body = split_front_matter("---\ntitle: T\nDate: 2026-01-01\n---\n# Heading\ntext")
    assert meta == {"title": "T", "date": "2026-01-01"}
    assert body.startswith("# Heading")
    assert split_front_matter("# no front matter")[0] == {}


# --- kinds -------------------------------------------------------------------------------------------

def test_kinds_are_detected_from_content() -> None:
    assert detect_kind(".csv", "a,b\n1,2") is SourceKind.TASKS
    assert detect_kind(".json", '{"issues": []}') is SourceKind.GITHUB
    assert detect_kind(".json", '{"messages": []}') is SourceKind.CHAT
    assert detect_kind(".json", '[{"text": "hi"}]') is SourceKind.CHAT
    assert detect_kind(".json", '{"anything": 1}') is SourceKind.NOTES
    assert detect_kind(".md", "Q1. a?\nA1. b\n\nQ2. c?\nA2. d") is SourceKind.LEAVER_QA
    assert detect_kind(".txt", "just some notes") is SourceKind.NOTES


def test_a_json_file_that_is_not_json_is_a_failure_not_prose() -> None:
    with pytest.raises(ExtractionError) as caught:
        detect_kind(".json", "{broken")
    assert caught.value.message == "The file is not valid JSON."


def test_every_demo_file_is_detected_as_the_kind_the_manifest_says() -> None:
    manifest = json.loads((DEMO / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["sources"]:
        path = DEMO / entry["path"]
        assert detect_kind(path.suffix, decode_text(path.read_bytes())).value == entry["kind"], entry["path"]


# --- the demo data, kind by kind ----------------------------------------------------------------------

def test_chat_has_one_segment_per_message_with_author_time_and_place() -> None:
    segments = demo("chat/dev-channel.json", SourceKind.CHAT)
    assert len(segments) == 17
    reply = find(segments, "The registrar login was never handed over.")
    assert (reply.author, reply.locator) == ("arjun", "#dev 2026-09-02 14:41")
    assert reply.occurred_at is not None and reply.occurred_at.isoformat() == "2026-09-02T14:41:00+05:30"
    assert reply.text.startswith("arjun: ")


def test_tasks_have_one_segment_per_row_and_an_unowned_task_says_so() -> None:
    segments = demo("tasks/tasks.csv", SourceKind.TASKS)
    assert len(segments) == 10
    unowned = find(segments, "T-152")
    assert "owner: (not assigned)" in unowned.text and "No owner assigned since Kabir left." in unowned.text
    assert unowned.author is None and unowned.locator == "task T-152"
    assert unowned.occurred_at is not None and unowned.occurred_at.date() == date(2026, 10, 12)
    assert find(segments, "T-171").author == "arjun"


def test_github_export_yields_readme_issues_comments_prs_and_commits() -> None:
    segments = demo("github/export.json", SourceKind.GITHUB)
    locators = {s.locator for s in segments}
    assert {"README: campus-api > Stack", "issue #49", "issue #63 comment 1", "PR #60", "PR #60 comment 1",
            "commit d52e6b1"} <= locators
    comment = find(segments, "The reset token expired after 10 minutes")
    assert (comment.author, comment.locator) == ("arjun", "issue #63 comment 1")
    assert comment.occurred_at is not None and comment.occurred_at.isoformat() == "2026-06-22T10:20:00+05:30"
    assert find(segments, "Posters over 5 MB").occurred_at.date() == date(2026, 1, 16)  # type: ignore[union-attr]


def test_notes_carry_front_matter_dates_authors_and_heading_paths() -> None:
    segments = demo("notes/architecture-overview.md", SourceKind.NOTES)
    api = find(segments, "rate limited to 100 requests")
    assert api.locator == "CampusConnect architecture overview > API (campus-api)"
    assert api.author == "Arjun Mehta" and api.occurred_at is not None and api.occurred_at.date() == date(2026, 6, 2)
    assert segments[0].locator == "document header" and "title: CampusConnect architecture overview" in segments[0].text


def test_leaver_answers_are_paired_with_their_questions_and_unanswered_ones_are_kept() -> None:
    segments = demo("leaver_qa/exit-qa.md", SourceKind.LEAVER_QA)
    assert len(segments) == 8  # header plus seven questions
    third = find(segments, "A3.")
    assert third.text.startswith("Q3. Who owns the domain registration and its renewals?\nA3. I do not know.")
    assert (third.locator, third.author) == ("question 3", "Arjun Mehta")
    assert third.occurred_at is not None and third.occurred_at.date() == date(2026, 10, 14)
    assert find(segments, "Q4.").text.endswith("A4. (not answered)")


# --- failures ------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("kind", "text"),
    [
        (SourceKind.GITHUB, "{not json"),
        (SourceKind.GITHUB, '{"unrelated": 1}'),
        (SourceKind.CHAT, '{"messages": "no"}'),
        (SourceKind.CHAT, '{"messages": []}'),
        (SourceKind.TASKS, "only a header, a, b"),
        (SourceKind.TASKS, "a,a\n1,2"),
        (SourceKind.TASKS, "a,b\n1,2,3"),
        (SourceKind.NOTES, "---\ntitle: nothing else\n---\n\n"),
    ],
)
def test_unusable_content_raises_a_user_safe_error(kind: SourceKind, text: str) -> None:
    if kind is SourceKind.NOTES:
        assert extract_segments(kind, text)  # a header alone is still content
        return
    with pytest.raises(ExtractionError) as caught:
        extract_segments(kind, text)
    assert caught.value.message and "Traceback" not in caught.value.message


def test_deeply_nested_json_is_refused_not_crashed() -> None:
    with pytest.raises(ExtractionError):
        extract_segments(SourceKind.GITHUB, "[" * 100_000)


def test_unknown_json_is_flattened_into_path_value_lines() -> None:
    segments = extract_segments(SourceKind.NOTES, '{"service": {"name": "api", "ports": [80, 443]}, "empty": null}')
    assert segments[0].text == "service.name: api\nservice.ports[0]: 80\nservice.ports[1]: 443"


# --- chunker -------------------------------------------------------------------------------------------

def test_long_sections_are_split_and_short_ones_are_not() -> None:
    long_body = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(12))
    parts = split_markdown(f"# Title\n\nshort intro\n\n## Long\n\n{long_body}")
    assert [p[0] for p in parts][0] == "Title"
    long_parts = [p for p in parts if p[0].startswith("Title > Long")]
    assert len(long_parts) > 1 and all(len(text) <= MAX_CHARS for _, text in long_parts)
    assert long_parts[0][0] == "Title > Long (part 1)"


def test_heading_only_sections_are_dropped_and_code_fences_are_not_headings() -> None:
    parts = split_markdown("# Only a title\n\n## Real\n\ntext\n\n```bash\n# a comment, not a heading\nls\n```\n")
    assert [p[0] for p in parts] == ["Only a title > Real"]
    assert "# a comment, not a heading" in parts[0][1]


def test_one_enormous_paragraph_is_cut_at_spaces() -> None:
    pieces = pack_paragraphs("word " * 2000)
    assert len(pieces) > 1 and all(0 < len(p) <= MAX_CHARS for p in pieces)
    assert " ".join(" ".join(pieces).split()) == " ".join(["word"] * 2000)


# --- upload policy ---------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("name", "data", "code", "status"),
    [("notes.exe", b"x", "unsupported_type", 415), ("noextension", b"x", "unsupported_type", 415),
     ("a.md", b"", "empty_file", 400), ("a.md", b"x" * 11, "file_too_large", 413)],
)
def test_policy_rejections(name: str, data: bytes, code: str, status: int) -> None:
    with pytest.raises(IngestError) as caught:
        validate_upload(name, data, max_bytes=10)
    assert (caught.value.code, caught.value.status) == (code, status)


def test_filenames_lose_paths_and_control_characters() -> None:
    assert clean_filename("../../etc/passwd.md") == "passwd.md"
    assert clean_filename("C:\\Users\\me\\notes.txt") == "notes.txt"
    assert clean_filename("a\x00b\nc.md") == "abc.md"
    assert clean_filename(None) == "upload" and len(clean_filename("x" * 500 + ".md")) == 200
    upload = validate_upload("../n.md", b"abc", 10)
    assert (upload.name, upload.extension, upload.size) == ("n.md", ".md", 3)
    assert upload.sha256 == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
