"""Searching everything ever said, and everything ever attached.

The one skill that makes the history worth keeping. Everything hard about it
lives in `memory/`; this is the part the model sees.
"""

from __future__ import annotations

from datetime import datetime

from ..memory.indexer import Indexer
from ..widgets import SkillResult, build
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

    async def use(self, query: str = "") -> SkillResult | str:
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
            return SkillResult(
                f"Nothing in the history matches {query!r}. "
                "This is the complete record, so it is safe to say you have "
                "not discussed it before.",
                # Worth a card of its own. An empty search is the strongest
                # answer this skill gives -- it is what lets the reply say
                # "you have never mentioned it" rather than "I don't think so"
                # -- and it should not be the one result that leaves no trace.
                build("recall", query=query, empty="Nothing in the history"),
            )

        blocks = [f"{len(hits)} passage(s) from the user's history, best first:"]
        for index, hit in enumerate(hits, start=1):
            blocks.append(f"{index}. {_header(hit)}\n{_snippet(hit.chunk.content)}")
        return SkillResult("\n\n".join(blocks), _card(query, hits))


def _card(query: str, hits: list):
    """The passages as a card.

    The same `_header` and `_snippet` the model is given: a reader comparing
    the card with the fold-out result should see the two agree, and a card
    that paraphrased its own evidence would be the one place in this feature
    where the shown half and the told half could drift apart.
    """
    return build(
        "recall",
        query=query,
        subtitle=f"{len(hits)} passage{'' if len(hits) == 1 else 's'}",
        passages=[
            {"source": _header(hit), "text": _snippet(hit.chunk.content)}
            for hit in hits
        ],
    )


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
