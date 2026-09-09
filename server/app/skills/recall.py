"""Searching everything ever said, and everything ever attached.

The one skill that makes the history worth keeping. Everything hard about it
lives in `memory/`; this is the part the model sees.
"""

from __future__ import annotations

from datetime import datetime

from ..memory.indexer import Indexer
from .skill import Skill

# How many hits come back. The orchestrator truncates a skill result at 4000
# characters from the tail, so this is chosen against that: six passages with
# their headers lands comfortably inside it, and the best one is written first
# so a truncation costs the worst result rather than the best.
RESULTS = 6

# Per passage. Long enough to answer from, short enough that six fit.
SNIPPET_CHARS = 420


class Recall(Skill):
    def __init__(self, indexer: Indexer):
        super().__init__(
            name="search_history",
            description=(
                "Search everything the user has ever said, in this and every "
                "previous conversation, plus the text of every document they "
                "have attached. Use it for anything about the user's own past: "
                "what they told you before, what one of their files said, when "
                "something happened. Prefer it over recalling from the current "
                "conversation alone."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "What to look for, in the user's own words. "
                            "Keywords work better than a full question."
                        ),
                    }
                },
                "required": ["query"],
            },
        )
        self.indexer = indexer

    async def use(self, query: str = "") -> str:
        query = (query or "").strip()
        if not query:
            return "search_history needs a query -- a few words to look for."

        try:
            hits = await self.indexer.search(query, limit=RESULTS)
        except Exception as exc:
            # A skill that knows what went wrong should say so itself; the
            # loop's fallback is a stack-trace summary nobody can act on.
            return f"The history search failed: {type(exc).__name__}: {exc}"

        if not hits:
            return (
                f"Nothing in the history matches {query!r}. "
                "This is the complete record, so it is safe to say you have "
                "not discussed it before."
            )

        blocks = [f"{len(hits)} passage(s) from the user's history, best first:"]
        for index, hit in enumerate(hits, start=1):
            blocks.append(f"{index}. {_header(hit)}\n{_snippet(hit.chunk.content)}")
        return "\n\n".join(blocks)


def _header(hit) -> str:
    """Where a passage came from, in one line a model can quote back."""
    source = hit.source or {}
    if source.get("attachment_name"):
        page = f", page {hit.chunk.page}" if hit.chunk.page else ""
        return (
            f"From the file {source['attachment_name']}{page} "
            f"({_date(source.get('attachment_at'))})"
        )
    who = "the user" if source.get("role") == "user" else "you"
    title = source.get("session_title") or "an untitled conversation"
    return f"Said by {who} in {title!r} on {_date(source.get('message_at'))}"


def _date(milliseconds) -> str:
    """DD-MM-YYYY, which is the format the system preamble asks for."""
    if not milliseconds:
        return "an unknown date"
    return datetime.fromtimestamp(milliseconds / 1000).strftime("%d-%m-%Y")


def _snippet(content: str) -> str:
    text = " ".join(content.split())
    if len(text) <= SNIPPET_CHARS:
        return text
    return text[: SNIPPET_CHARS - 1].rstrip() + "…"
