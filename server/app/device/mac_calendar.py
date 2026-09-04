"""The user's real calendar, through EventKit.

This replaces a private table with the thing the person actually looks at. A
calendar only Courier can see is worse than no calendar skill at all: it
answers confidently about a week it does not know, and the user's real
appointments are in Calendar.app, Google or iCloud regardless.

Access is gated by TCC, which is the operating system asking the user rather
than us asking. Two consequences shape this module:

* The prompt is raised by the *responsible process*. Launched from the Tauri
  bundle that is Courier.app, and the strings the prompt shows come from that
  bundle's Info.plist -- which is why `NSCalendarsFullAccessUsageDescription`
  has to be there and not here.
* A denial is permanent until the user changes it in System Settings. Nothing
  here retries, and every function returns a reason instead of raising, so a
  refusal reaches the model as a sentence it can pass on.

Additive only. Reading, creating and updating are here; deleting is not.
Removing something from a person's real calendar on a local model's say-so is
not a mistake that can be undone by re-running the turn.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime

# Imported at module scope and allowed to fail: this file is imported on every
# platform, and only macOS has any of it.
try:  # pragma: no cover - the import itself is the platform check
    import EventKit
    import Foundation

    _IMPORT_ERROR: str | None = None
except Exception as exc:  # pragma: no cover
    EventKit = None  # type: ignore[assignment]
    Foundation = None  # type: ignore[assignment]
    _IMPORT_ERROR = str(exc)


# EKAuthorizationStatus. `FullAccess` is 3 on macOS 14+, where it replaced the
# older `Authorized` with the same value; `WriteOnly` (4) is new and is not
# enough for us, because a calendar you may write but not read is one you
# cannot check before writing to.
NOT_DETERMINED = 0
RESTRICTED = 1
DENIED = 2
FULL_ACCESS = 3
WRITE_ONLY = 4

# How long to wait for the user to answer the system prompt. Long enough to
# find the dialog behind another window, short enough that a turn does not hang
# forever on a prompt nobody is looking at.
PROMPT_TIMEOUT = 60.0

_store_lock = threading.Lock()
_store = None


@dataclass(frozen=True)
class DeviceEvent:
    """One event, in the shapes the rest of the app already speaks."""

    identifier: str
    title: str
    starts_at: str  # YYYY-MM-DD or YYYY-MM-DDTHH:MM, matching the old store
    ends_at: str | None
    all_day: bool
    calendar: str
    notes: str | None
    location: str | None


def available() -> bool:
    """Whether this module can do anything at all on this machine."""
    return EventKit is not None


def unavailable_reason() -> str | None:
    if EventKit is None:
        return (
            "The calendar is only reachable on macOS, and the EventKit bridge "
            f"is not installed here ({_IMPORT_ERROR})."
        )
    return None


def _event_store():
    """One EKEventStore for the process.

    Rebuilding it per call is what makes EventKit appear to lose track of
    permission: a fresh store re-reads authorization and, on some versions,
    re-prompts. It is also expensive.
    """
    global _store
    with _store_lock:
        if _store is None:
            _store = EventKit.EKEventStore.alloc().init()
        return _store


def authorization_status() -> int:
    if EventKit is None:
        return DENIED
    return EventKit.EKEventStore.authorizationStatusForEntityType_(
        EventKit.EKEntityTypeEvent
    )


def describe_status(status: int) -> str:
    return {
        NOT_DETERMINED: "not yet asked",
        RESTRICTED: "blocked by policy on this machine",
        DENIED: "refused",
        FULL_ACCESS: "granted",
        WRITE_ONLY: "write-only, which is not enough to read the calendar",
    }.get(status, f"unknown ({status})")


def request_access(timeout: float = PROMPT_TIMEOUT) -> tuple[bool, str | None]:
    """Ask the system for calendar access, blocking until the user answers.

    Returns (granted, reason). The completion handler fires on a queue of
    EventKit's choosing, so an Event is what turns their callback back into a
    straight line -- and the timeout is what stops a prompt nobody noticed from
    holding a turn open indefinitely.
    """
    if EventKit is None:
        return False, unavailable_reason()

    status = authorization_status()
    if status == FULL_ACCESS:
        return True, None
    if status in (DENIED, RESTRICTED, WRITE_ONLY):
        return False, (
            f"Calendar access is {describe_status(status)}. Change it in System "
            "Settings > Privacy & Security > Calendars, then ask me again."
        )

    store = _event_store()
    done = threading.Event()
    outcome: dict[str, object] = {"granted": False, "error": None}

    def completion(granted, error):
        outcome["granted"] = bool(granted)
        outcome["error"] = str(error) if error else None
        done.set()

    # macOS 14 split the old single request in two. Ask for the new one first
    # and fall back, rather than checking the OS version: the selector either
    # exists on this store or it does not.
    if hasattr(store, "requestFullAccessToEventsWithCompletion_"):
        store.requestFullAccessToEventsWithCompletion_(completion)
    else:  # pragma: no cover - only reachable on macOS 13 and older
        store.requestAccessToEntityType_completion_(EventKit.EKEntityTypeEvent, completion)

    if not done.wait(timeout):
        return False, (
            "macOS asked for permission and nothing answered within "
            f"{int(timeout)} seconds. The prompt may be behind another window."
        )
    if not outcome["granted"]:
        detail = outcome["error"]
        return False, "Calendar access was refused." + (f" ({detail})" if detail else "")
    return True, None


def _to_nsdate(when: datetime):
    return Foundation.NSDate.dateWithTimeIntervalSince1970_(when.timestamp())


def _from_nsdate(value, all_day: bool) -> str | None:
    """An NSDate as the fixed-width ISO string the rest of the app compares."""
    if value is None:
        return None
    stamp = datetime.fromtimestamp(value.timeIntervalSince1970())
    return stamp.strftime("%Y-%m-%d" if all_day else "%Y-%m-%dT%H:%M")


def calendars() -> list[str]:
    """Every calendar that can be written to, by title."""
    if EventKit is None:
        return []
    store = _event_store()
    return [
        c.title()
        for c in store.calendarsForEntityType_(EventKit.EKEntityTypeEvent)
        if c.allowsContentModifications()
    ]


def list_events(since: datetime, until: datetime) -> list[DeviceEvent]:
    """Everything in a half-open window, soonest first, across all calendars."""
    store = _event_store()
    predicate = store.predicateForEventsWithStartDate_endDate_calendars_(
        _to_nsdate(since), _to_nsdate(until), None
    )
    found = store.eventsMatchingPredicate_(predicate) or []
    events = [
        DeviceEvent(
            identifier=str(e.eventIdentifier() or ""),
            title=str(e.title() or "(untitled)"),
            starts_at=_from_nsdate(e.startDate(), e.isAllDay()) or "",
            ends_at=_from_nsdate(e.endDate(), e.isAllDay()),
            all_day=bool(e.isAllDay()),
            calendar=str(e.calendar().title() if e.calendar() else ""),
            notes=str(e.notes()) if e.notes() else None,
            location=str(e.location()) if e.location() else None,
        )
        for e in found
    ]
    events.sort(key=lambda e: e.starts_at)
    return events


def create_event(
    title: str,
    starts_at: datetime,
    ends_at: datetime | None = None,
    *,
    all_day: bool = False,
    notes: str | None = None,
    calendar: str | None = None,
) -> tuple[DeviceEvent | None, str | None]:
    """Put an event on a real calendar. Returns (event, error)."""
    store = _event_store()

    target = None
    if calendar:
        target = next(
            (
                c
                for c in store.calendarsForEntityType_(EventKit.EKEntityTypeEvent)
                if c.title() == calendar and c.allowsContentModifications()
            ),
            None,
        )
        if target is None:
            names = ", ".join(calendars()) or "none"
            return None, f"There is no writable calendar called {calendar!r}. I can see: {names}."
    if target is None:
        target = store.defaultCalendarForNewEvents()
    if target is None:
        return None, "This machine has no calendar that accepts new events."

    event = EventKit.EKEvent.eventWithEventStore_(store)
    event.setTitle_(title)
    event.setCalendar_(target)
    event.setAllDay_(bool(all_day))
    event.setStartDate_(_to_nsdate(starts_at))
    # EventKit requires an end. An all-day event ends the same day; a timed one
    # with no stated end gets an hour, which is what a person means by "at 3".
    if ends_at is None:
        ends_at = starts_at.replace(hour=23, minute=59) if all_day else starts_at.replace(
            hour=min(starts_at.hour + 1, 23)
        )
    event.setEndDate_(_to_nsdate(ends_at))
    if notes:
        event.setNotes_(notes)

    ok, error = store.saveEvent_span_error_(event, EventKit.EKSpanThisEvent, None)
    if not ok:
        return None, f"macOS refused to save the event: {error}"
    return (
        DeviceEvent(
            identifier=str(event.eventIdentifier() or ""),
            title=title,
            starts_at=_from_nsdate(event.startDate(), all_day) or "",
            ends_at=_from_nsdate(event.endDate(), all_day),
            all_day=bool(all_day),
            calendar=str(target.title()),
            notes=notes,
            location=None,
        ),
        None,
    )
