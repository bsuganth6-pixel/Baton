"""demo-data and ground_truth.json must agree with each other, and the data must be safely synthetic."""
from __future__ import annotations

import base64
import csv
import json
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

DEMO = Path(__file__).resolve().parents[2] / "demo-data"
NON_SOURCE_FILES = {"README.md", "ground_truth.json"}

TRUTH: dict[str, Any] = json.loads((DEMO / "ground_truth.json").read_text(encoding="utf-8"))
SOURCES = {s["id"]: s for s in TRUTH["sources"]}
CLAIMS = {c["id"]: c for c in TRUTH["claims"]}
AS_OF = date.fromisoformat(TRUTH["as_of"])
STALE_CUTOFF = AS_OF - timedelta(days=TRUTH["stale_after_days"])
ALL_TEXT = {p.relative_to(DEMO).as_posix(): p.read_text(encoding="utf-8") for p in DEMO.rglob("*") if p.is_file()}


def searchable_strings(path: Path) -> list[str]:
    """Text a quote may come from: every string value in a JSON file, else the whole file."""
    raw = path.read_text(encoding="utf-8")
    if path.suffix != ".json":
        return [raw]
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            found.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(json.loads(raw))
    return found


def appears_in(source_id: str, needle: str) -> bool:
    return any(needle in text for text in searchable_strings(DEMO / SOURCES[source_id]["path"]))


def own_clearance(claim: dict[str, Any]) -> int:
    return max(SOURCES[e["source"]]["clearance"] for e in claim["evidence"])


def derive_label(claim: dict[str, Any]) -> str:
    """The documented label rules, applied in order of precedence."""
    if claim["value"] is None:
        return "missing"
    if claim.get("conflict_id"):
        return "conflicting"
    if max(date.fromisoformat(e["date"]) for e in claim["evidence"]) < STALE_CUTOFF:
        return "stale"
    if len({e["source"] for e in claim["evidence"]}) >= 2:
        return "supported"
    return "single_source"


# --- the files and the ground truth agree ---------------------------------------------------------

def test_demo_data_readme_exists() -> None:
    assert (DEMO / "README.md").is_file()


def test_every_file_is_a_listed_source_and_every_source_is_a_file() -> None:
    on_disk = {p.relative_to(DEMO).as_posix() for p in DEMO.rglob("*") if p.is_file()} - NON_SOURCE_FILES
    assert {s["path"] for s in TRUTH["sources"]} == on_disk


def test_ids_are_unique() -> None:
    for key in ("sources", "claims", "conflicts", "expected_questions",
                "planted_secrets", "planted_pii", "planted_injections"):
        ids = [item["id"] for item in TRUTH[key]]
        assert len(ids) == len(set(ids)), key


def test_json_and_csv_sources_parse() -> None:
    for source in TRUTH["sources"]:
        path = DEMO / source["path"]
        if path.suffix == ".json":
            json.loads(path.read_text(encoding="utf-8"))
        elif path.suffix == ".csv":
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.reader(handle))
            assert len(rows) > 1
            assert len({len(row) for row in rows}) == 1, "ragged CSV"


@pytest.mark.parametrize("claim_id", list(CLAIMS))
def test_every_evidence_quote_appears_verbatim_in_its_source(claim_id: str) -> None:
    for evidence in CLAIMS[claim_id]["evidence"]:
        assert evidence["source"] in SOURCES
        assert appears_in(evidence["source"], evidence["quote"]), evidence


@pytest.mark.parametrize("item", TRUTH["planted_secrets"], ids=lambda i: i["id"])
def test_planted_secrets_appear_in_their_source(item: dict[str, Any]) -> None:
    assert appears_in(item["source"], item["value"])
    assert item["secret"] in item["value"]


@pytest.mark.parametrize("item", TRUTH["planted_pii"], ids=lambda i: i["id"])
def test_planted_pii_appears_in_its_source(item: dict[str, Any]) -> None:
    assert appears_in(item["source"], item["value"])


@pytest.mark.parametrize("item", TRUTH["planted_injections"], ids=lambda i: i["id"])
def test_planted_injections_appear_in_their_source(item: dict[str, Any]) -> None:
    assert appears_in(item["source"], item["text"])


# --- the scenario has what the demo promises ------------------------------------------------------

def test_totals_match_the_lists() -> None:
    labels = [c["expected_label"] for c in TRUTH["claims"]]
    assert TRUTH["expected_totals"] == {
        "claims": len(TRUTH["claims"]),
        "conflicts": len(TRUTH["conflicts"]),
        "stale": labels.count("stale"),
        "missing": labels.count("missing"),
        "secrets": len(TRUTH["planted_secrets"]),
        "pii": len(TRUTH["planted_pii"]),
        "injections": len(TRUTH["planted_injections"]),
    }


def test_every_label_is_represented_and_stale_and_missing_are_exactly_one() -> None:
    labels = [c["expected_label"] for c in TRUTH["claims"]]
    assert set(labels) == {"supported", "single_source", "conflicting", "stale", "missing"}
    assert labels.count("stale") == 1
    assert labels.count("missing") == 1
    assert len(TRUTH["conflicts"]) >= 3


def test_expected_labels_follow_the_documented_rules() -> None:
    for claim in TRUTH["claims"]:
        assert derive_label(claim) == claim["expected_label"], claim["id"]


def test_conflicts_are_exactly_the_claims_that_disagree() -> None:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for claim in TRUTH["claims"]:
        groups[(claim["subject"], claim["attribute"])].append(claim)
    disagreeing = {key for key, members in groups.items() if len({m["value"] for m in members}) > 1}
    assert disagreeing == {(c["subject"], c["attribute"]) for c in TRUTH["conflicts"]}
    for conflict in TRUTH["conflicts"]:
        members = [CLAIMS[i] for i in conflict["claim_ids"]]
        assert {m["conflict_id"] for m in members} == {conflict["id"]}
        assert len({m["value"] for m in members}) >= 2
        assert {(m["subject"], m["attribute"]) for m in members} == {(conflict["subject"], conflict["attribute"])}
    in_conflicts = {i for c in TRUTH["conflicts"] for i in c["claim_ids"]}
    assert in_conflicts == {c["id"] for c in TRUTH["claims"] if c.get("conflict_id")}


def test_required_clearance_follows_the_acl_rule() -> None:
    peers = {c["id"]: c["claim_ids"] for c in TRUTH["conflicts"]}
    for claim in TRUTH["claims"]:
        group = peers[claim["conflict_id"]] if claim.get("conflict_id") else [claim["id"]]
        assert claim["required_clearance"] == max(own_clearance(CLAIMS[i]) for i in group), claim["id"]


def test_clearance_edge_cases_are_all_exercised() -> None:
    assert {c["required_clearance"] for c in TRUTH["claims"]} == {1, 2, 3}
    # a conflict whose sides differ in clearance: the visible side must not hint at the hidden one
    assert any(len({own_clearance(CLAIMS[i]) for i in c["claim_ids"]}) > 1 for c in TRUTH["conflicts"])
    # a claim backed by sources of different clearance: it needs the highest
    assert any(len({SOURCES[e["source"]]["clearance"] for e in c["evidence"]}) > 1 for c in TRUTH["claims"])


def test_evidence_dates_are_not_after_as_of() -> None:
    for claim in TRUTH["claims"]:
        for evidence in claim["evidence"]:
            assert date.fromisoformat(evidence["date"]) <= AS_OF, (claim["id"], evidence)


def test_expected_questions_and_decoys_point_at_real_things() -> None:
    conflict_ids = {c["id"] for c in TRUTH["conflicts"]}
    for question in TRUTH["expected_questions"]:
        if question["kind"] == "conflict":
            assert question["target"] in conflict_ids
        else:
            assert CLAIMS[question["target"]]["expected_label"] == question["kind"]
    asked = sorted(q["target"] for q in TRUTH["expected_questions"] if q["kind"] == "conflict")
    assert asked == sorted(conflict_ids)
    for decoy in TRUTH["decoys"]:
        assert CLAIMS[decoy["claim_id"]]["expected_label"] == "supported"


# --- the data is synthetic by construction (rules 6 and 7) ----------------------------------------

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
PHONE = re.compile(r"\+\d{1,3}[ -]?\d[\d -]{6,}\d")
JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
REAL_SECRET_FORMATS = {
    "aws access key": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    "github token": r"\bgh[pousr]_[A-Za-z0-9]{36,}\b",
    "slack token": r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    "stripe key": r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}",
    "google api key": r"\bAIza[0-9A-Za-z_-]{35}\b",
    "private key block": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "openai style key": r"\bsk-[A-Za-z0-9]{20,}",
    "anthropic key": r"\bsk-ant-[A-Za-z0-9_-]{20,}",
}


def test_emails_use_reserved_example_domains() -> None:
    for name, text in ALL_TEXT.items():
        for match in EMAIL.finditer(text):
            assert match.group(1).lower().endswith((".example", ".example.internal")), (name, match.group(0))


def test_phone_numbers_use_the_invalid_mobile_prefix() -> None:
    for name, text in ALL_TEXT.items():
        for match in PHONE.finditer(text):
            assert match.group(0).startswith("+91 5550"), (name, match.group(0))


def test_no_real_provider_secret_formats() -> None:
    for name, text in ALL_TEXT.items():
        for label, pattern in REAL_SECRET_FORMATS.items():
            assert not re.search(pattern, text), (name, label)


def test_every_jwt_is_provably_fake() -> None:
    tokens = [(n, m.group(0)) for n, t in ALL_TEXT.items() for m in JWT.finditer(t)]
    assert tokens, "expected the planted bearer token"
    for name, token in tokens:
        payload = token.split(".")[1]
        decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        assert decoded["note"] == "synthetic", name


def test_exports_are_flagged_synthetic() -> None:
    for name in ("github/export.json", "chat/dev-channel.json", "chat/leads-private.json", "ground_truth.json"):
        assert json.loads(ALL_TEXT[name])["synthetic"] is True, name
