"""Confidence engine rules: normalization, decoy merging and label precedence.

These pin the exact behaviours the demo data depends on:
- filler words must not erase a value that IS a filler word ("daily" vs "weekly")
- paraphrases of one value must not become conflicts ("Node.js 18" == "Node 18 (LTS)")
- label precedence is missing > conflicting > stale > supported > single_source
"""
from __future__ import annotations

from datetime import datetime, timedelta, UTC

from app.ingest.confidence import Candidate, EvidenceRef, Label, classify, merge_candidates, normalize_value


def _ref(chunk_id: int, source_id: int, days_ago: int = 0) -> EvidenceRef:
    return EvidenceRef(chunk_id=chunk_id, source_id=source_id, quote=f"quote {chunk_id}",
                       occurred_at=datetime.now(UTC) - timedelta(days=days_ago))


def test_filler_word_values_stay_distinct() -> None:
    """daily vs weekly must not collapse to the same normalized value."""
    assert normalize_value("daily") != normalize_value("weekly")
    assert normalize_value("daily") == "daily"
    assert normalize_value("weekly") == "weekly"


def test_paraphrases_normalize_to_one_value() -> None:
    assert normalize_value("Node.js 18") == normalize_value("Node 18 (LTS)")
    assert normalize_value("INR 3,800") == normalize_value("inr 3800")


def test_merge_candidates_keeps_evidence_of_agreeing_sources() -> None:
    first = Candidate(key=("campus-api", "runtime"), kind="fact", value="Node.js 18",
                      statement="s", evidence=[_ref(1, 1)])
    second = Candidate(key=("campus-api", "runtime"), kind="fact", value="Node 18 (LTS)",
                       statement="s", evidence=[_ref(2, 2)])
    merged = merge_candidates([first, second])
    assert len(merged) == 1
    assert {e.chunk_id for e in merged[0].evidence} == {1, 2}


def test_merge_candidates_keeps_distinct_values_apart() -> None:
    daily = Candidate(key=("production-database", "backup-frequency"), kind="fact", value="daily",
                      statement="s", evidence=[_ref(1, 1)])
    weekly = Candidate(key=("production-database", "backup-frequency"), kind="fact", value="weekly",
                       statement="s", evidence=[_ref(2, 2)])
    merged = merge_candidates([daily, weekly])
    assert len(merged) == 2


def test_classify_precedence_missing_beats_everything() -> None:
    candidate = Candidate(key=("a", "b"), kind="fact", value=None, statement="s", evidence=[_ref(1, 1, 400)])
    labels = classify([candidate], as_of=datetime.now(UTC).date(), stale_after_days=30)
    assert labels[id(candidate)] is Label.MISSING


def test_classify_precedence_conflicting_beats_stale() -> None:
    old_a = Candidate(key=("a", "b"), kind="fact", value="x", statement="s", evidence=[_ref(1, 1, 400)])
    old_b = Candidate(key=("a", "b"), kind="fact", value="y", statement="s", evidence=[_ref(2, 2, 400)])
    labels = classify([old_a, old_b], as_of=datetime.now(UTC).date(), stale_after_days=30)
    assert labels[id(old_a)] is Label.CONFLICTING
    assert labels[id(old_b)] is Label.CONFLICTING


def test_classify_stale_when_evidence_older_than_window() -> None:
    old = Candidate(key=("a", "b"), kind="fact", value="x", statement="s", evidence=[_ref(1, 1, 400)])
    labels = classify([old], as_of=datetime.now(UTC).date(), stale_after_days=30)
    assert labels[id(old)] is Label.STALE


def test_classify_supported_when_two_sources_agree() -> None:
    fresh = Candidate(key=("a", "b"), kind="fact", value="x", statement="s",
                      evidence=[_ref(1, 1), _ref(2, 2)])
    labels = classify([fresh], as_of=datetime.now(UTC).date(), stale_after_days=30)
    assert labels[id(fresh)] is Label.SUPPORTED


def test_classify_single_source_when_one_source_backs_value() -> None:
    fresh = Candidate(key=("a", "b"), kind="fact", value="x", statement="s", evidence=[_ref(1, 1)])
    labels = classify([fresh], as_of=datetime.now(UTC).date(), stale_after_days=30)
    assert labels[id(fresh)] is Label.SINGLE_SOURCE
