"""Deterministic confidence labels.

The rules are fixed here and never delegated to the LLM:
  missing > conflicting > stale > supported > single_source

A claim's label depends on the *group* of claims sharing its (subject, attribute):
two claims with different values for the same subject and attribute are both
"conflicting", even if each is individually backed by a single source. A value
that appears in different words ("Node.js 18" and "Node 18 (LTS)") is one value:
normalize_value canonicalizes before comparing so paraphrases do not become
conflicts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.models import Label

# Words that carry no meaning for whether two values agree.
_FILLER = frozenset({
    "the", "a", "an", "of", "for", "per", "every", "each", "about", "approximately", "approx",
    "is", "are", "was", "were", "be", "been", "at", "on", "in", "to", "and", "with", "by", "from",
    "as", "it", "its", "day", "days", "day.", "cycle", "automatically", "auto", "monthly", "month",
    "months", "weekly", "week", "weeks", "daily", "yearly", "year", "years", "now", "only", "just",
    "lts", "js", "ver", "version", "v", "total", "totally", "currently", "currently.",
})
_NUMBER_SEPARATORS = re.compile(r"(\d)[,.\s]+(?=\d)")
_PAREN = re.compile(r"\([^)]*\)")
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class EvidenceRef:
    """Where one piece of evidence lives: a chunk id, its quote, and when it happened."""

    chunk_id: int
    source_id: int
    quote: str
    occurred_at: datetime | None


@dataclass
class Candidate:
    """One extracted claim before labels are applied. `key` groups candidates for conflict detection."""

    key: tuple[str, str]  # (subject, attribute)
    kind: str
    value: str | None
    statement: str
    evidence: list[EvidenceRef] = field(default_factory=list)
    external_id: str | None = None


def normalize_value(value: str) -> str:
    """Canonical form for agreement checks: lowercase, drop parentheticals and filler, sort tokens."""
    text = value.strip().lower()
    text = _PAREN.sub(" ", text)
    text = _NUMBER_SEPARATORS.sub(r"\1", text)  # "3,800" -> "3800"; "60-day" keeps its hyphen split below
    raw = [t for t in _TOKEN_SPLIT.split(text) if t]
    tokens = sorted({t for t in raw if t not in _FILLER})
    if not tokens:  # the value IS a filler word ("daily" vs "weekly") — it must not collapse to ""
        tokens = sorted(set(raw))
    return " ".join(tokens)


def distinct_values(candidates: list[Candidate]) -> list[str]:
    seen: list[str] = []
    for candidate in candidates:
        if candidate.value is None:
            continue
        normalized = normalize_value(candidate.value)
        if normalized not in seen:
            seen.append(normalized)
    return seen


def merge_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """Merge candidates that share a key AND a normalized value into one claim with combined evidence.

    This is what keeps paraphrases ("Node.js 18" and "Node 18 (LTS)") from becoming fake conflicts:
    they are one value in different words, so they become one claim backed by every source that said it.
    """
    merged: dict[tuple[tuple[str, str], str], Candidate] = {}
    order: list[tuple[tuple[str, str], str]] = []
    for candidate in candidates:
        norm = normalize_value(candidate.value) if candidate.value is not None else ""
        key = (candidate.key, norm)
        existing = merged.get(key)
        if existing is None:
            merged[key] = candidate
            order.append(key)
        else:
            known = {e.chunk_id for e in existing.evidence}
            for ref in candidate.evidence:
                if ref.chunk_id not in known:
                    existing.evidence.append(ref)
    return [merged[key] for key in order]


def newest_evidence_date(candidate: Candidate) -> datetime | None:
    dates = [e.occurred_at for e in candidate.evidence if e.occurred_at is not None]
    return max(dates) if dates else None


def is_stale(candidate: Candidate, as_of: date, stale_after_days: int) -> bool:
    """Stale when the newest evidence is older than stale_after_days before as_of."""
    newest = newest_evidence_date(candidate)
    if newest is None:
        return False
    reference = newest.date() if newest.tzinfo else newest.replace(tzinfo=None).date()
    cutoff = as_of - timedelta(days=stale_after_days)
    return reference < cutoff


def classify(
    candidates: list[Candidate], as_of: date, stale_after_days: int
) -> dict[int, Label]:
    """Return {id(candidate): label} for every candidate, applying the precedence rules.

    id(candidate) is used as the key because candidates are mutable dataclasses created fresh per run.
    """
    groups: dict[tuple[str, str], list[Candidate]] = {}
    for candidate in candidates:
        groups.setdefault(candidate.key, []).append(candidate)

    labels: dict[int, Label] = {}
    for group in groups.values():
        values = distinct_values(group)
        for candidate in group:
            if candidate.value is None:
                labels[id(candidate)] = Label.MISSING
            elif len(values) > 1:
                labels[id(candidate)] = Label.CONFLICTING
            elif is_stale(candidate, as_of, stale_after_days):
                labels[id(candidate)] = Label.STALE
            elif len({e.source_id for e in candidate.evidence}) >= 2:
                labels[id(candidate)] = Label.SUPPORTED
            else:
                labels[id(candidate)] = Label.SINGLE_SOURCE
    return labels


def label_rank(label: Label) -> int:
    """Higher rank wins when merging evidence across a conflict group."""
    return {
        Label.MISSING: 0,
        Label.CONFLICTING: 1,
        Label.STALE: 2,
        Label.SUPPORTED: 3,
        Label.SINGLE_SOURCE: 4,
    }[label]
