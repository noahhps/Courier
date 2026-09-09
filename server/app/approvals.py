"""Whether a skill may run, asked rather than assumed.

A skill that is switched on can read your folders, search the web and call
whatever an MCP server exposes, and until now the only say you had was the
switch: on meant "run whenever the model asks", off meant "never". That is a
decision made once, in advance, about calls you have not seen yet.

This is the middle setting. With it on, the turn stops at the call, tells you
which skill wants to run and with what arguments, and waits. Three seams are
worth naming:

* **the turn holds open.** The approval travels as one more SSE frame and the
  answer is still being streamed on the same connection, so the wait costs a
  pending request and nothing else. It is not a second round trip through the
  model, and the conversation does not restart when you answer;

* **a refusal is an answer, not an error.** Denying puts a sentence back into
  the window where the skill's result would have gone, so the model knows it
  was refused and can say so. Raising instead would end the turn and leave the
  reader with a stack trace where a reply should be;

* **silence is refusal.** A request nobody answers times out and is treated as
  a no. The other default -- run it anyway -- would make the feature worse
  than useless, because it would teach you the prompt could be ignored.

Grants come at three widths, and only the widest is written down. "Once" is
this call. "Session" lives in memory on the orchestrator and dies with the
process, because a conversation is the unit of trust here. "Always" is a
stored preference, and the only one that survives a restart.
"""

from __future__ import annotations

import asyncio
import json
import uuid

#: The global switch. Off by default: a harness that interrupts every call the
#: first time it is started teaches the reader to dismiss the prompt, which is
#: the failure mode this whole feature exists to avoid.
APPROVAL_DEFAULTS = {"skills.ask_first": False}

#: Skills the reader has said "always" to, as a JSON list of names in the same
#: `app_settings` table the accent and the memory switches use.
AUTO_APPROVE_KEY = "skills.auto_approve"

# What the client may send back.
ALLOW_ONCE = "allow_once"
ALLOW_SESSION = "allow_session"
ALLOW_ALWAYS = "allow_always"
DENY = "deny"

DECISIONS = (ALLOW_ONCE, ALLOW_SESSION, ALLOW_ALWAYS, DENY)

#: How long a prompt stands before it is treated as a refusal. Long enough to
#: walk away from the desk and come back, short enough that a turn nobody is
#: watching does not hold a connection open for the life of the process.
TIMEOUT_SECONDS = 300.0


def allowed(decision: str) -> bool:
    """Every decision except the refusal lets the call through."""
    return decision in (ALLOW_ONCE, ALLOW_SESSION, ALLOW_ALWAYS)


class Approvals:
    """The prompts currently waiting for an answer, keyed by request id.

    In memory, and deliberately: a pending approval belongs to a stream that is
    open right now. Persisting one would mean restoring, on boot, a question
    about a turn whose connection died with the previous process.
    """

    def __init__(self, timeout: float = TIMEOUT_SECONDS) -> None:
        self._pending: dict[str, asyncio.Future[str]] = {}
        self.timeout = timeout

    def open(self) -> tuple[str, asyncio.Future[str]]:
        """A new pending request, and the future its answer arrives on."""
        request_id = uuid.uuid4().hex
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        return request_id, future

    def resolve(self, request_id: str, decision: str) -> bool:
        """Answer one prompt. False if there is nothing waiting under that id.

        False rather than an exception for the ordinary races: the same button
        pressed twice, or an answer that arrives after the turn was abandoned.
        Neither is a fault worth a 500, and the route reports them as a 404.
        """
        future = self._pending.pop(request_id, None)
        if future is None or future.done():
            return False
        future.set_result(decision)
        return True

    async def wait(self, request_id: str, future: asyncio.Future[str]) -> str:
        """Block until the reader answers, or long enough to call it a no.

        The `finally` matters more than the timeout. If the reader closes the
        tab mid-prompt the generator is cancelled here, and without this the
        entry would sit in the dict for the life of the process holding a
        future nobody can ever resolve.
        """
        try:
            return await asyncio.wait_for(asyncio.shield(future), self.timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return DENY
        finally:
            self._pending.pop(request_id, None)


def read_auto_approved(store) -> set[str]:
    """The skills marked "always", tolerant of a row written by hand.

    A malformed value reads as an empty set rather than raising. This is a
    preference; the cost of losing it is one extra prompt, and the cost of
    raising is a turn that cannot run a skill at all.
    """
    raw = store.get_text_setting(AUTO_APPROVE_KEY)
    if not raw:
        return set()
    try:
        names = json.loads(raw)
    except (TypeError, ValueError):
        return set()
    return {str(name) for name in names} if isinstance(names, list) else set()


def write_auto_approved(store, names: set[str]) -> None:
    """Store the "always" list, sorted so the row does not churn."""
    store.set_text_setting(AUTO_APPROVE_KEY, json.dumps(sorted(names)))
