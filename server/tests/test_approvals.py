"""Per-call skill approval: the broker, the standing grants, and the turn.

The turn tests are the ones that matter. Everything else here could pass while
the feature was still broken in the only way that counts -- a prompt that never
reaches the reader, or one that is drawn and then ignored.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.approvals import (
    ALLOW_ALWAYS,
    ALLOW_ONCE,
    ALLOW_SESSION,
    APPROVAL_DEFAULTS,
    AUTO_APPROVE_KEY,
    DENY,
    Approvals,
    allowed,
    read_auto_approved,
    write_auto_approved,
)
from app.db import Database
from app.orchestrator import Orchestrator
from app.providers.base import Chunk, ToolCall
from app.providers.router import ProviderRouter
from app.skills.registry import Registry
from app.skills.skill import Skill
from app.store import Store


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Recorder(Skill):
    """A skill that says whether it ran, which is the whole question here."""

    def __init__(self) -> None:
        super().__init__(name="peek", description="Look at something")
        self.ran = 0

    async def use(self, **kwargs) -> str:
        self.ran += 1
        return "the thing it saw"


class OneCallProvider:
    """Asks for `peek` once, then answers with whatever came back."""

    def __init__(self) -> None:
        self.name, self.model, self.rounds = "mock", "mock", 0

    async def stream(self, messages, *, think=None, tools=None):
        self.rounds += 1
        if self.rounds == 1:
            yield Chunk(
                text="",
                done=True,
                tool_calls=(ToolCall(id="c1", name="peek", arguments={"path": "~/Notes"}),),
            )
        else:
            yield Chunk(text="Done.", done=True)

    async def embed(self, texts):
        return [[0.0] * 8 for _ in texts]

    async def health(self):
        return True


def _orchestrator(tmp_path: Path):
    """A turn loop with one skill in it, wired the way the MCP turn test does."""
    store = Store(Database(tmp_path / "approvals.db"))
    registry = Registry()
    skill = Recorder()
    registry.register(skill)

    provider = OneCallProvider()
    router = ProviderRouter.__new__(ProviderRouter)
    router.local = provider
    router.cloud = provider

    async def resolve(prefer=None):
        return type("Route", (), {"provider": provider, "reason": "local"})()

    router.resolve = resolve
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
    return Orchestrator(settings, store, router, registry), store, skill


async def _run(orch, store, message="look"):
    """Drive a whole turn, collecting the frames it emitted."""
    session_id = store.create_session()["id"]
    frames = []
    async for frame in orch.run_turn(session_id, message):
        frames.append(frame)
    return session_id, frames


def _events(frames, name):
    """Every payload for one event type, decoded."""
    out = []
    for frame in frames:
        if frame.startswith(f"event: {name}\n"):
            out.append(json.loads(frame.split("data: ", 1)[1].strip()))
    return out


# -- the broker -----------------------------------------------------------


def test_every_decision_but_the_refusal_lets_the_call_through():
    assert allowed(ALLOW_ONCE) and allowed(ALLOW_SESSION) and allowed(ALLOW_ALWAYS)
    assert not allowed(DENY)


@pytest.mark.anyio
async def test_an_answer_reaches_the_waiter():
    approvals = Approvals()
    request_id, future = approvals.open()
    waiting = asyncio.ensure_future(approvals.wait(request_id, future))
    await asyncio.sleep(0)
    assert approvals.resolve(request_id, ALLOW_ONCE)
    assert await waiting == ALLOW_ONCE


@pytest.mark.anyio
async def test_the_same_prompt_cannot_be_answered_twice():
    approvals = Approvals()
    request_id, future = approvals.open()
    waiting = asyncio.ensure_future(approvals.wait(request_id, future))
    await asyncio.sleep(0)
    assert approvals.resolve(request_id, ALLOW_ONCE)
    # The second press, or an answer racing a turn that already moved on.
    assert not approvals.resolve(request_id, DENY)
    assert await waiting == ALLOW_ONCE


@pytest.mark.anyio
async def test_silence_is_a_refusal_rather_than_a_hang():
    approvals = Approvals(timeout=0.05)
    request_id, future = approvals.open()
    assert await approvals.wait(request_id, future) == DENY
    # And the entry is gone, rather than held for the life of the process.
    assert not approvals.resolve(request_id, ALLOW_ONCE)


# -- the standing grants --------------------------------------------------


def test_the_always_list_round_trips(tmp_path: Path):
    store = Store(Database(tmp_path / "s.db"))
    assert read_auto_approved(store) == set()
    write_auto_approved(store, {"peek", "clock"})
    assert read_auto_approved(store) == {"peek", "clock"}


def test_a_hand_written_always_row_reads_as_empty_rather_than_raising(tmp_path: Path):
    """Losing the preference costs one prompt. Raising costs the whole turn."""
    store = Store(Database(tmp_path / "s.db"))
    store.set_text_setting(AUTO_APPROVE_KEY, "not json at all")
    assert read_auto_approved(store) == set()


def test_the_switch_is_off_until_it_is_turned_on(tmp_path: Path):
    store = Store(Database(tmp_path / "s.db"))
    assert store.get_settings(APPROVAL_DEFAULTS)["skills.ask_first"] is False


# -- the turn -------------------------------------------------------------


@pytest.mark.anyio
async def test_with_the_switch_off_nothing_is_asked(tmp_path: Path):
    orch, store, skill = _orchestrator(tmp_path)
    _, frames = await _run(orch, store)
    assert _events(frames, "skill_approval") == []
    assert skill.ran == 1


@pytest.mark.anyio
async def test_the_prompt_names_the_skill_and_its_arguments(tmp_path: Path):
    """What is being asked for is the decision. A prompt that hides the
    arguments is one that trains the reader to press yes."""
    orch, store, skill = _orchestrator(tmp_path)
    store.set_settings({"skills.ask_first": True})

    async def answer_once():
        for _ in range(200):
            await asyncio.sleep(0.01)
            pending = list(orch.approvals._pending)
            if pending:
                orch.approvals.resolve(pending[0], ALLOW_ONCE)
                return

    answering = asyncio.ensure_future(answer_once())
    _, frames = await _run(orch, store)
    await answering

    asked = _events(frames, "skill_approval")
    assert len(asked) == 1
    assert asked[0]["name"] == "peek"
    assert asked[0]["arguments"] == {"path": "~/Notes"}
    assert asked[0]["id"]
    assert skill.ran == 1


@pytest.mark.anyio
async def test_a_refusal_stops_the_skill_and_tells_the_model_why(tmp_path: Path):
    orch, store, skill = _orchestrator(tmp_path)
    store.set_settings({"skills.ask_first": True})

    async def refuse():
        for _ in range(200):
            await asyncio.sleep(0.01)
            pending = list(orch.approvals._pending)
            if pending:
                orch.approvals.resolve(pending[0], DENY)
                return

    refusing = asyncio.ensure_future(refuse())
    _, frames = await _run(orch, store)
    await refusing

    assert skill.ran == 0
    result = _events(frames, "tool_result")[0]
    assert result["denied"] is True
    # The model is told, in the window, rather than the turn ending in an error.
    assert "declined" in result["text"]
    assert _events(frames, "error") == []


@pytest.mark.anyio
async def test_always_is_written_down_and_the_next_turn_does_not_ask(tmp_path: Path):
    orch, store, skill = _orchestrator(tmp_path)
    store.set_settings({"skills.ask_first": True})

    async def answer_always():
        for _ in range(200):
            await asyncio.sleep(0.01)
            pending = list(orch.approvals._pending)
            if pending:
                orch.approvals.resolve(pending[0], ALLOW_ALWAYS)
                return

    answering = asyncio.ensure_future(answer_always())
    await _run(orch, store)
    await answering
    assert read_auto_approved(store) == {"peek"}

    # A second turn, with nobody there to answer: it must not ask again.
    orch.router.local.rounds = 0
    _, frames = await _run(orch, store, "again")
    assert _events(frames, "skill_approval") == []
    assert skill.ran == 2


@pytest.mark.anyio
async def test_a_session_grant_covers_that_conversation_and_no_other(tmp_path: Path):
    """The grant is scoped to the conversation, and is not written down."""
    orch, store, skill = _orchestrator(tmp_path)
    store.set_settings({"skills.ask_first": True})

    session_id = store.create_session()["id"]
    orch._remember_decision("peek", session_id, ALLOW_SESSION)

    assert orch._standing_decision("peek", session_id) == ALLOW_SESSION
    assert orch._standing_decision("peek", "some-other-session") is None
    # Nothing persisted: a restart asks again.
    assert read_auto_approved(store) == set()


def test_a_refusal_is_never_remembered(tmp_path: Path):
    """A no is about this call. Storing it would turn one cautious answer into
    a skill that quietly stops working with nothing on screen to say why."""
    orch, store, _ = _orchestrator(tmp_path)
    session_id = store.create_session()["id"]
    orch._remember_decision("peek", session_id, DENY)
    assert read_auto_approved(store) == set()
    assert orch._session_grants.get(session_id, set()) == set()
