"""The preset cards, and what each one is allowed to carry.

Preset, not freeform. A skill cannot invent a card: it picks one of the shapes
below and fills it in. That is the whole reason this file exists rather than
skills emitting arbitrary HTML --

* the client's templates are written once, by hand, and reviewed. Nothing that
  arrives at runtime can add a tag, an attribute or a handler to them;
* a field the client never learned to draw fails here, in a test, rather than
  silently rendering an empty box in front of a reader;
* the same skill drawn on the phone and on the desktop is drawn the same way,
  because the shape is agreed on the server and the look is decided in CSS.

Each preset names its required fields, its optional ones, and at most one list
of rows. One list is enough for every card here and keeps both this validator
and the client's template language small; a card that genuinely needs two is a
sign it is really two cards.

**This file and `client/src/lib/widgetPresets.js` are one contract.** Adding a
kind means adding both, and `docs/widgets.md` is where the pair is written down.
"""

from __future__ import annotations

from dataclasses import dataclass

from .widget import Widget

# How many rows a card carries. The card is a glance, not a listing -- the full
# answer is a fold away in the same component, and the model got all of it
# regardless. Rows beyond this are counted into `more` rather than dropped
# silently, so a card that is showing you six of forty says so.
MAX_ROWS = 8

# Per string value. Long enough for a search snippet, short enough that a
# runaway skill cannot double the size of the message row a widget is stored in.
MAX_CHARS = 400


class UnknownWidget(ValueError):
    """A kind with no preset, or a field no preset declares."""


@dataclass(frozen=True)
class Preset:
    """One card's contract.

    `rows` names the single list-valued field, when there is one. Its entries
    are filtered to `row_required` + `row_optional`, and an entry missing a
    required field is dropped rather than drawn half-empty -- a calendar row
    with no title is noise wearing the shape of information.
    """

    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    rows: str = ""
    row_required: tuple[str, ...] = ()
    row_optional: tuple[str, ...] = ()


#: Every card the client can draw, keyed by the name a skill asks for.
KINDS: dict[str, Preset] = {
    # The time, as the clock skill read it. Split into a day and a date rather
    # than one string because the card sets them in different places -- the day
    # is the title line, the date sits under the reading -- and a single
    # "Sunday 06 September 2026" is too long to be either. `note` carries the
    # zone when one was asked for by name, and is absent for the ordinary
    # "what time is it".
    "clock": Preset(
        required=("time", "day", "date"),
        optional=("zone", "note"),
    ),
    # What is on the calendar. One row per event, soonest first, exactly as
    # the skill's own text lists them.
    "agenda": Preset(
        required=("title",),
        optional=("subtitle", "empty"),
        rows="events",
        row_required=("title",),
        row_optional=("when", "detail"),
    ),
    # Web results. `domain` rather than the full URL in the row's own line:
    # the host is the part a reader weighs, and the rest is a link.
    "sources": Preset(
        required=("query",),
        optional=("subtitle", "empty"),
        rows="results",
        row_required=("title",),
        row_optional=("domain", "url", "snippet"),
    ),
    # Passages found in the user's own history.
    "recall": Preset(
        required=("query",),
        optional=("subtitle", "empty"),
        rows="passages",
        row_required=("text",),
        row_optional=("source", "when"),
    ),
    # One fact written down, or crossed out. `action` is what happened, in a
    # word the card prints: "Remembered", "Forgotten".
    "fact": Preset(
        required=("action", "text"),
        optional=("category",),
    ),
    # A directory, or the hits from a search across one.
    "files": Preset(
        required=("path",),
        optional=("subtitle", "empty"),
        rows="entries",
        row_required=("name",),
        row_optional=("kind", "size"),
    ),
    # The fallback, and the one every MCP tool wears: what ran, where it came
    # from, whether it worked. `state` is "done" or "failed" and nothing else
    # -- the card colours a dot from it.
    "tool": Preset(
        required=("name", "state"),
        optional=("source", "summary"),
    ),
}

# Set by `build` when a list was longer than MAX_ROWS. Not declared per preset
# because it is never passed in: it is arithmetic this file does, and every
# card with rows can draw it.
OVERFLOW = "more"

_STATES = ("done", "failed")


def build(kind: str, **fields) -> Widget:
    """One validated card, or a `ValueError` naming what is wrong.

    Raises rather than repairs. Every caller is code in this repository, so a
    bad card is a bug in a skill and the loud version of that is a failing test
    -- a card quietly missing its title is a bug nobody finds until a reader
    is looking at it.

    Empty and `None` values are dropped rather than stored. The templates test
    a field for presence, so "absent" and "there but blank" have to be the same
    thing on the way in, or half the cards grow an empty line.
    """
    preset = KINDS.get(kind)
    if preset is None:
        raise UnknownWidget(
            f"no widget preset called {kind!r}. Known: {', '.join(sorted(KINDS))}."
        )

    allowed = set(preset.required) | set(preset.optional) | ({preset.rows} if preset.rows else set())
    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise UnknownWidget(
            f"the {kind!r} widget has no field(s) {', '.join(unknown)}. "
            f"It takes: {', '.join(sorted(allowed))}."
        )

    data: dict = {}
    for name, value in fields.items():
        if name == preset.rows:
            continue
        clean = _scalar(value)
        if clean is not None:
            data[name] = clean

    if preset.rows and preset.rows in fields:
        rows, dropped = _rows(fields[preset.rows], preset)
        if rows:
            data[preset.rows] = rows
        if dropped:
            data[OVERFLOW] = dropped

    missing = [name for name in preset.required if name not in data]
    if missing:
        raise ValueError(
            f"the {kind!r} widget needs {', '.join(missing)} and was given nothing for it."
        )

    if kind == "tool" and data["state"] not in _STATES:
        raise ValueError(
            f"a tool widget's state is {' or '.join(_STATES)}, not {data['state']!r}."
        )

    return Widget(kind=kind, data=data)


def _scalar(value) -> str | int | float | bool | None:
    """One field, as something JSON and a template can both hold.

    Numbers and booleans pass through: a template's `{{#if}}` reads `False` as
    absent, which is what a flag should mean, and a count printed through `str`
    here would sort as text in any card that ever wanted to.
    """
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    text = " ".join(str(value).split())
    if not text:
        return None
    return text[:MAX_CHARS]


def _rows(raw, preset: Preset) -> tuple[list[dict], int]:
    """The rows a card will draw, and how many were left over.

    Entries may be dicts or objects -- the calendar hands over its own event
    dataclasses -- so fields are read either way and the skill is not made to
    build dictionaries it already has the data for.
    """
    if raw is None:
        return [], 0

    kept: list[dict] = []
    for entry in raw:
        row = {}
        for name in (*preset.row_required, *preset.row_optional):
            value = entry.get(name) if isinstance(entry, dict) else getattr(entry, name, None)
            clean = _scalar(value)
            if clean is not None:
                row[name] = clean
        # A row that lost a required field lost the thing that made it a row.
        if all(name in row for name in preset.row_required):
            kept.append(row)

    return kept[:MAX_ROWS], max(0, len(kept) - MAX_ROWS)
