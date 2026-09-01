"""Turn orchestration -- section 6's request lifecycle.

    message arrives
      -> resolve session, append user message
      -> build system prompt (static preamble first, for cache stability)
      -> assemble window
      -> provider.stream()
      -> stream tokens to the client
      -> persist assistant message
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from . import attachments as files
from .config import Settings, ThinkingLevel
from .memory import MEMORY_DEFAULTS
from .situation import Situation, render as render_situation
from .providers import (
    Chunk,
    ContextOverflow,
    Image,
    Message,
    ProviderError,
    ProviderRouter,
)
from .skills.registry import Registry
from .store import Store, StoredAttachment, StoredMessage




def estimate_tokens(text: str) -> int:
    """Cheap proxy. Real counts come back from the provider and overwrite this."""
    return max(1, len(text) // 4)


# What one image costs the window. Real figures depend on the model and the
# resolution -- a few hundred tokens for a thumbnail, a couple of thousand for
# a screenshot. This sits at the high end deliberately: overcharging trims a
# turn early, undercharging overflows the context, and only one of those is
# recoverable.
IMAGE_TOKENS = 1600

# How many images travel with a request, newest first.
#
# Resending every picture in a long conversation is not just expensive: asked
# about the photo they just attached, a small vision model handed six images
# will answer about one of the others. Older pictures stay in the transcript as
# a named placeholder, so the model knows they existed and can be asked to look
# again by sending one afresh.
MAX_WINDOW_IMAGES = 4


# How many times a turn may go back to the model after running skills. A local
# model handed a shelf of them will loop on near-identical calls; this is the
# thing that stops a bad turn from burning the whole context window.
MAX_TOOL_ROUNDS = 8

# A skill's result is trimmed here rather than in build_window, because the
# window trims from the *head* -- so an unbounded result would push out the
# user's actual question rather than itself.
MAX_RESULT_CHARS = 4000


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        router: ProviderRouter,
        registry: Registry | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.router = router
        self.registry = registry

    def _skill_schemas(self) -> list[dict] | None:
        """What the model is told it can call, or None when it can call nothing.

        `enabled()` rather than `all()`: a skill switched off is still listed on
        the Skills page but must not be offered here. None rather than an empty
        list, because an empty `tools` array still trips the chat template's
        tool branch and tells the model it has a shelf with nothing on it.
        """
        if self.registry is None:
            return None
        schemas = [skill.schema() for _, skill in self.registry.enabled()]
        return schemas or None

    # -- prompt assembly --------------------------------------------------

    def build_system_prompt(self, session_id: str | None = None) -> tuple[str, list[str]]:
        """The preamble, then where the user is, then whatever is remembered.

        Returns (prompt, fact ids).

        Three sections, ordered by how often each changes, because everything
        after the first edit is cache that has to be paid for again:

        * the preamble never changes;
        * the situation is captured once when the conversation starts and is
          fixed for its lifetime;
        * facts can be rewritten by the curation pass after *any* turn.

        So facts stay last. Putting the situation after them would throw away
        the situation's cache every time a fact was learned, for a string that
        had not moved.

        Facts come *after* the static preamble, never before or inside it: the
        preamble is the cacheable prefix, and appending means editing memory
        invalidates only the tail rather than every request that follows.

        They are ordered pinned-first then oldest-first, which is stable across
        turns. `updated_at` would have been the obvious sort and is wrong here
        -- reinforcing a fact would reshuffle the list and throw away the cache
        for a change nobody made.

        The ids come back so the caller can record that these were used without
        this method reaching for the clock or writing a row; it is called while
        assembling a prompt, and a prompt builder that writes to the database
        is a prompt builder you cannot call twice.
        """
        prompt = self.settings.system_preamble

        situation = self._situation_block(session_id)
        if situation:
            prompt = f"{prompt}\n\n{situation}"

        if not self._memory_enabled():
            return prompt, []

        facts = self.store.active_facts(limit=self.settings.memory_max_facts)
        if not facts:
            return prompt, []

        lines = "\n".join(
            f"- {fact.text[: self.settings.memory_fact_chars]}" for fact in facts
        )
        return (
            f"{prompt}\n\n"
            "What you already know about the user, from previous "
            f"conversations:\n{lines}",
            [fact.id for fact in facts],
        )

    def _situation_block(self, session_id: str | None) -> str:
        """The user's time and rough whereabouts, as their device reported them.

        Empty for a session that never said -- an API client with no browser
        behind it is an ordinary caller. Saying nothing is the right answer
        there: the preamble already tells the model to admit it does not know
        the date, and the server's own clock would be an answer to a different
        question.
        """
        if not session_id:
            return ""
        situation = self.store.session_situation(session_id)
        if not situation.known:
            return ""
        session = self.store.get_session(session_id)
        if not session:
            return ""
        # created_at is milliseconds, and is the moment the conversation began
        # rather than the moment this prompt is being assembled -- which is the
        # whole reason the block is stable enough to cache.
        started = datetime.fromtimestamp(session["created_at"] / 1000, tz=timezone.utc)
        return render_situation(situation, started)

    def _memory_enabled(self) -> bool:
        """The "Remember between chats" switch, checked where it matters.

        Enforced here as well as in the curation pass: a switch that only stops
        new facts being written, while the ones already stored keep arriving in
        every prompt, has not turned anything off.
        """
        return self.store.get_settings(MEMORY_DEFAULTS)["memory.between_chats"]

    def build_window(
        self,
        history: list[StoredMessage],
        attached: dict[str, list[StoredAttachment]] | None = None,
        *,
        budget: int | None = None,
        system: str | None = None,
        session_id: str | None = None,
    ) -> list[Message]:
        """Most recent turns that fit the budget, oldest-first.

        Trimming from the head is a placeholder for real compaction
        (summarise the middle, keep head and tail) -- that lands in phase 5
        with the rest of the memory work.

        Attached files are charged against the same budget as the words around
        them, so a conversation full of screenshots trims to fewer turns rather
        than quietly overflowing the context.
        """
        attached = attached or {}
        budget = budget or (self.settings.context_tokens - self.settings.reply_tokens)
        # Built once. It used to be called twice here -- to charge the budget
        # and again to build the message -- which was free while it was an
        # attribute read and is two queries now that memory is in it. Worse,
        # the two calls could disagree if a fact were edited between them,
        # charging the window for a prompt it did not send.
        if system is None:
            # session_id matters here only for the budget: without it the
            # prompt built for costing would be missing the situation block
            # that the prompt actually sent contains.
            system, _ = self.build_system_prompt(session_id)
        budget -= estimate_tokens(system)

        # Which images ride along is decided first, newest backwards, so the
        # cost of a turn reflects what will actually be sent with it.
        carried: set[str] = set()
        for message in reversed(history):
            for item in reversed(attached.get(message.id, ())):
                if item.kind == "image" and len(carried) < MAX_WINDOW_IMAGES:
                    carried.add(item.id)

        selected: list[StoredMessage] = []
        used = 0
        for message in reversed(history):
            cost = message.tokens or estimate_tokens(message.content)
            cost += _attachment_cost(attached.get(message.id, ()), carried)
            if used + cost > budget and selected:
                break
            selected.append(message)
            used += cost
        selected.reverse()

        window = [Message(role="system", content=system)]
        window.extend(_to_message(m, attached.get(m.id, ()), carried) for m in selected)
        return window

    # -- the turn ---------------------------------------------------------

    async def run_turn(
        self,
        session_id: str,
        user_text: str,
        *,
        attached: list[files.IncomingFile] | None = None,
        think: ThinkingLevel | None = None,
        prefer: str | None = None,
    ) -> AsyncIterator[str]:
        """Yield SSE frames for one turn.

        Frames: `meta` (ids, provider, model), `delta` (token), `done`, `error`.
        """
        user_message = self.store.add_message(session_id, "user", user_text)
        for incoming in attached or ():
            self.store.add_attachment(
                user_message.id,
                kind=incoming.kind,
                name=incoming.name,
                mime=incoming.mime,
                data=incoming.data,
                text=incoming.text,
            )

        route = await self.router.resolve(prefer)
        provider = route.provider
        history = self.store.list_messages(session_id)
        # Bytes, not just names: this is the one call that needs them.
        stored_files = self.store.attachments_for_session(session_id, with_data=True)
        system, fact_ids = self.build_system_prompt(session_id)
        window = self.build_window(history, stored_files, system=system)
        # One batched update, not one statement per fact per turn. This is what
        # "12 answers" under a fact on the memory page is counting, and what
        # keeps an unused inferred fact fading rather than lingering forever.
        self.store.mark_facts_used(fact_ids)

        assistant = self.store.add_message(
            session_id,
            "assistant",
            "",
            model=provider.model,
            provider=provider.name,
        )

        yield _sse(
            "meta",
            {
                "user_message_id": user_message.id,
                "message_id": assistant.id,
                "provider": provider.name,
                "model": provider.model,
                "source": route.reason,
            },
        )

        parts: list[str] = []
        # The working, kept so it can be stored with the answer. Reopening a
        # conversation used to give back the reply alone, which loses the one
        # thing worth auditing about a turn that ran skills: what it read.
        reasoning: list[str] = []
        used: list[dict] = []
        final: Chunk | None = None
        saved = False
        try:
            thinking_level = think or self.settings.ollama_think
            tools = self._skill_schemas()

            # One pass per round. A round ends when the model stops; if it
            # stopped to ask for skills, they run and the window goes back with
            # their answers appended. `parts` accumulates across rounds, so an
            # answer written either side of a skill call arrives as one reply.
            for _ in range(MAX_TOOL_ROUNDS):
                final = None
                round_text: list[str] = []

                async for chunk in self._stream_with_recovery(
                    provider, window, think=thinking_level, tools=tools
                ):
                    # The model's working, not its answer -- kept apart from
                    # `parts` so it is never mistaken for the reply, but stored
                    # alongside it.
                    if chunk.thinking:
                        reasoning.append(chunk.thinking)
                        yield _sse("thinking", {"text": chunk.thinking})
                    if chunk.text:
                        round_text.append(chunk.text)
                        parts.append(chunk.text)
                        yield _sse("delta", {"text": chunk.text})
                    if chunk.done:
                        final = chunk

                if final is None or not final.tool_calls:
                    break

                # What the model said on its way to asking, plus the asking
                # itself. Both have to go back or the next round replays a
                # conversation where nothing was requested.
                window.append(
                    Message(
                        role="assistant",
                        content="".join(round_text),
                        tool_calls=final.tool_calls,
                    )
                )

                for call in final.tool_calls:
                    yield _sse(
                        "tool_call", {"name": call.name, "arguments": call.arguments}
                    )
                    # Recorded before it runs, so a skill that raises or a turn
                    # the reader abandons still leaves evidence it was asked
                    # for. `finally` persists whatever this list holds.
                    record = {"name": call.name, "arguments": call.arguments}
                    used.append(record)
                    result = await self._run_skill(call, session_id)
                    record["result"] = result
                    yield _sse("tool_result", {"name": call.name, "text": result})
                    window.append(
                        Message(
                            role="tool",
                            content=result,
                            tool_call_id=call.id,
                            tool_name=call.name,
                        )
                    )
        except ProviderError as exc:
            self.router.invalidate_health()
            # Keep whatever arrived before the failure rather than dropping it.
            self._persist(assistant.id, parts, None, provider, reasoning, used)
            saved = True
            yield _sse("error", {"message": str(exc), "provider": provider.name})
            return
        finally:
            # Covers client disconnect (CancelledError/GeneratorExit) as well as
            # anything unexpected: a phone dropping off cellular mid-answer
            # should still leave a coherent conversation behind.
            if not saved:
                self._persist(assistant.id, parts, final, provider, reasoning, used)

        # A turn that ends with nothing to show is a failure, even though every
        # frame arrived and no exception was raised. Left alone it reaches the
        # thread as an assistant bubble that never fills, which reads as a hang
        # and gives no clue whose fault it was.
        #
        # There are two ways to get here and they have different fixes, so they
        # get different sentences.
        if not "".join(parts).strip():
            yield _sse(
                "error",
                {"message": _silent_turn_reason(final), "provider": provider.name},
            )
            return

        yield _sse(
            "done",
            {
                "message_id": assistant.id,
                "tokens": final.completion_tokens if final else None,
            },
        )

    async def _stream_with_recovery(
        self,
        provider,
        window: list[Message],
        *,
        think: str | None = None,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[Chunk]:
        """Section 7: on OOM or overflow, retry once with a smaller window.

        The tool loop wraps *around* this rather than inside it, so overflow
        recovery still applies to every round of a turn -- including the ones
        that come back carrying a skill's output.
        """
        try:
            async for chunk in provider.stream(window, think=think, tools=tools):
                yield chunk
            return
        except ContextOverflow:
            pass  # fall through to the reduced-context retry

        reduced = [window[0], *window[-5:]] if len(window) > 6 else window
        async for chunk in provider.stream(reduced, think=think, tools=tools):
            yield chunk

    async def _run_skill(self, call, session_id: str | None = None) -> str:
        """One skill call, reduced to text the model can read.

        Every failure returns rather than raises. A model that mistypes an
        argument name should cost one round and be told what it got wrong --
        not kill the conversation with a TypeError.
        """
        skill = self.registry.get(call.name) if self.registry else None
        if skill is None:
            known = ", ".join(name for name, _ in self.registry.enabled()) if self.registry else ""
            return (
                f"There is no skill called {call.name!r}."
                + (f" Available: {known}." if known else "")
            )
        if not skill.enabled:
            return f"{call.name} is switched off."
        arguments = dict(call.arguments)
        if skill.wants_context and session_id:
            # Assigned after the copy, so a model that hallucinates a `context`
            # argument cannot talk over the real one.
            arguments["context"] = self.store.session_situation(session_id)
        try:
            result = await skill.use(**arguments)
        except TypeError as exc:
            # Almost always a hallucinated or missing argument name.
            return f"{call.name} was called wrongly: {exc}"
        except Exception as exc:
            return f"{call.name} failed: {type(exc).__name__}: {exc}"
        return str(result)[:MAX_RESULT_CHARS]

    def _persist(
        self,
        message_id: str,
        parts: list[str],
        final: Chunk | None,
        provider,
        reasoning: list[str] | None = None,
        skills: list[dict] | None = None,
    ) -> None:
        text = "".join(parts)
        self.store.update_message(
            message_id,
            text,
            reasoning="".join(reasoning or ()) or None,
            skills=skills or None,
            tokens=(final.completion_tokens if final else None)
            or (estimate_tokens(text) if text else None),
            model=provider.model,
            provider=provider.name,
        )

    # -- titles -----------------------------------------------------------

    async def ensure_title(self, session_id: str) -> str | None:
        """Name a session from its first exchange. Runs off the response path."""
        session = self.store.get_session(session_id)
        if not session or session.get("title"):
            return None

        history = self.store.list_messages(session_id)
        first_user = next((m for m in history if m.role == "user"), None)
        if not first_user:
            return None

        # A turn can be nothing but a dropped image. Name it after the file
        # rather than leaving the conversation blank in the sidebar.
        seed = first_user.content.strip()
        if not seed:
            named = self.store.attachments_for_session(session_id).get(first_user.id, ())
            seed = ", ".join(a.name for a in named)
        if not seed:
            return None

        title = _truncate_title(seed)
        try:
            route = await self.router.resolve()
            prompt = [
                Message(
                    role="system",
                    content=(
                        "Title this conversation in at most six words. "
                        "Reply with the title alone -- no quotes, no punctuation "
                        "at the end, no preamble."
                    ),
                ),
                Message(role="user", content=seed[:2000]),
            ]
            generated = []
            async for chunk in route.provider.stream(prompt):
                if chunk.text:
                    generated.append(chunk.text)
            candidate = "".join(generated).strip().strip('"').splitlines()[0]
            if candidate:
                title = _truncate_title(candidate)
        except (ProviderError, IndexError):
            pass  # the truncated first message is a perfectly good fallback

        self.store.rename_session(session_id, title)
        return title


def _to_message(stored: StoredMessage, attached, carried: set[str]) -> Message:
    """One stored turn as the providers see it.

    Text files are pasted in ahead of what the user typed, so their question
    lands last and reads as being about the files above it. Images travel
    beside the text rather than in it -- see providers/base.py -- except for
    the older ones, which are left as a line of text saying they were here.
    """
    text: list[str] = []
    images: list[Image] = []

    for item in attached:
        if item.kind != "image":
            text.append(files.as_prompt_text(item.name, _readable(item)))
        elif item.id in carried:
            # Files stored before uploads were normalised can still be in a
            # format the runner refuses, which would fail this turn and every
            # later one in the conversation. Converting on the way out costs a
            # few milliseconds and leaves the stored original untouched.
            name, mime, data = item.name, item.mime, item.data
            if mime not in files.STORABLE_MIMES:
                name, mime, data = files.normalize_image(name, mime, data)
            images.append(Image(name=name, mime=mime, data=data))
        else:
            text.append(f"[earlier image: {item.name}]")

    if stored.content:
        text.append(stored.content)

    return Message(role=stored.role, content="\n\n".join(text), images=tuple(images))


def _readable(item) -> str:
    """The words in an attachment.

    A text file is its own bytes; a document was read at upload and the result
    is in the column beside them.
    """
    if item.kind == "document":
        return item.text or ""
    return (item.data or b"").decode("utf-8", "replace")


def _attachment_cost(attached, carried: set[str]) -> int:
    total = 0
    for item in attached:
        if item.kind != "image":
            total += estimate_tokens(files.as_prompt_text(item.name, _readable(item)))
        elif item.id in carried:
            total += IMAGE_TOKENS
        else:
            total += estimate_tokens(f"[earlier image: {item.name}]")
    return total


def _silent_turn_reason(final: Chunk | None) -> str:
    """Why a turn produced no visible answer, in a sentence the user reads.

    The message names the missing piece rather than the symptom, because the
    symptom -- an empty bubble -- is the same in every case and tells nobody
    anything.
    """
    if final is not None and final.tool_calls:
        # The loop ran its full allowance of rounds and the model was still
        # asking for more rather than writing an answer -- so the last round's
        # calls were never run. Naming them is the useful part: it is almost
        # always the same skill over and over, and the fix is in what that
        # skill returns rather than anywhere near here.
        asked = ", ".join(sorted({call.name for call in final.tool_calls})) or "a skill"
        return (
            f"The model used all {MAX_TOOL_ROUNDS} rounds of skill calls without "
            f"writing an answer, and was still asking for {asked}. Run "
            "`python -m tools.why_silent` to see what it was told each time."
        )
    return (
        "The model finished without saying anything. That usually means it "
        "believed it should use a skill and had none offered: check that the "
        "system preamble isn't promising abilities the request doesn't declare "
        "in `tools`."
    )


def _truncate_title(text: str, limit: int = 60) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
