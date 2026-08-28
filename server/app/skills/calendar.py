"""The calendar, as three skills.

Three rather than one with an `action` argument: the schema is what the model
reads to decide, and "add_event / list_events / find_events" is a decision it
can make from the names alone. A single `calendar(action=...)` makes every call
a two-step guess, and a wrong `action` is a silent no-op rather than a name the
model can be told does not exist.

Everything returns text. A skill's result goes back into the prompt, so a JSON
blob would be re-read by the model and paraphrased anyway -- writing the
sentence here is one less thing for it to get wrong.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from ..store import Store, StoredEvent
from .skill import Skill

# 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM'. Deliberately strict: a model that guesses
# at "next Tuesday" should be told the format rather than have a date invented
# for it from a partial match.
_WHEN = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?$")

MAX_LISTED = 40


def _describe(event: StoredEvent) -> str:
    when = event.starts_at.replace("T", " ")
    if event.all_day:
        when = event.starts_at.split("T", 1)[0] + " (all day)"
    elif event.ends_at:
        # Same day: show the end as a bare time rather than repeating the date.
        tail = event.ends_at.replace("T", " ")
        if event.ends_at[:10] == event.starts_at[:10]:
            tail = event.ends_at[11:]
        when = f"{when}–{tail}"
    line = f"{when} — {event.title}"
    return f"{line} ({event.notes})" if event.notes else line


def _lines(events: list[StoredEvent], empty: str) -> str:
    if not events:
        return empty
    shown = events[:MAX_LISTED]
    out = "\n".join(_describe(e) for e in shown)
    if len(events) > MAX_LISTED:
        out += f"\n… and {len(events) - MAX_LISTED} more."
    return out


class AddEvent(Skill):
    def __init__(self, store: Store) -> None:
        super().__init__(
            name="add_event",
            description=(
                "Put an event on the user's calendar. Times are the user's own "
                "local time, written as YYYY-MM-DDTHH:MM, or YYYY-MM-DD for an "
                "all-day event. Check the current date with current_time first "
                "if the user said something relative like 'tomorrow'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short name for the event."},
                    "starts_at": {
                        "type": "string",
                        "description": "YYYY-MM-DDTHH:MM, or YYYY-MM-DD for all day.",
                    },
                    "ends_at": {
                        "type": "string",
                        "description": "Optional end, same format as starts_at.",
                    },
                    "notes": {"type": "string", "description": "Optional detail."},
                },
                "required": ["title", "starts_at"],
            },
        )
        self.store = store

    async def use(
        self,
        title: str,
        starts_at: str,
        ends_at: str | None = None,
        notes: str | None = None,
    ) -> str:
        if not _WHEN.match(starts_at or ""):
            return (
                f"{starts_at!r} is not a date I can use. Write YYYY-MM-DDTHH:MM, "
                "or YYYY-MM-DD for an all-day event."
            )
        if ends_at and not _WHEN.match(ends_at):
            return f"{ends_at!r} is not a date I can use. Same format as starts_at."
        if ends_at and ends_at <= starts_at:
            return "The end has to come after the start."

        all_day = "T" not in starts_at
        event = self.store.add_event(
            title.strip(),
            starts_at,
            ends_at=ends_at,
            all_day=all_day,
            notes=(notes or "").strip() or None,
        )
        return f"Added: {_describe(event)}"


class ListEvents(Skill):
    def __init__(self, store: Store) -> None:
        super().__init__(
            name="list_events",
            description=(
                "What is on the user's calendar over the next N days, soonest "
                "first. Use this before answering anything about their schedule."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "How far ahead to look. Defaults to 7.",
                    }
                },
            },
        )
        self.store = store

    async def use(self, days: int = 7) -> str:
        try:
            span = max(1, min(int(days), 365))
        except (TypeError, ValueError):
            span = 7
        now = datetime.now()
        # From midnight today, so "what's on today" includes this morning
        # rather than only what is still to come.
        since = now.strftime("%Y-%m-%dT00:00")
        until = (now + timedelta(days=span)).strftime("%Y-%m-%dT00:00")
        events = self.store.list_events(since=since, until=until)
        return _lines(events, f"Nothing on the calendar in the next {span} days.")


class FindEvents(Skill):
    def __init__(self, store: Store) -> None:
        super().__init__(
            name="find_events",
            description=(
                "Search the calendar by word, across all dates including past "
                "ones. Use when the user names an event rather than a time."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Word or phrase to look for."}
                },
                "required": ["query"],
            },
        )
        self.store = store

    async def use(self, query: str) -> str:
        needle = (query or "").strip()
        if not needle:
            return "Give me something to search for."
        events = self.store.search_events(needle)
        return _lines(events, f"Nothing on the calendar matches {needle!r}.")
