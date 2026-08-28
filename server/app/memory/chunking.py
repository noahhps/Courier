"""Splitting something long into pieces worth embedding.

Paragraph-first, because a paragraph is already the unit a person wrote in and
a model reads in. Merged up to a target size, because a one-line paragraph
embeds to noise; overlapped, because the sentence that answers a question is as
likely to sit on a boundary as anywhere else.

Pure. No database, no network, no `async` -- which is what makes it the one
part of retrieval you can be sure of before any of the rest exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ~300 tokens. Comfortably inside any encoder's window, and small enough that a
# hit points at a paragraph rather than at a page.
TARGET_CHARS = 1200
# Carried from the end of one piece into the start of the next.
OVERLAP_CHARS = 150
# Below this there is nothing to match on. "ok, thanks" is a real message and a
# useless chunk.
MIN_CHARS = 40

# extract.py labels each page of a PDF like this, on a line of its own. It is
# the only landmark a citation can use, so it is parsed out and kept as a
# column rather than left in the text -- a chunk that opens "[page 4]" reads
# badly when quoted back and embeds no better for it.
_PAGE_MARKER = re.compile(r"^\[page (\d+)\]\s*$", re.MULTILINE)

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


@dataclass(frozen=True)
class Piece:
    ordinal: int
    content: str
    page: int | None = None


def split(text: str) -> list[Piece]:
    """One body of text as the pieces worth storing.

    Never emits an empty piece: `chunks.content` is NOT NULL and would accept
    "" happily, then match nothing for the rest of time.
    """
    if not text or not text.strip():
        return []

    pieces: list[Piece] = []
    for page, body in _pages(text):
        for content in _merge(_paragraphs(body)):
            pieces.append(Piece(ordinal=len(pieces), content=content, page=page))

    # A short message is still worth keeping whole -- MIN_CHARS drops noise
    # between paragraphs, not the entire input. Without this a three-word
    # answer would vanish from history rather than merely rank badly.
    if not pieces:
        stripped = " ".join(text.split())
        if stripped:
            pieces.append(Piece(ordinal=0, content=stripped[:TARGET_CHARS]))
    return pieces


def _pages(text: str) -> list[tuple[int | None, str]]:
    """Split on `[page N]` markers, carrying each number to the text under it."""
    markers = list(_PAGE_MARKER.finditer(text))
    if not markers:
        return [(None, text)]

    sections: list[tuple[int | None, str]] = []
    head = text[: markers[0].start()].strip()
    if head:
        sections.append((None, head))
    for index, match in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        body = text[match.end() : end].strip()
        if body:
            sections.append((int(match.group(1)), body))
    return sections


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in _PARAGRAPH_BREAK.split(text) if p.strip()]


def _merge(paragraphs: list[str]) -> list[str]:
    """Paragraphs gathered up to TARGET_CHARS, with an overlapping tail.

    A single paragraph longer than the target is cut on its own, because a
    pasted error log or a model's long answer arrives as exactly that and a
    paragraph-based overlap would degenerate to no overlap precisely there.
    """
    out: list[str] = []
    buffer = ""

    def flush() -> None:
        nonlocal buffer
        body = buffer.strip()
        if len(body) >= MIN_CHARS:
            out.append(body)
        elif body and out:
            # Too small to stand alone, and there is somewhere to put it.
            out[-1] = f"{out[-1]}\n\n{body}"
        elif body:
            out.append(body)
        buffer = ""

    for paragraph in paragraphs:
        if len(paragraph) > TARGET_CHARS:
            flush()
            out.extend(_hard_wrap(paragraph))
            continue
        if buffer and len(buffer) + len(paragraph) + 2 > TARGET_CHARS:
            tail = _tail(buffer)
            flush()
            buffer = f"{tail}\n\n{paragraph}" if tail else paragraph
        else:
            buffer = f"{buffer}\n\n{paragraph}" if buffer else paragraph
    flush()
    return out


def _hard_wrap(paragraph: str) -> list[str]:
    """One oversized paragraph, cut at sentence ends where there are any."""
    out: list[str] = []
    remaining = paragraph
    while len(remaining) > TARGET_CHARS:
        window = remaining[:TARGET_CHARS]
        cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
        if cut < TARGET_CHARS // 2:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = TARGET_CHARS  # one unbroken run of characters; cut it anyway
        out.append(remaining[: cut + 1].strip())
        remaining = (remaining[max(0, cut + 1 - OVERLAP_CHARS) :]).strip()
    if remaining:
        out.append(remaining)
    return [piece for piece in out if piece]


def _tail(text: str) -> str:
    """The overlap carried into the next piece: whole words, from the end."""
    if len(text) <= OVERLAP_CHARS:
        return text.strip()
    tail = text[-OVERLAP_CHARS:]
    space = tail.find(" ")
    return tail[space + 1 :].strip() if space != -1 else tail.strip()
