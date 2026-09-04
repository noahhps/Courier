"""Move the private calendar table into the user's real calendar.

The private `calendar_events` table was Courier's own, which is exactly what is
wrong with it: nothing else on the machine can see those events, so the person
keeps their real appointments somewhere else and the assistant answers from a
table that is always a little bit false. This copies them across so the table
can be deleted without losing anything.

    python -m tools.migrate_calendar --dry-run     # say what would happen
    python -m tools.migrate_calendar               # do it
    python -m tools.migrate_calendar --calendar Courier

Safe to run twice. An event already present -- same title, same start -- is
skipped rather than duplicated, because the failure mode of a migration nobody
is watching is forty duplicates rather than none.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

from app.config import load_settings
from app.db import Database
from app.device import mac_calendar as backend
from app.store import Store


def _parse(value: str) -> datetime | None:
    for shape in ("%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, shape)
        except (TypeError, ValueError):
            continue
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print the plan and change nothing")
    parser.add_argument("--calendar", help="which calendar to write to; default is the usual one")
    args = parser.parse_args()

    if not backend.available():
        print(backend.unavailable_reason())
        return 1

    settings = load_settings()
    store = Store(Database(settings.db_path))
    rows = store.list_events(limit=1000)
    if not rows:
        print("The private calendar is empty. Nothing to migrate.")
        return 0

    print(f"{len(rows)} event(s) in the private table.")
    if args.dry_run:
        for row in rows:
            print(f"  would add  {row.starts_at}  {row.title}")
        print("\nNothing was changed. Re-run without --dry-run to migrate.")
        return 0

    granted, reason = backend.request_access()
    if not granted:
        print(reason)
        return 1

    # One read of the surrounding window, so "is it already there?" costs one
    # query rather than one per row.
    starts = [r.starts_at for r in rows if r.starts_at]
    span_from = _parse(min(starts)) or datetime.now()
    span_to = _parse(max(starts)) or datetime.now()
    existing = {
        (e.title.strip().lower(), e.starts_at[:10])
        for e in backend.list_events(span_from - timedelta(days=1), span_to + timedelta(days=2))
    }

    added = skipped = failed = 0
    for row in rows:
        start = _parse(row.starts_at)
        if start is None:
            print(f"  skip   {row.starts_at!r} is not a date I can parse — {row.title}")
            failed += 1
            continue
        if (row.title.strip().lower(), row.starts_at[:10]) in existing:
            skipped += 1
            continue

        end = _parse(row.ends_at) if row.ends_at else None
        event, error = backend.create_event(
            row.title,
            start,
            end,
            all_day=bool(row.all_day),
            notes=row.notes,
            calendar=args.calendar,
        )
        if error:
            print(f"  fail   {row.title}: {error}")
            failed += 1
            continue
        print(f"  added  {event.starts_at}  {event.title}  -> {event.calendar}")
        added += 1

    print(f"\n{added} added, {skipped} already there, {failed} failed.")
    if failed:
        print("The private table has not been touched; nothing is lost.")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
