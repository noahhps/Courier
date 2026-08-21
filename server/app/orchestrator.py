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

from .config import Settings
from .providers import Chunk, ContextOverflow, Message, ProviderError, ProviderRouter
from .store import Store, StoredMessage


def estimate_tokens(text: str) -> int:
    """Cheap proxy. Real counts come back from the provider and overwrite this."""
    return max(1, len(text) // 4)


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
        self, history: list[StoredMessage], *, budget: int | None = None
    ) -> list[Message]:
        """Most recent turns that fit the budget, oldest-first.

        Trimming from the head is a placeholder for real compaction
        (summarise the middle, keep head and tail) -- that lands in phase 5
        with the rest of the memory work.
        """
        budget = budget or (self.settings.context_tokens - self.settings.reply_tokens)
        budget -= estimate_tokens(self.build_system_prompt())

        selected: list[StoredMessage] = []
        used = 0
        for message in reversed(history):
            cost = message.tokens or estimate_tokens(message.content)
            if used + cost > budget and selected:
                break
            selected.append(message)
            used += cost
        selected.reverse()

        window = [Message(role="system", content=self.build_system_prompt())]
        window.extend(Message(role=m.role, content=m.content) for m in selected)
        return window

    # -- the turn ---------------------------------------------------------

    async def run_turn(
        self,
        session_id: str,
        user_text: str,
        *,
        prefer: str | None = None,
    ) -> AsyncIterator[str]:
        """Yield SSE frames for one turn.

        Frames: `meta` (ids, provider, model), `delta` (token), `done`, `error`.
        """
        user_message = self.store.add_message(session_id, "user", user_text)

        route = await self.router.resolve(prefer)
        provider = route.provider
        history = self.store.list_messages(session_id)
        window = self.build_window(history)

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
            async for chunk in self._stream_with_recovery(provider, window):
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
        self, provider, window: list[Message]
    ) -> AsyncIterator[Chunk]:
        """Section 7: on OOM or overflow, retry once with a smaller window."""
        try:
            async for chunk in provider.stream(window):
                yield chunk
            return
        except ContextOverflow:
            pass  # fall through to the reduced-context retry

        reduced = [window[0], *window[-5:]] if len(window) > 6 else window
        async for chunk in provider.stream(reduced):
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

        seed = first_user.content.strip()
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


def _truncate_title(text: str, limit: int = 60) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
