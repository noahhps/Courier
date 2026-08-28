"""Writing something down on purpose, and crossing it out.

The told half of memory. Exact, because the user said it in so many words --
which is why `remember` stores at confidence 1.0 and never goes near the
curation pass's guesswork.

`forget` is the pair to it. Without one, "forget what I said about the
plumber" can only be answered by sending someone to a settings page, which is
the wrong answer to a sentence said out loud mid-conversation.
"""

from __future__ import annotations

from ..store import Store
from .skill import Skill

MAX_MATCHES = 5


class Remember(Skill):
    def __init__(self, store: Store, *, max_chars: int = 200):
        super().__init__(
            name="remember",
            description=(
                "Store one durable fact about the user so it is available in "
                "every future conversation. Use it when they say to remember "
                "something, or state something lastingly true about "
                "themselves, their home, their work, or how they want to be "
                "answered. Not for passing details of the current task -- "
                "those are already in the conversation."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "fact": {
                        "type": "string",
                        "description": (
                            "One self-contained sentence, written so it still "
                            "makes sense read on its own in a month. Say 'The "
                            "user rents a flat in Leeds', not 'yes, Leeds'."
                        ),
                    },
                    "category": {
                        "type": "string",
                        "description": (
                            "Short grouping for the memory page, e.g. Home, "
                            "Work, People, How I answer."
                        ),
                    },
                },
                "required": ["fact"],
            },
        )
        self.store = store
        self.max_chars = max_chars

    async def use(self, fact: str = "", category: str | None = None) -> str:
        text = (fact or "").strip()
        if not text:
            return "remember needs the fact to store, as one sentence."
        if len(text) > self.max_chars:
            return (
                f"That is too long to keep as a fact ({len(text)} characters, "
                f"limit {self.max_chars}). A fact is one sentence -- the detail "
                "is already in the conversation, where search_history will "
                "find it."
            )

        stored = self.store.add_fact(
            text, source="told", category=category.strip() or None if category else None
        )
        if stored is None:
            return f"Already remembered: {text!r}. Nothing to change."
        return f"Remembered: {text!r}. It will be available in every conversation."


class Forget(Skill):
    def __init__(self, store: Store):
        super().__init__(
            name="forget",
            description=(
                "Delete something previously remembered about the user. Use "
                "it when they ask you to forget, or correct, a stored fact. "
                "It removes curated facts only -- the conversation history "
                "itself is deleted from the app, not from here."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "about": {
                        "type": "string",
                        "description": (
                            "Words from the fact to remove, e.g. 'plumber'. "
                            "Matched against what was stored."
                        ),
                    }
                },
                "required": ["about"],
            },
        )
        self.store = store

    async def use(self, about: str = "") -> str:
        needle = (about or "").strip()
        if not needle:
            return "forget needs to know what to forget."

        matches = self.store.find_facts(needle, limit=MAX_MATCHES + 1)
        if not matches:
            return f"Nothing remembered matches {needle!r}, so there is nothing to forget."

        # More than a handful means the words were too broad, and deleting the
        # lot on a guess is not recoverable. Ask rather than act.
        if len(matches) > MAX_MATCHES:
            return (
                f"{len(matches)} stored facts mention {needle!r} -- too many to "
                "delete on that alone. Ask the user which one they mean."
            )

        deleted = [fact.text for fact in matches if self.store.delete_fact(fact.id)]
        listed = "; ".join(repr(text) for text in deleted)
        return f"Forgotten {len(deleted)} fact(s): {listed}."
