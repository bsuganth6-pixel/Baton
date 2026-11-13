"""Redact secrets and personal data.

Runs before anything is stored, shown or sent to an LLM. It favours safety over detail: a false positive
hides a word, a false negative leaks a secret. Names of people are kept on purpose, because a handover
has to say who owns what. Every pattern is bounded so hostile input cannot make it run for long.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

Replacer = Callable[[re.Match[str]], str | None]
SECRET_WORDS = (
    r"(?:password|passwd|pwd|passphrase|secret|token|api[_-]?key|apikey|access[_-]?key|private[_-]?key|credential)"
)


def mark(kind: str) -> str:
    return f"[REDACTED:{kind}]"


@dataclass
class RedactionResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)


def _whole(kind: str) -> Replacer:
    return lambda match: mark(kind)


def _value_only(kind: str) -> Replacer:
    """Replace only the group named "value", so the label in front of it stays readable."""

    def replace(match: re.Match[str]) -> str | None:
        if match.group("value").startswith("[REDACTED:"):
            return None
        start, end = match.span("value")
        whole, offset = match.group(0), match.start()
        return whole[: start - offset] + mark(kind) + whole[end - offset :]

    return replace


def _entropy(token: str) -> float:
    counts = Counter(token)
    return -sum(n / len(token) * math.log2(n / len(token)) for n in counts.values())


def _random_looking(match: re.Match[str]) -> str | None:
    token = match.group(0)
    if re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", token):
        return None  # git and sha-256 digests identify things; they are not secrets
    if not (re.search(r"\d", token) and re.search(r"[A-Za-z]", token)):
        return None
    return mark("api_key") if _entropy(token) >= 3.5 else None


def _luhn_ok(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        digit = int(char)
        if index % 2:
            digit = digit * 2 - 9 if digit > 4 else digit * 2
        total += digit
    return total % 10 == 0


def _card(match: re.Match[str]) -> str | None:
    digits = re.sub(r"\D", "", match.group(0))
    return mark("card") if 13 <= len(digits) <= 19 and _luhn_ok(digits) else None


def _rule(kind: str, pattern: str, replacer: Replacer, flags: int = 0) -> tuple[str, re.Pattern[str], Replacer]:
    return kind, re.compile(pattern, flags), replacer


# Order matters: specific shapes first, broad ones last.
RULES: list[tuple[str, re.Pattern[str], Replacer]] = [
    _rule("private_key", r"-----BEGIN [A-Z ]{0,20}PRIVATE KEY-----.*?-----END [A-Z ]{0,20}PRIVATE KEY-----",
          _whole("private_key"), re.S),
    # scheme://user:PASSWORD@host
    _rule("password", r"(?P<head>\b[a-z][a-z0-9+.-]{0,20}://[^\s/:@]{1,100}:)(?P<value>[^\s/@]{1,200})(?=@)",
          _value_only("password"), re.I),
    _rule("token", r"\b(?P<head>Bearer[ \t]+)(?P<value>[A-Za-z0-9._~+/=-]{16,})", _value_only("token"), re.I),
    _rule("token", r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*", _whole("token")),
    _rule("api_key",
          r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{36,}\b|\bxox[abprs]-[A-Za-z0-9-]{10,}"
          r"|\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}|\bAIza[0-9A-Za-z_-]{35}\b|\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}",
          _whole("api_key")),
    # smtp_password: VALUE, JWT_SECRET=VALUE (anchored on the keyword, so scanning stays linear)
    _rule("secret",
          rf"(?P<head>{SECRET_WORDS}[A-Za-z0-9_.-]{{0,40}}[ \t]{{0,3}}[:=][ \t]{{0,3}}[\"']?)"
          r"(?P<value>[^\s\"',;]{1,300})", _value_only("secret"), re.I),
    # "the admin password is Campus@2026!": a value that holds a digit or symbol, so "token is signed" survives
    _rule("secret",
          r"(?P<head>\b(?:password|passcode|passphrase|secret|token|api key|apikey|pin)"
          r"[ \t]{1,3}(?:is|was|are|=|:)[ \t]{1,3})"
          r"(?P<value>(?=\S{0,200}[\d@#$%^&*!+=~/\\_-])\S{6,200})", _value_only("secret"), re.I),
    _rule("api_key", r"\b[A-Za-z][A-Za-z0-9]{0,30}_(?:key|token|secret|pwd|pass|password)_[A-Za-z0-9]{12,}\b",
          _whole("api_key")),
    _rule("api_key", r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])", _random_looking),
    _rule("email",
          r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,24}",
          _whole("email")),
    _rule("upi_id", r"(?<![A-Za-z0-9._%+@-])[A-Za-z0-9._-]{2,64}@[A-Za-z][A-Za-z0-9]{1,30}(?![A-Za-z0-9@-]|\.[A-Za-z0-9])",
          _whole("upi_id")),
    _rule("phone", r"(?<![\w+])\+\d{1,3}(?:[ \t-]?\d{2,5}){2,4}(?!\d)", _whole("phone")),
    _rule("phone", r"(?<![\d.])[6-9]\d{9}(?!\d)", _whole("phone")),
    _rule("phone", r"(?<!\d)\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)", _whole("phone")),
    _rule("card", r"(?<![\d-])\d(?:[ -]?\d){12,18}(?![\d-])", _card),
    _rule("national_id", r"(?<!\d)\d{4}[ -]\d{4}[ -]\d{4}(?!\d)", _whole("national_id")),
    _rule("national_id", r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)", _whole("national_id")),
    _rule("pan", r"(?<![A-Za-z0-9])[A-Z]{5}\d{4}[A-Z](?![A-Za-z0-9])", _whole("pan")),
    _rule("ifsc", r"(?<![A-Za-z0-9])[A-Z]{4}0[A-Z0-9]{6}(?![A-Za-z0-9])", _whole("ifsc")),
    _rule("bank_account",
          r"(?P<head>\b(?:a/c|acct|account)(?:[ \t]+(?:no|number|num|#))?\.?[ \t]*[:#-]?[ \t]*)(?P<value>\d{9,18})\b",
          _value_only("bank_account"), re.I),
    _rule("date_of_birth",
          r"(?P<head>\b(?:date[ \t]+of[ \t]+birth|dob|d\.o\.b\.?|born(?:[ \t]+on)?)[ \t]*[:\-]?[ \t]*)"
          r"(?P<value>\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}-\d{2}-\d{2}|[A-Za-z]{3,9}[ \t]+\d{1,2},?[ \t]+\d{4})",
          _value_only("date_of_birth"), re.I),
    _rule("student_id",
          r"(?P<head>\b(?:roll|enrol{1,2}ment|student|employee|emp)[ \t]{0,3}(?:no|number|id|#)\.?[ \t]*[:#-]?[ \t]*)"
          r"(?P<value>[A-Za-z0-9-]{4,30})", _value_only("student_id"), re.I),
    _rule("student_id", r"(?<![A-Za-z0-9])\d{2}[A-Z]{2,4}\d{3,5}(?![A-Za-z0-9])", _whole("student_id")),
]


def redact(text: str) -> RedactionResult:
    """Replace secrets and personal data with [REDACTED:kind] and count what was replaced."""
    counts: Counter[str] = Counter()
    for kind, pattern, replacer in RULES:

        def substitute(match: re.Match[str], kind: str = kind, replacer: Replacer = replacer) -> str:
            replacement = replacer(match)
            if replacement is None:
                return match.group(0)
            counts[kind] += 1
            return replacement

        text = pattern.sub(substitute, text)
    return RedactionResult(text=text, counts=dict(counts))
