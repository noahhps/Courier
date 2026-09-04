"""The real-calendar skills, with EventKit stubbed out.

Nothing here touches the system calendar: these run on Linux CI and on a Mac
that has never been granted access. What is being tested is the layer above the
framework -- argument validation, formatting, and the order in which those
happen, which is what decides whether a person sees a permission dialog they
did not provoke.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest

from app.device import mac_calendar as backend
from app.skills import device_calendar as skills


@dataclass(frozen=True)
class FakeEvent:
    identifier: str = "x"
    title: str = "Dentist"
    starts_at: str = "2026-09-03T11:00"
    ends_at: str | None = "2026-09-03T12:00"
    all_day: bool = False
    calendar: str = "Home"
    notes: str | None = None
    location: str | None = None


@pytest.fixture
def granted(monkeypatch):
    """A machine where EventKit exists and access has been given."""
    monkeypatch.setattr(backend, "available", lambda: True)
    monkeypatch.setattr(backend, "unavailable_reason", lambda: None)
    monkeypatch.setattr(backend, "request_access", lambda timeout=60.0: (True, None))
    return backend


@pytest.fixture
def refused(monkeypatch):
    monkeypatch.setattr(backend, "available", lambda: True)
    monkeypatch.setattr(
        backend, "request_access", lambda timeout=60.0: (False, "Calendar access was refused.")
    )
    return backend


# -- the property that matters most -------------------------------------------


@pytest.mark.anyio
async def test_a_malformed_date_never_raises_a_consent_dialog(monkeypatch):
    """Validation comes before permission, deliberately.

    A model that mistypes a date should cost one round, not a system dialog the
    person has to read and dismiss for a call that was never going to work.
    """
    asked = []
    monkeypatch.setattr(backend, "available", lambda: True)
    monkeypatch.setattr(
        backend, "request_access", lambda timeout=60.0: (asked.append(1), (True, None))[1]
    )
    out = await skills.AddDeviceEvent().use("Lunch", "next tuesday")
    assert "not a date I can use" in out
    assert asked == [], "permission was requested for a call that could not succeed"


@pytest.mark.anyio
async def test_end_before_start_is_caught_before_permission(monkeypatch):
    asked = []
    monkeypatch.setattr(backend, "available", lambda: True)
    monkeypatch.setattr(
        backend, "request_access", lambda timeout=60.0: (asked.append(1), (True, None))[1]
    )
    out = await skills.AddDeviceEvent().use(
        "Lunch", "2026-09-03T13:00", ends_at="2026-09-03T12:00"
    )
    assert "after the start" in out
    assert asked == []


# -- refusal reaches the model as a sentence ----------------------------------


@pytest.mark.anyio
async def test_refusal_is_reported_not_raised(refused):
    out = await skills.ListDeviceEvents().use()
    assert "refused" in out.lower()


@pytest.mark.anyio
async def test_unavailable_platform_explains_itself(monkeypatch):
    monkeypatch.setattr(backend, "available", lambda: False)
    monkeypatch.setattr(backend, "unavailable_reason", lambda: "only on macOS")
    out = await skills.ListDeviceEvents().use()
    assert "only on macOS" in out


def test_skills_are_unavailable_when_the_bridge_is_missing(monkeypatch):
    monkeypatch.setattr(backend, "available", lambda: False)
    assert skills.ListDeviceEvents().available is False


# -- reading -------------------------------------------------------------------


@pytest.mark.anyio
async def test_listing_formats_a_timed_event(granted, monkeypatch):
    monkeypatch.setattr(backend, "list_events", lambda since, until: [FakeEvent()])
    out = await skills.ListDeviceEvents().use(7)
    assert "2026-09-03 11:00–12:00 — Dentist" in out
    assert "[Home]" in out


@pytest.mark.anyio
async def test_all_day_events_say_so(granted, monkeypatch):
    event = FakeEvent(starts_at="2026-09-21", ends_at=None, all_day=True, title="Term begins")
    monkeypatch.setattr(backend, "list_events", lambda since, until: [event])
    out = await skills.ListDeviceEvents().use()
    assert "2026-09-21 (all day) — Term begins" in out


@pytest.mark.anyio
async def test_empty_calendar_says_so(granted, monkeypatch):
    monkeypatch.setattr(backend, "list_events", lambda since, until: [])
    out = await skills.ListDeviceEvents().use(3)
    assert "Nothing on the calendar in the next 3 days" in out


@pytest.mark.anyio
async def test_negative_days_looks_backwards(granted, monkeypatch):
    seen = {}

    def capture(since, until):
        seen["since"], seen["until"] = since, until
        return []

    monkeypatch.setattr(backend, "list_events", capture)
    out = await skills.ListDeviceEvents().use(-30)
    assert "last 30 days" in out
    assert seen["since"] < seen["until"]
    assert (seen["until"] - seen["since"]) >= timedelta(days=30)


# -- searching -----------------------------------------------------------------


@pytest.mark.anyio
async def test_search_matches_title_notes_and_location(granted, monkeypatch):
    events = [
        FakeEvent(title="Driving test"),
        FakeEvent(title="Lunch", notes="bring the licence"),
        FakeEvent(title="Standup", location="Los Gatos DMV"),
        FakeEvent(title="Unrelated"),
    ]
    monkeypatch.setattr(backend, "list_events", lambda since, until: events)
    assert "Driving test" in await skills.FindDeviceEvents().use("driving")
    assert "Lunch" in await skills.FindDeviceEvents().use("licence")
    assert "Standup" in await skills.FindDeviceEvents().use("los gatos")
    assert "Unrelated" not in await skills.FindDeviceEvents().use("driving")


@pytest.mark.anyio
async def test_empty_search_asks_for_a_term(granted):
    out = await skills.FindDeviceEvents().use("   ")
    assert "something to search for" in out


# -- writing -------------------------------------------------------------------


@pytest.mark.anyio
async def test_add_reports_the_calendar_it_landed_on(granted, monkeypatch):
    def create(title, start, end, *, all_day, notes, calendar):
        assert start == datetime(2026, 9, 1, 11, 0)
        assert all_day is False
        return FakeEvent(title=title, starts_at="2026-09-01T11:00", ends_at="2026-09-01T12:00"), None

    monkeypatch.setattr(backend, "create_event", create)
    out = await skills.AddDeviceEvent().use("Driving test", "2026-09-01T11:00")
    assert "Added to your Home calendar" in out


@pytest.mark.anyio
async def test_a_date_without_a_time_is_all_day(granted, monkeypatch):
    seen = {}

    def create(title, start, end, *, all_day, notes, calendar):
        seen["all_day"] = all_day
        return FakeEvent(starts_at="2026-09-21", ends_at=None, all_day=True, title=title), None

    monkeypatch.setattr(backend, "create_event", create)
    await skills.AddDeviceEvent().use("Term begins", "2026-09-21")
    assert seen["all_day"] is True


@pytest.mark.anyio
async def test_a_backend_error_is_passed_through(granted, monkeypatch):
    monkeypatch.setattr(
        backend, "create_event", lambda *a, **k: (None, "There is no writable calendar called 'Work'.")
    )
    out = await skills.AddDeviceEvent().use("x", "2026-09-01", calendar="Work")
    assert "no writable calendar" in out
