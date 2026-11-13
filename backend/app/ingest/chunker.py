"""Split text into chunks small enough for a model and large enough to keep their meaning."""
from __future__ import annotations

import re

MAX_CHARS = 1500
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$")
_FENCE = re.compile(r"^[ \t]*(```|~~~)")


def _hard_split(paragraph: str, max_chars: int) -> list[str]:
    pieces, rest = [], paragraph.strip()
    while len(rest) > max_chars:
        cut = rest.rfind(" ", 0, max_chars)
        cut = cut if cut > max_chars // 2 else max_chars
        pieces.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    return [*pieces, rest] if rest else pieces


def pack_paragraphs(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    """Group paragraphs into chunks of at most max_chars; one oversized paragraph is cut at spaces."""
    chunks: list[str] = []
    current = ""
    for paragraph in re.split(r"\n[ \t]*\n", text.strip()):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        for piece in _hard_split(paragraph, max_chars):
            if current and len(current) + 2 + len(piece) > max_chars:
                chunks.append(current)
                current = piece
            else:
                current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def split_markdown(body: str, max_chars: int = MAX_CHARS) -> list[tuple[str, str]]:
    """(locator, text) per heading section. Heading-only sections are dropped; long ones are split."""
    sections: list[tuple[str, list[str]]] = []
    trail: list[tuple[int, str]] = []
    where, lines = "document", []
    in_fence = False
    for line in body.split("\n"):
        heading = None if in_fence else _HEADING.match(line)
        if _FENCE.match(line):
            in_fence = not in_fence
        if heading:
            sections.append((where, lines))
            level, title = len(heading.group(1)), heading.group(2).strip()
            trail = [(lv, t) for lv, t in trail if lv < level] + [(level, title)]
            where, lines = " > ".join(t for _, t in trail), [line]
        else:
            lines.append(line)
    sections.append((where, lines))

    result: list[tuple[str, str]] = []
    for where, lines in sections:
        text = "\n".join(lines).strip()
        if not any(line.strip() and not _HEADING.match(line) for line in lines):
            continue
        parts = pack_paragraphs(text, max_chars)
        for index, part in enumerate(parts, start=1):
            result.append((where if len(parts) == 1 else f"{where} (part {index})", part))
    return result
