"""Writing a document to disk.

Produces a .pdf or a .docx from text and puts it where the API can serve it
back. The bytes go to a directory rather than into SQLite alongside
attachments, and that is a deliberate split: an attachment is something the
user gave the conversation and belongs with it, while this is an artifact the
conversation produced -- regenerable, potentially large, and not something a
`VACUUM INTO` backup needs to carry.

The filename comes from the model, which makes it untrusted input. Everything
about `_safe_name` exists for that reason.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

from ..documents import THEMES, build_docx, build_pdf, build_pptx, build_xlsx
from .skill import Skill

FORMATS = {
    "pdf": (build_pdf, ".pdf"),
    "docx": (build_docx, ".docx"),
    "pptx": (build_pptx, ".pptx"),
    "xlsx": (build_xlsx, ".xlsx"),
}

# Generous for prose, small enough that a runaway generation cannot fill a disk.
MAX_BODY_CHARS = 200_000

# Roughly how many words each depth is worth. The model states a depth and then
# has to meet it: a stated intention the caller can check is worth far more than
# an instruction it can quietly ignore, and "write more" in a tool result is a
# correction it can act on within the same turn.
DEPTHS = {"brief": 150, "standard": 400, "detailed": 900}

# How far below target is worth sending back. Deliberately lax -- this is meant
# to catch a four-bullet "report", not to police word counts, and every refusal
# costs one of the loop's four rounds.
_SHORTFALL = 0.5

_UNSAFE = re.compile(r"[^A-Za-z0-9 ._-]")


class DocumentWriter(Skill):
    def __init__(self, directory: Path):
        super().__init__(
            name="write_document",
            description=(
                "Write a file: a PDF or Word document, a PowerPoint deck, or "
                "an Excel spreadsheet. Use when asked for a document, report, "
                "letter, deck or table rather than an answer in the chat. The "
                "user is offered the file automatically; you do not need to "
                "provide a link."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": (
                            "Name without an extension, e.g. 'Tenancy notice'."
                        ),
                    },
                    "format": {
                        "type": "string",
                        "enum": ["pdf", "docx", "pptx", "xlsx"],
                        "description": (
                            "pdf or docx for a document, pptx for slides, "
                            "xlsx for a spreadsheet."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": "Heading printed at the top of the document.",
                    },
                    "depth": {
                        "type": "string",
                        "enum": sorted(DEPTHS),
                        "description": (
                            "How much to write. 'brief' is a note or a one-"
                            "pager, around 150 words. 'standard' is a proper "
                            "document with several sections, around 400. "
                            "'detailed' is a full report, 900 or more. Choose "
                            "from what was asked for and then actually write "
                            "that much -- this is checked, and a body far "
                            "shorter than the depth you named is sent back."
                        ),
                    },
                    "theme": {
                        "type": "string",
                        "enum": sorted(THEMES),
                        "description": (
                            "Colour scheme. Everything else about the look is "
                            "chosen for you -- fonts, spacing, table banding, "
                            "the accent rule. Pick on subject, not on whim: "
                            "slate for business and reports, ink for something "
                            "formal or legal, green for anything about money "
                            "or growth, plum sparingly. Leave it out for slate."
                        ),
                    },
                    "body": {
                        "type": "string",
                        "description": "\n".join(
                            [
                                "The document's text, in this small markup:",
                                "'# Title' on the first line.",
                                "'## Section heading' for each section.",
                                "'- item' for a bullet, '1. item' for a numbered one.",
                                "'> quoted line' for a quotation.",
                                "Plain paragraphs otherwise, one per line, with a"
                                " blank line between them.",
                                "Do NOT use **bold**, *italic*, `code`, tables or"
                                " horizontal rules. This is a printed page, not"
                                " chat: those markers are stripped rather than"
                                " rendered, so put the emphasis in the wording"
                                " instead.",
                                "Write plain hyphens and quotes -- typographic ones"
                                " are substituted.",
                                "",
                                "Write the document out in full. Every '##'"
                                " section needs real content under it -- a"
                                " paragraph or three to six bullets. A heading"
                                " with a single line beneath it looks"
                                " unfinished. Each bullet should be a complete"
                                " clause that says something, not a two-word"
                                " label: 'Lead times rose from 9 to 16 days"
                                " across July and August', not 'Lead times'.",
                                "",
                                "For 'pptx' the same markup makes a deck: every"
                                " heading starts a new slide and becomes its"
                                " title, and the lines under it are that slide's"
                                " bullets. Three to six bullets per slide, one"
                                " line each; add another slide rather than"
                                " crowding one.",
                                "",
                                "For 'xlsx' send rows instead of prose: either"
                                " CSV (one row per line, commas between cells,"
                                " quotes around a cell containing a comma) or a"
                                " markdown table. Put the column headings in the"
                                " first row. Numbers are written as numbers;"
                                " anything with a leading zero stays text.",
                            ]
                        ),
                    },
                },
                "required": ["filename", "format", "body"],
            },
        )
        self.directory = directory

    async def use(
        self,
        filename: str,
        format: str,
        body: str,
        title: str = "",
        theme: str = "",
        depth: str = "",
    ) -> str:
        chosen = (format or "").strip().lower()
        if chosen not in FORMATS:
            return (
                f"{format!r} is not a format I can write. "
                f"Use one of: {', '.join(sorted(FORMATS))}."
            )

        if not (body or "").strip():
            return "The document would be empty -- give it some body text."
        if len(body) > MAX_BODY_CHARS:
            return f"That document is too long (limit {MAX_BODY_CHARS:,} characters)."

        # Prose formats only. A spreadsheet is measured in rows and a deck in
        # slides: two slides of good bullets came to 32 words and were sent
        # back for being under a 150-word target, which made the deck wordier
        # than it should have been. Terseness is the point of a slide.
        target = DEPTHS.get((depth or "").strip().lower())
        if target and chosen in ("pdf", "docx"):
            words = len(body.split())
            if words < target * _SHORTFALL:
                return (
                    f"That body is {words} words, but you asked for "
                    f"'{depth}', which is about {target}. Nothing was written. "
                    "Expand it -- give every section real content rather than a "
                    "heading and one line -- and call write_document again."
                )

        stem = _safe_name(filename)
        if not stem:
            return "That filename has no usable characters in it."

        build, suffix = FORMATS[chosen]
        try:
            data = build(title or stem, body, theme) if theme else build(title or stem, body)
        except Exception as exc:
            return f"The document couldn't be built: {type(exc).__name__}: {exc}"

        self.directory.mkdir(parents=True, exist_ok=True)
        path = _unused(self.directory, stem, suffix)
        try:
            path.write_bytes(data)
        except OSError as exc:
            return f"The document couldn't be saved: {exc}"

        size = len(data) / 1024
        # Percent-encoded: a filename with spaces in it is the common case, and
        # an unencoded link is one the user cannot click.
        return (
            f"Wrote {path.name} ({size:.0f} KB). "
            f"The user can download it from /api/documents/{quote(path.name)}."
        )


def _safe_name(raw: str) -> str:
    """A model-supplied filename reduced to something safe to join to a path.

    The model picks this, so it is untrusted. Taking only the final component
    defeats `../../etc/passwd` and `C:\\Windows\\...` in one step; stripping
    everything outside a small allowlist deals with the rest, including the
    NUL bytes and reserved characters Windows objects to.
    """
    stem = Path((raw or "").strip()).name          # drops every separator
    stem = _UNSAFE.sub("", stem).strip(" .")        # and every odd character
    # Windows refuses these regardless of extension.
    if stem.upper().split(".")[0] in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        stem = f"{stem}_"
    return stem[:80]


def _unused(directory: Path, stem: str, suffix: str) -> Path:
    """A path that does not exist yet.

    Asked twice for the same report, a model should not silently overwrite the
    first one -- the user may have wanted both.
    """
    candidate = directory / f"{stem}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = directory / f"{stem} ({counter}){suffix}"
        counter += 1
    return candidate
