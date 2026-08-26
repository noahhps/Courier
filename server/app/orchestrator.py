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

from . import attachments as files
from .config import Settings, ThinkingLevel
from .providers import (
    Chunk,
    ContextOverflow,
    Image,
    Message,
    ProviderError,
    ProviderRouter,
)
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


class Orchestrator:
    def __init__(self, settings: Settings, store: Store, router: ProviderRouter) -> None:
        self.settings = settings
        self.store = store
        self.router = router

    # -- prompt assembly --------------------------------------------------

    def build_system_prompt(self) -> str:
        """Static preamble, first and unchanging.

        Curated memory facts will be appended *after* this string in phase 5,
        precisely so that editing memory invalidates as little of the cached
        prefix as possible.
        """
        return self.settings.system_preamble

    def build_window(
        self,
        history: list[StoredMessage],
        attached: dict[str, list[StoredAttachment]] | None = None,
        *,
        budget: int | None = None,
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
        budget -= estimate_tokens(self.build_system_prompt())

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

        window = [Message(role="system", content=self.build_system_prompt())]
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
        window = self.build_window(history, stored_files)

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
        final: Chunk | None = None
        saved = False
        try:
            thinking_level = think or self.settings.ollama_think
            async for chunk in self._stream_with_recovery(
                provider, window, think=thinking_level
            ):
                # The model's working, not its answer. Sent live and never
                # added to `parts`, so it is not persisted: reopening the
                # conversation gives back the reply alone.
                if chunk.thinking:
                    yield _sse("thinking", {"text": chunk.thinking})
                if chunk.text:
                    parts.append(chunk.text)
                    yield _sse("delta", {"text": chunk.text})
                if chunk.done:
                    final = chunk
        except ProviderError as exc:
            self.router.invalidate_health()
            # Keep whatever arrived before the failure rather than dropping it.
            self._persist(assistant.id, parts, None, provider)
            saved = True
            yield _sse("error", {"message": str(exc), "provider": provider.name})
            return
        finally:
            # Covers client disconnect (CancelledError/GeneratorExit) as well as
            # anything unexpected: a phone dropping off cellular mid-answer
            # should still leave a coherent conversation behind.
            if not saved:
                self._persist(assistant.id, parts, final, provider)

        yield _sse(
            "done",
            {
                "message_id": assistant.id,
                "tokens": final.completion_tokens if final else None,
            },
        )

    async def _stream_with_recovery(
        self, provider, window: list[Message], *, think: str | None = None,
    ) -> AsyncIterator[Chunk]:
        """Section 7: on OOM or overflow, retry once with a smaller window."""
        try:
            async for chunk in provider.stream(window, think=think):
                yield chunk
            return
        except ContextOverflow:
            pass  # fall through to the reduced-context retry

        reduced = [window[0], *window[-5:]] if len(window) > 6 else window
        async for chunk in provider.stream(reduced, think=think):
            yield chunk

    def _persist(
        self, message_id: str, parts: list[str], final: Chunk | None, provider
    ) -> None:
        text = "".join(parts)
        self.store.update_message(
            message_id,
            text,
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


def _truncate_title(text: str, limit: int = 60) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
