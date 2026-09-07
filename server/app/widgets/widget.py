"""What a skill shows the reader, beside what it tells the model.

A skill has always returned one thing -- text, which goes straight back into
the prompt. That text is written for a model: it is dense, it repeats the
question back, and it is the wrong shape for a person to read while they wait.

A widget is the other half. Same call, same moment, but structured: a kind
naming one of the preset cards in `catalog.py`, and the fields that card draws.
The card itself is HTML and CSS in the client -- see `client/src/lib/widgets.js`
-- so nothing here decides what anything looks like. This layer only decides
what is true.

Two rules keep the seam honest:

* the model never sees a widget, and the reader never sees the raw text unless
  they open the card. Neither audience is served the other's copy;
* a skill that has nothing worth drawing returns a plain string, exactly as
  before. Widgets are opt-in, one skill at a time.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Widget:
    """One card: which preset to draw, and what to put in it.

    Built through `catalog.build` rather than constructed directly, so a kind
    the client has no template for cannot reach a message.
    """

    kind: str
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        # Copied on the way out. The instance is frozen but its dict is not,
        # and this is what gets serialised into a message row that outlives
        # the turn -- a caller mutating it afterwards would edit history.
        return {"kind": self.kind, "data": dict(self.data)}


class SkillResult(str):
    """A skill's answer, with a card attached to it.

    A `str` subclass rather than a pair of fields, because "a skill returns
    text" is a contract older than widgets and the point of this feature is
    that it does not change it. Everything that already treated a result as a
    string -- the turn loop, the window it is appended to, and the tests
    written against each skill's exact wording -- keeps working untouched, and
    the card is one attribute away for the single caller that wants it.

    The attribute does not survive string operations: slicing or concatenating
    a result gives back a plain `str` with no widget on it. That is why the
    orchestrator reads `.widget` before it trims the text rather than after,
    and why a widget is lifted off exactly once, at the top of the turn loop,
    instead of being carried around.
    """

    widget: Widget | None

    def __new__(cls, text: str, widget: Widget | None = None) -> "SkillResult":
        result = super().__new__(cls, text)
        result.widget = widget
        return result

    def __repr__(self) -> str:
        # The plain string's repr is what a failing assertion should print;
        # the card is not the thing under test in any of them.
        return super().__repr__()
