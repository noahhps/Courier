"""The user's real calendar, as skills.

Deliberately the same three verbs the old private calendar used -- read the
week, search it, add to it -- so nothing above this has to learn a new
vocabulary when the private one is removed. What changed is where the events
are: Calendar.app, and therefore iCloud, Google, or whatever else the person
has already set up.

Permission is asked for lazily, on the first call that needs it, rather than at
boot. A person who never asks about their calendar should never see a consent
dialog, and a dialog raised during startup is one nobody has any context for.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..device import mac_calendar as backend
from .skill import Skill

MAX_LISTED = 40


def _describe(event) -> str:
    when = event.starts_at.replace("T", " ")
    if event.all_day:
        when = f"{event.starts_at.split('T', 1)[0]} (all day)"
    elif event.ends_at:
        tail = event.ends_at.replace("T", " ")
        if event.ends_at[:10] == event.starts_at[:10]:
            tail = event.ends_at[11:]
        when = f"{when}–{tail}"
    line = f"{when} — {event.title}"
    extras = [bit for bit in (event.location, event.calendar) if bit]
    if extras:
        line += f"  [{' · '.join(extras)}]"
    return f"{line} ({event.notes})" if event.notes else line


def _lines(events, empty: str) -> str:
    if not events:
        return empty
    shown = events[:MAX_LISTED]
    out = "\n".join(_describe(e) for e in shown)
    if len(events) > MAX_LISTED:
        out += f"\n… and {len(events) - MAX_LISTED} more."
    return out


class _CalendarSkill(Skill):
    """Shared availability and the permission handshake."""

    @property
    def available(self) -> bool:
        return backend.available()

    def _ready(self) -> str | None:
        """None when the calendar can be used, or the sentence explaining why not."""
        if not backend.available():
            return backend.unavailable_reason()
        granted, reason = backend.request_access()
        return None if granted else reason


class ListDeviceEvents(_CalendarSkill):
    def __init__(self) -> None:
        super().__init__(
            name="list_events",
            description=(
                "What is on the user's calendar over the next N days, soonest "
                "first. This is their real calendar. Use it before answering "
                "anything about their schedule."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "How far ahead to look. Defaults to 7. Negative looks back.",
                    }
                },
            },
            requires="permission to read this Mac's calendar",
        )

    async def use(self, days: int = 7) -> str:
        problem = self._ready()
        if problem:
            return problem
        try:
            span = int(days)
        except (TypeError, ValueError):
            span = 7
        span = max(-365, min(span, 365))
        now = datetime.now()
        # From midnight, so "what's on today" includes this morning.
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if span >= 0:
            since, until = start, start + timedelta(days=span or 1)
            empty = f"Nothing on the calendar in the next {span or 1} days."
        else:
            since, until = start + timedelta(days=span), start + timedelta(days=1)
            empty = f"Nothing on the calendar in the last {abs(span)} days."
        return _lines(backend.list_events(since, until), empty)


class FindDeviceEvents(_CalendarSkill):
    def __init__(self) -> None:
        super().__init__(
            name="find_events",
            description=(
                "Search the user's real calendar by word, over the next year "
                "and the last one. Use when they name an event rather than a time."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Word or phrase to look for."}
                },
                "required": ["query"],
            },
            requires="permission to read this Mac's calendar",
        )

    async def use(self, query: str) -> str:
        needle = (query or "").strip()
        if not needle:
            return "Give me something to search for."
        problem = self._ready()
        if problem:
            return problem
        now = datetime.now()
        # EventKit has no text predicate, so the window is the search: a year
        # either side is everything a person means by "my calendar".
        events = backend.list_events(now - timedelta(days=365), now + timedelta(days=365))
        lowered = needle.lower()
        hits = [
            e
            for e in events
            if lowered in e.title.lower()
            or lowered in (e.notes or "").lower()
            or lowered in (e.location or "").lower()
        ]
        return _lines(hits, f"Nothing on the calendar matches {needle!r}.")


class AddDeviceEvent(_CalendarSkill):
    def __init__(self) -> None:
        super().__init__(
            name="add_event",
            description=(
                "Put an event on the user's real calendar. Times are their own "
                "local time, written as YYYY-MM-DDTHH:MM, or YYYY-MM-DD for an "
                "all-day event. Check the date with current_time first if they "
                "said something relative like 'next Tuesday'."
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
                    "calendar": {
                        "type": "string",
                        "description": "Which calendar to add to. Defaults to their usual one.",
                    },
                },
                "required": ["title", "starts_at"],
            },
            requires="permission to change this Mac's calendar",
        )

    @staticmethod
    def _parse(value: str) -> datetime | None:
        for shape in ("%Y-%m-%dT%H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, shape)
            except (TypeError, ValueError):
                continue
        return None

    async def use(
        self,
        title: str,
        starts_at: str,
        ends_at: str | None = None,
        notes: str | None = None,
        calendar: str | None = None,
    ) -> str:
        start = self._parse((starts_at or "").strip())
        if start is None:
            return (
                f"{starts_at!r} is not a date I can use. Write YYYY-MM-DDTHH:MM, "
                "or YYYY-MM-DD for an all-day event."
            )
        end = self._parse((ends_at or "").strip()) if ends_at else None
        if ends_at and end is None:
            return f"{ends_at!r} is not a date I can use. Same format as starts_at."
        if end and end <= start:
            return "The end has to come after the start."

        problem = self._ready()
        if problem:
            return problem

        event, error = backend.create_event(
            title.strip(),
            start,
            end,
            all_day="T" not in starts_at,
            notes=(notes or "").strip() or None,
            calendar=(calendar or "").strip() or None,
        )
        if error:
            return error
        return f"Added to your {event.calendar} calendar: {_describe(event)}"
