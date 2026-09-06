"""The preset cards: what a skill may build, and what reaches the reader.

Three things are worth pinning down here, and they are the three ways this
feature could go wrong quietly:

* a card is validated, so a typo in a skill fails in this file rather than
  drawing an empty panel in front of someone;
* a skill that gained a widget still returns its text unchanged, because that
  text is what the model reads and every other test in this directory asserts
  on it;
* a widget survives the whole turn -- streamed while it happens, and stored
  with the answer so reopening the conversation brings the card back.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.db import Database
from app.orchestrator import Orchestrator
from app.providers.base import Chunk, ToolCall
from app.providers.router import ProviderRouter
from app.skills.clock import Clock
from app.skills.registry import Registry
from app.skills.skill import Skill
from app.store import Store
from app.widgets import KINDS, MAX_ROWS, SkillResult, UnknownWidget, Widget, build


# -- the catalogue --------------------------------------------------------


def test_a_card_carries_only_the_fields_its_preset_declares():
    card = build("clock", time="14:05", date="Friday 6 September 2026", zone="BST")
    assert card.kind == "clock"
    assert card.data == {"time": "14:05", "date": "Friday 6 September 2026", "zone": "BST"}


def test_an_unknown_kind_is_refused_by_name():
    with pytest.raises(UnknownWidget) as raised:
        build("weather", temperature="58")
    assert "weather" in str(raised.value)


def test_a_field_the_preset_never_declared_is_refused():
    # The failure mode this replaces: a skill writes `titel`, the card renders
    # with a blank heading, and nothing anywhere says why.
    with pytest.raises(UnknownWidget) as raised:
        build("clock", time="14:05", date="Friday", titel="Now")
    assert "titel" in str(raised.value)


def test_a_required_field_left_empty_is_a_missing_field():
    # "" and None both mean absent, so a required field cannot be satisfied by
    # a blank -- a card whose heading is an empty string is the bug, not the fix.
    with pytest.raises(ValueError):
        build("clock", time="", date="Friday")


def test_optional_blanks_are_dropped_rather_than_stored():
    card = build("clock", time="14:05", date="Friday", zone="", note=None)
    assert "zone" not in card.data and "note" not in card.data


def test_rows_are_filtered_to_the_fields_the_row_declares():
    card = build(
        "agenda",
        title="Next 7 days",
        events=[{"title": "Dentist", "when": "Mon 09:00", "detail": None}],
    )
    assert card.data["events"] == [{"title": "Dentist", "when": "Mon 09:00"}]


def test_a_row_missing_what_makes_it_a_row_is_dropped():
    card = build("agenda", title="Next 7 days", events=[{"when": "Mon 09:00"}])
    assert "events" not in card.data


def test_rows_may_be_objects_rather_than_dicts():
    """The calendar hands over its own event records; nothing rebuilds them."""

    class Event:
        title = "Dentist"
        when = "Mon 09:00"
        detail = "Leeds"

    card = build("agenda", title="This week", events=[Event()])
    assert card.data["events"] == [{"title": "Dentist", "when": "Mon 09:00", "detail": "Leeds"}]


def test_a_long_list_is_cut_and_says_how_much_was_cut():
    card = build(
        "sources",
        query="rain",
        results=[{"title": f"Result {n}"} for n in range(MAX_ROWS + 5)],
    )
    assert len(card.data["results"]) == MAX_ROWS
    assert card.data["more"] == 5


def test_a_tool_card_only_takes_the_two_states_it_can_draw():
    assert build("tool", name="search", state="failed").data["state"] == "failed"
    with pytest.raises(ValueError):
        build("tool", name="search", state="probably")


def test_every_preset_can_be_built_from_its_own_required_fields():
    """A preset nothing can satisfy is a preset with a typo in it."""
    for kind, preset in KINDS.items():
        fields = {name: "x" for name in preset.required}
        if kind == "tool":
            fields["state"] = "done"
        assert build(kind, **fields).kind == kind


def test_a_card_hands_out_a_copy_of_its_own_data():
    # It is about to be serialised into a row that outlives the turn.
    card = build("fact", action="Remembered", text="The user rents in Leeds")
    card.to_dict()["data"]["text"] = "something else"
    assert card.data["text"] == "The user rents in Leeds"


# -- the contract with the model -----------------------------------------


def test_a_result_with_a_card_is_still_the_text_it_always_was():
    result = SkillResult("Friday 6 September 2026, 14:05 BST", build("clock", time="14:05", date="Friday"))
    assert result == "Friday 6 September 2026, 14:05 BST"
    assert "14:05" in result
    assert result.widget.kind == "clock"


def test_the_clock_answers_the_model_in_words_and_the_reader_in_a_card():
    answer = asyncio.run(Clock().use())
    assert answer.widget.kind == "clock"
    # The card's time is the same instant as the sentence, not a second read.
    assert answer.widget.data["time"] in str(answer)


def test_a_clock_that_cannot_answer_draws_nothing():
    # A card is a statement of fact; an apology is not one.
    answer = asyncio.run(Clock().use(timezone="Nowhere/Here"))
    assert getattr(answer, "widget", None) is None


# -- the turn -------------------------------------------------------------


class _Drawing(Skill):
    """A skill with a card, and a second one without, in one class."""

    def __init__(self, *, draws: bool) -> None:
        super().__init__(name="draws" if draws else "silent", description="test skill")
        self.draws = draws

    async def use(self, **kwargs):
        if not self.draws:
            return "nothing to show"
        return SkillResult(
            "the time is 14:05",
            build("clock", time="14:05", date="Friday 6 September 2026"),
        )


class _TwoRounds:
    """Asks for both skills, then answers."""

    name = "mock"
    model = "mock"

    def __init__(self) -> None:
        self.rounds = 0

    async def stream(self, messages, *, think=None, tools=None):
        self.rounds += 1
        if self.rounds == 1:
            yield Chunk(
                done=True,
                tool_calls=(
                    ToolCall(id="1", name="draws", arguments={}),
                    ToolCall(id="2", name="silent", arguments={}),
                ),
            )
        else:
            yield Chunk(text="It is five past two.", done=True)

    async def health(self):
        return True


def _orchestrator(tmp_path: Path) -> tuple[Orchestrator, Store]:
    store = Store(Database(tmp_path / "widgets.db"))
    registry = Registry()
    registry.register(_Drawing(draws=True))
    registry.register(_Drawing(draws=False))

    provider = _TwoRounds()
    router = ProviderRouter.__new__(ProviderRouter)
    router.resolve = lambda prefer=None: _route(provider)
    router.invalidate_health = lambda: None

    settings = type(
        "Settings",
        (),
        {
            "system_preamble": "You are a helpful assistant.",
            "context_tokens": 8192,
            "reply_tokens": 1024,
            "ollama_think": "medium",
            "memory_max_facts": 20,
            "memory_fact_chars": 200,
        },
    )()
    return Orchestrator(settings, store, router, registry), store


async def _route(provider):
    return type("Route", (), {"provider": provider, "reason": "local"})()


@pytest.mark.anyio
async def test_a_card_is_streamed_with_the_result_that_produced_it(tmp_path: Path):
    orchestrator, store = _orchestrator(tmp_path)
    session = store.create_session()

    cards = []
    async for frame in orchestrator.run_turn(session["id"], "what time is it"):
        for line in frame.splitlines():
            if line.startswith("data: ") and '"widget"' in line:
                cards.append(json.loads(line[6:]))

    drawn = {card["name"]: card["widget"] for card in cards}
    assert drawn["draws"]["kind"] == "clock"
    # Both results carry the key. A skill with nothing to draw says so with a
    # null rather than by omitting the field, so the client has one shape to
    # read rather than two.
    assert drawn["silent"] is None


@pytest.mark.anyio
async def test_a_card_is_stored_with_the_answer_and_comes_back_with_it(tmp_path: Path):
    orchestrator, store = _orchestrator(tmp_path)
    session = store.create_session()
    async for _ in orchestrator.run_turn(session["id"], "what time is it"):
        pass

    answer = next(m for m in store.list_messages(session["id"]) if m.role == "assistant")
    used = answer.to_dict()["skills"]
    assert used[0]["widget"]["data"]["time"] == "14:05"
    # And the one with nothing to draw is stored without the key at all, so an
    # old row and a new one that drew nothing read the same way.
    assert "widget" not in used[1]


@pytest.mark.anyio
async def test_a_widget_survives_a_result_long_enough_to_be_trimmed(tmp_path: Path):
    """Slicing a SkillResult loses its card, so the order of the two matters."""
    from app.orchestrator import MAX_RESULT_CHARS, _as_result

    kept = _as_result(SkillResult("x" * (MAX_RESULT_CHARS * 2), build("tool", name="t", state="done")))
    assert len(kept) == MAX_RESULT_CHARS
    assert kept.widget.kind == "tool"


@pytest.mark.anyio
async def test_a_result_that_is_not_a_widget_never_reaches_the_client(tmp_path: Path):
    from app.orchestrator import _as_result

    class Impostor(str):
        widget = {"kind": "clock", "data": {"time": "not validated"}}

    assert _as_result(Impostor("text")).widget is None
    assert isinstance(build("tool", name="t", state="done"), Widget)
