"""The clock. The smallest useful skill, and the one worth building first.

It reaches nothing -- no database, no network, no subprocess -- so when a turn
using it misbehaves, the fault is in the loop rather than in here.
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..situation import Situation
from ..widgets import SkillResult, build
from .skill import Skill


class Clock(Skill):
    # The default answer depends on which device is asking, so the turn loop
    # hands this one the session's situation.
    wants_context = True

    def __init__(self):
        super().__init__(
            name="current_time",
            description=(
                "The current date and time. Defaults to the user's own "
                "timezone; pass one only to ask about somewhere else."
            ),
            parameters={"type": "object", "properties": {
                "timezone": {"type": "string",
                             "description": "IANA name, e.g. Europe/London."}}},
        )

    async def use(
        self, timezone: str | None = None, context: Situation | None = None
    ) -> SkillResult | str:
        if not timezone:
            # The user's zone first, the server's only as a last resort. These
            # are routinely different machines -- the README has the server on
            # the box with the GPU and the reader on a phone -- so answering
            # "what time is it" with the server's clock is a wrong answer
            # delivered confidently.
            zone = context.tzinfo() if context else None
            now = datetime.now(zone) if zone else datetime.now().astimezone()
            return _answer(now)

        try:
            zone = ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            # An unknown name and a missing database raise the same exception,
            # so they have to be told apart by probing for a name that always
            # exists. Reporting "I don't recognise Europe/London" would send
            # someone hunting for a typo that isn't there.
            if not _have_timezone_db():
                return (
                    f"I can't look up {timezone!r}: this machine has no timezone "
                    "database installed (pip install tzdata). Ask me without a "
                    "timezone for the server's local time."
                )
            return f"{timezone!r} is not a timezone name I recognise."
        except (ValueError, TypeError):
            return f"{timezone!r} is not a timezone name I recognise."

        # Named, because the card is about to be read next to a clock on the
        # reader's own wall: "14:05" with no label is the wrong time twice a
        # day and there is no way to tell from the card which one it is.
        return _answer(datetime.now(zone), place=timezone)


def _answer(now: datetime, place: str | None = None) -> SkillResult:
    """The same instant twice: a sentence for the model, a card for the reader.

    The three failure branches above deliberately do not come through here.
    A card is a statement of fact, and "I don't recognise that timezone" is not
    one -- drawing it would put a confident-looking panel around an apology.
    """
    return SkillResult(
        now.strftime("%A %d %B %Y, %H:%M %Z").strip(),
        build(
            "clock",
            time=now.strftime("%H:%M"),
            date=now.strftime("%A %d %B %Y"),
            # `%Z` is empty for a naive datetime, which `_scalar` drops -- so
            # the card simply loses its zone line rather than growing a blank.
            zone=now.strftime("%Z"),
            note=place,
        ),
    )


@lru_cache(maxsize=1)
def _have_timezone_db() -> bool:
    """Whether any IANA name can be resolved on this machine.

    Cached: the answer cannot change while the process runs, and the check
    would otherwise repeat on every mistyped timezone.
    """
    try:
        ZoneInfo("UTC")
    except ZoneInfoNotFoundError:
        return False
    return True
