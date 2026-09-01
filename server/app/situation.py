"""Where and when the user is, as reported by their own device.

The server is not the user. Courier is meant to run on the machine with the
GPU and be reached from a phone in another room -- or another country -- so the
server's clock and the server's timezone answer a question nobody asked. The
only honest source for "what time is it for you" is the browser making the
request, which knows its IANA zone, its UTC offset and its locale without
asking anyone's permission and without a single outbound call.

Nothing here reaches the network. "General location" is derived from the
timezone and the locale's region, which is as coarse as it sounds: a zone names
a city that stands for a whole region, and that is the intended resolution. No
GPS prompt, no IP lookup, no third party.

Everything arriving here is client-supplied and ends up inside the system
prompt, so every field is validated rather than trusted -- see `clean_zone_name`
in particular, which is a path guard as much as a spelling check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Area/Location, up to three segments (America/Argentina/Buenos_Aires). Checked
# before the name reaches ZoneInfo, which resolves a key against files on disk:
# a name is a lookup path, so "../../.." has to be impossible rather than
# merely unlikely.
_ZONE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9+_-]*(?:/[A-Za-z0-9+_-]+){0,2}$")

# BCP-47, loosely: en, en-GB, zh-Hant-TW. Enough to reject anything that is not
# a language tag; not a full RFC 5646 parser, which this has no use for.
_LOCALE = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{2,8}){0,3}$")

# Country names are free text from Intl.DisplayNames, so they are filtered down
# to the characters a place name actually uses. This drops newlines, colons and
# brackets, which is the point: this string is interpolated into the system
# prompt, and a place name has no business introducing structure there.
_PLACE_ALLOWED = re.compile(r"[^\w \-'’.,()]", re.UNICODE)

# A quarter-hour past 14 hours either side covers every zone that has ever
# existed, with room to spare.
_MAX_OFFSET_MINUTES = 14 * 60


def clean_zone_name(raw: object) -> str | None:
    """A syntactically valid IANA zone name, or None. Does not check it exists."""
    if not isinstance(raw, str):
        return None
    name = raw.strip()
    if not name or len(name) > 64 or not _ZONE_NAME.match(name):
        return None
    return name


def clean_locale(raw: object) -> str | None:
    """A BCP-47 language tag, or None."""
    if not isinstance(raw, str):
        return None
    tag = raw.strip()
    if not tag or len(tag) > 35 or not _LOCALE.match(tag):
        return None
    return tag


def clean_place_name(raw: object) -> str | None:
    """A country or region name reduced to the characters place names use."""
    if not isinstance(raw, str):
        return None
    name = _PLACE_ALLOWED.sub("", raw)
    name = " ".join(name.split())  # collapses every kind of whitespace
    return name[:60] or None


def clean_offset(raw: object) -> int | None:
    """Minutes east of UTC, or None.

    East-positive, which is the opposite of what `Date.getTimezoneOffset()`
    returns; the client negates it before sending so that the sign here means
    what it says everywhere else.
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    minutes = int(raw)
    if abs(minutes) > _MAX_OFFSET_MINUTES:
        return None
    return minutes


@dataclass(frozen=True)
class Situation:
    """One device's answer to "where and when are you".

    Frozen because it is captured once, when the conversation starts, and read
    on every turn after that. A conversation whose stated time drifts turn by
    turn would both mislead the model and invalidate the prompt cache on every
    request, so this is deliberately a snapshot rather than a live reading.
    """

    timezone: str | None = None
    locale: str | None = None
    utc_offset: int | None = None  # minutes east of UTC
    region: str | None = None  # display name, e.g. "United Kingdom"

    @classmethod
    def from_client(cls, data: dict | None) -> Situation:
        """Build one from whatever the browser sent, discarding what fails to validate."""
        data = data or {}
        return cls(
            timezone=clean_zone_name(data.get("timezone")),
            locale=clean_locale(data.get("locale")),
            utc_offset=clean_offset(data.get("utc_offset")),
            region=clean_place_name(data.get("region")),
        )

    @property
    def known(self) -> bool:
        """Whether this says anything at all worth putting in a prompt."""
        return bool(self.timezone or self.utc_offset is not None or self.region)

    def to_row(self) -> dict:
        return {
            "tz": self.timezone,
            "locale": self.locale,
            "utc_offset": self.utc_offset,
            "region": self.region,
        }

    @classmethod
    def from_row(cls, row: dict | None) -> Situation:
        row = row or {}
        return cls(
            timezone=row.get("tz"),
            locale=row.get("locale"),
            utc_offset=row.get("utc_offset"),
            region=row.get("region"),
        )

    # -- deriving ---------------------------------------------------------

    def tzinfo(self) -> ZoneInfo | timezone | None:
        """The zone to read a timestamp in, preferring the named one.

        The offset is a fallback rather than a duplicate: a Windows box with no
        `tzdata` installed cannot resolve a single IANA name, and the whole
        feature would go dark on exactly the machine the README tells people to
        run this on. The offset still gives the right wall clock -- it just
        cannot name the zone or know when the clocks change.
        """
        if self.timezone:
            try:
                return ZoneInfo(self.timezone)
            except (ZoneInfoNotFoundError, ValueError, KeyError):
                pass  # unknown name, or no timezone database on this machine
        if self.utc_offset is not None:
            return timezone(timedelta(minutes=self.utc_offset))
        return None

    def local_time(self, at: datetime) -> datetime | None:
        """`at` as the user's device would have shown it."""
        zone = self.tzinfo()
        return at.astimezone(zone) if zone else None

    def place(self) -> str | None:
        """A coarse location: the zone's city, the locale's country, or both.

        "Europe/London" plus "en-GB" gives "London, United Kingdom". Neither
        half is precise and neither is meant to be -- a timezone city stands in
        for everywhere that keeps that time.
        """
        city = None
        if self.timezone and "/" in self.timezone:
            city = self.timezone.rsplit("/", 1)[-1].replace("_", " ")
        if city and self.region:
            # "Europe/London" + "United Kingdom" -- but not "London, London".
            return city if city == self.region else f"{city}, {self.region}"
        return city or self.region


def render(situation: Situation, started_at: datetime) -> str:
    """The block that goes in the system prompt, or "" when nothing is known.

    Deliberately labelled as the start of the conversation rather than as the
    current moment. It is a snapshot, and a snapshot presented as a live clock
    is worse than no clock at all -- three hours in, the model would state a
    wrong time with total confidence instead of reaching for the skill that
    knows.
    """
    if not situation.known:
        return ""

    lines: list[str] = []

    local = situation.local_time(started_at)
    if local is not None:
        # DD-MM-YYYY because the preamble asks for dates in that form, and a
        # prompt that models one format while demanding another is a prompt
        # arguing with itself.
        stamp = local.strftime("%A %d-%m-%Y at %H:%M")
        raw_offset = local.strftime("%z")
        offset = f"UTC{raw_offset[:3]}:{raw_offset[3:]}" if raw_offset else ""
        # A named zone gives an abbreviation worth having ("BST"). A bare
        # offset gives one back that just restates the offset, so it is dropped
        # rather than printed twice.
        abbrev = local.strftime("%Z")
        marks = [m for m in (abbrev, offset) if m and m != offset] + ([offset] if offset else [])
        lines.append(f"- Local time: {stamp}" + (f" ({', '.join(marks)})" if marks else ""))

    where = situation.place()
    if where:
        zone = f" ({situation.timezone})" if situation.timezone else ""
        lines.append(f"- General location: {where}{zone}")
    if situation.locale:
        lines.append(f"- Locale: {situation.locale}")

    if not lines:
        return ""

    # The heading carries the framing on its own, deliberately. The preamble
    # says the same thing in general terms, but SYSTEM_PREAMBLE is an env var
    # anyone can replace -- and a bare timestamp under a replaced preamble
    # would read as the current moment, which is the one failure this block
    # exists to prevent. The "use a skill" nudge lives only in the preamble,
    # since losing a nudge is survivable and losing the framing is not.
    return (
        "The user's device reported this when the conversation began:\n"
        + "\n".join(lines)
    )
