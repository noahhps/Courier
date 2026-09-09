"""The calendar, as four skills.

Four rather than one with an `action` argument: the schema is what the model
reads to decide, and "add_event / update_event / list_events / find_events" is
a decision it can make from the names alone. A single `calendar(action=...)`
makes every call a two-step guess, and a wrong `action` is a silent no-op
rather than a name the model can be told does not exist.

None of them takes an id. Every listing is prose, so an id is something the
model would have to be handed and then copy back exactly -- which a small local
model will get wrong often enough to matter. Events are named the way the user
names them, and an ambiguous name is a question asked back rather than a guess.

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

# A bare day, used only to pick between events that share a name.
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

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


def _granularity(like: str) -> str:
    """The format `like` is written in -- with a time, or a bare date."""
    return "%Y-%m-%dT%H:%M" if "T" in like else "%Y-%m-%d"


def _shifted_end(event: StoredEvent, new_start: str) -> str | None:
    """Where the end lands when the start moves, keeping the length.

    "Move my dentist appointment to 3pm" means the whole appointment, not just
    its beginning. Setting the start alone and leaving the end where it was
    produces an event that finishes before it starts -- which the ordering
    check below would then refuse, so the model would be told its own sensible
    request was invalid.
    """
    if not event.ends_at or new_start == event.starts_at:
        return event.ends_at
    delta = datetime.fromisoformat(new_start) - datetime.fromisoformat(event.starts_at)
    moved = datetime.fromisoformat(event.ends_at) + delta
    return moved.strftime(_granularity(event.ends_at))


class UpdateEvent(Skill):
    def __init__(self, store: Store) -> None:
        super().__init__(
            name="update_event",
            description=(
                "Change an event already on the calendar: move it, rename it, "
                "or change its notes. Name it the way the user does -- if that "
                "matches more than one you will be shown them and can pick one "
                "with 'on'. Moving the start moves the end with it, so an "
                "appointment keeps its length. Use add_event for something not "
                "on the calendar yet."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Words from the title of the event to change.",
                    },
                    "on": {
                        "type": "string",
                        "description": (
                            "YYYY-MM-DD. Only needed when the query matches "
                            "more than one event."
                        ),
                    },
                    "title": {"type": "string", "description": "New title, if renaming."},
                    "starts_at": {
                        "type": "string",
                        "description": (
                            "New start: YYYY-MM-DDTHH:MM, or YYYY-MM-DD to make "
                            "it an all-day event."
                        ),
                    },
                    "ends_at": {
                        "type": "string",
                        "description": "New end, same format as starts_at.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "New notes, replacing what was there.",
                    },
                },
                "required": ["query"],
            },
        )
        self.store = store

    async def use(
        self,
        query: str,
        on: str | None = None,
        title: str | None = None,
        starts_at: str | None = None,
        ends_at: str | None = None,
        notes: str | None = None,
    ) -> str:
        needle = (query or "").strip()
        if not needle:
            return "Tell me which event to change."
        if title is None and starts_at is None and ends_at is None and notes is None:
            return (
                f"Nothing to change about {needle!r}. Say what to move it to, "
                "what to rename it, or what to note."
            )
        if starts_at and not _WHEN.match(starts_at):
            return (
                f"{starts_at!r} is not a date I can use. Write YYYY-MM-DDTHH:MM, "
                "or YYYY-MM-DD for an all-day event."
            )
        if ends_at and not _WHEN.match(ends_at):
            return f"{ends_at!r} is not a date I can use. Same format as starts_at."
        if on and not _DAY.match(on):
            return f"{on!r} is not a date I can use. Write YYYY-MM-DD."

        # Found by name rather than by id, which is the whole shape of this
        # skill: nothing the model has read gives it an id, because every
        # listing is prose. One match is an edit; anything else is a question
        # asked back, with the candidates, rather than a guess acted on.
        matches = self.store.search_events(needle)
        if on:
            matches = [event for event in matches if event.starts_at[:10] == on]
        where = f"{needle!r}" + (f" on {on}" if on else "")
        if not matches:
            return f"Nothing on the calendar matches {where}."
        if len(matches) > 1:
            listed = _lines(matches, "")
            return (
                f"More than one event matches {where}:\n{listed}\n"
                "Say which by calling this again with 'on' set to its date."
            )
        event = matches[0]

        new_start = starts_at or event.starts_at
        new_end = ends_at if ends_at is not None else _shifted_end(event, new_start)
        all_day = "T" not in new_start
        # A whole-day event has no end time. Converting a timed event into one
        # would otherwise leave a stray 12:00 hanging off a date-only start --
        # invisible in every listing, and never corrected by anything. Guarded
        # on the start having actually changed shape, so renaming a multi-day
        # all-day event does not quietly truncate it.
        if all_day and ends_at is None and "T" in event.starts_at:
            new_end = None
        if new_end and new_end <= new_start:
            return (
                f"That would end the event at {new_end} and start it at "
                f"{new_start}. The end has to come after the start."
            )

        was_noted = event.notes
        updated = self.store.update_event(
            event.id,
            title=(title or event.title).strip(),
            starts_at=new_start,
            ends_at=new_end,
            all_day=all_day,
            notes=((notes if notes is not None else event.notes) or "").strip() or None,
        )
        if updated is None:
            return "That event was removed while I was changing it."

        line = f"Updated: {_describe(updated)}"
        # Notes replace rather than accumulate, which loses whatever was there
        # -- and the model reliably does this: told "bring your licence" it
        # overwrites the address the event was carrying. Saying so turns silent
        # data loss into something it can put right in the same turn, which is
        # the same trick write_document plays with a too-short document.
        # `not in` rather than `!=`: once the old wording has been carried into
        # the new note the warning has done its job, and repeating it invites
        # the model to keep appending the same sentence to itself.
        if notes is not None and was_noted and was_noted not in (updated.notes or ""):
            line += (
                f"\nThe note it had before was replaced, and said: {was_noted!r}. "
                "If any of that still applies, call this again with both."
            )
        return line
