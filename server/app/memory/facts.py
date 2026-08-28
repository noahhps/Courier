"""Curating the short list the model sees on every turn.

Two sources, and they are not equally trustworthy. A fact the user *told* the
assistant is exact and arrives through the `remember` skill. A fact the
assistant *inferred* is a guess, and everything in this module exists to keep
a guess from being presented as knowledge: a confidence, a cap on how many
land at once, a refusal to read the assistant's own words, and a setting that
holds every one of them back until a person agrees.
"""

from __future__ import annotations

import json
import re

from ..providers import Message, ProviderError, ProviderRouter
from ..store import Store, StoredMessage

# What one pass may propose. A model asked for "anything durable" will happily
# produce fifteen restatements of the same sentence; three is enough for a real
# exchange and cheap to review.
MAX_PER_PASS = 3

# Below this a guess is not worth storing at all -- it costs a row, a line in
# the page, and a decision from the user, for something the model was barely
# willing to claim.
MIN_CONFIDENCE = 0.4
# Below *this* it is stored but flagged: the page's "Looks right?" affordance.
CONFIRM_BELOW = 0.7

# How much of the exchange the pass reads. Long enough for a real turn, short
# enough that this stays a small generation.
MAX_EXCERPT_CHARS = 4000

PROMPT = (
    "You extract durable facts about a user from a conversation.\n"
    "\n"
    "A durable fact is something still worth knowing in a month: where they "
    "live, what they do, who is in their life, an ongoing situation, or how "
    "they want to be answered. It is about the USER, never about the topic "
    "they asked about, and never about you.\n"
    "\n"
    "Do not restate anything already in the known list. Do not record "
    "questions, one-off requests, or anything you inferred from your own "
    "reply rather than from what the user said.\n"
    "\n"
    f"Reply with a JSON array of at most {MAX_PER_PASS} objects, each with "
    '"text" (one sentence), "category" (a short noun phrase such as Home, '
    'Work, People, or How I answer), and "confidence" (0 to 1). If there is '
    "nothing durable, reply with []. Reply with the JSON alone."
)

# A local model told to reply with JSON alone will still wrap it in a fenced
# block about a third of the time.
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse(reply: str) -> list[dict]:
    """The model's answer as facts worth storing, or [] .

    Pure, and total: every malformed shape a small model produces here has to
    come back as an empty list rather than an exception, because this runs
    unattended after a turn that already succeeded.
    """
    if not reply or not reply.strip():
        return []

    text = reply.strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    else:
        # Some models narrate before the array. Take the outermost brackets.
        start, end = text.find("["), text.rfind("]")
        if start != -1 and end > start:
            text = text[start : end + 1]

    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(raw, list):
        return []

    facts: list[dict] = []
    for item in raw[:MAX_PER_PASS]:
        if not isinstance(item, dict):
            continue
        sentence = str(item.get("text") or "").strip()
        if not sentence:
            continue
        try:
            confidence = float(item.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = min(1.0, max(0.0, confidence))
        if confidence < MIN_CONFIDENCE:
            continue
        category = item.get("category")
        facts.append(
            {
                "text": sentence,
                "category": str(category).strip() if category else None,
                "confidence": confidence,
            }
        )
    return facts


class Curator:
    """The inferred half of memory. Runs after a turn, never during one."""

    def __init__(self, settings, store: Store, router: ProviderRouter) -> None:
        self.settings = settings
        self.store = store
        self.router = router

    def due(self, history: list[StoredMessage]) -> bool:
        """Whether this turn is a curation turn.

        On a cadence rather than every turn: the pass is a whole extra
        generation, and running it per turn doubles what the GPU does for
        something nobody is waiting on.
        """
        every = self.settings.memory_extract_every
        if every <= 0:
            return False
        turns = sum(1 for m in history if m.role == "user")
        return turns > 0 and turns % every == 0

    async def run(self, session_id: str, *, confirm: bool) -> int:
        """One pass over the tail of a conversation. Returns facts stored."""
        history = self.store.list_messages(session_id)
        if not self.due(history):
            return 0

        excerpt = _excerpt(history)
        if not excerpt:
            return 0

        known = [fact.text for fact in self.store.list_facts()]
        prompt = [
            Message(role="system", content=PROMPT),
            Message(
                role="user",
                content=(
                    ("Known already:\n" + "\n".join(f"- {k}" for k in known) + "\n\n")
                    if known
                    else ""
                )
                + f"Conversation:\n{excerpt}",
            ),
        ]

        try:
            route = await self.router.resolve()
            parts: list[str] = []
            async for chunk in route.provider.stream(prompt):
                if chunk.text:
                    parts.append(chunk.text)
        except ProviderError:
            return 0  # the model is down; the turn it followed already worked

        # The last user message is the provenance for anything found here.
        last_user = next(
            (m for m in reversed(history) if m.role == "user"), None
        )
        stored = 0
        for candidate in parse("".join(parts)):
            fact = self.store.add_fact(
                candidate["text"][: self.settings.memory_fact_chars],
                source="inferred",
                category=candidate["category"],
                confidence=candidate["confidence"],
                # Held back entirely when the user asked to confirm first;
                # otherwise a low-confidence guess is active but flagged on the
                # page rather than presented as settled.
                status="pending" if confirm else "active",
                message_id=last_user.id if last_user else None,
            )
            if fact:
                stored += 1
        return stored


def _excerpt(history: list[StoredMessage]) -> str:
    """The recent exchange, with the assistant's own words left out.

    The model's speculation about you, fed back in as something it knows about
    you, is how a memory system develops opinions nobody ever expressed. Only
    what the user actually said is read.
    """
    said = [m.content.strip() for m in history[-12:]
            if m.role == "user" and m.content.strip()]
    if not said:
        return ""
    return "\n\n".join(said)[-MAX_EXCERPT_CHARS:]
