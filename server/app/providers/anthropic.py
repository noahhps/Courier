"""Cloud fallback. Only reached when the local model is unreachable.

Section 7: the fallback is what makes the tool trustworthy enough to use daily.
It is never used silently -- the orchestrator records which provider answered
and the client shows it.

The `anthropic` package is imported lazily so a purely local install doesn't
need it. Set ANTHROPIC_API_KEY (or run `ant auth login`) to enable this path.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Sequence

from .base import Chunk, ContextOverflow, Message, ProviderError

# Fallback answers should arrive quickly; keep the reasoning budget modest.
_EFFORT = os.environ.get("ANTHROPIC_EFFORT", "medium")


class AnthropicProvider:
    def __init__(self, model: str = "claude-opus-5", max_tokens: int = 8192) -> None:
        self.name = "anthropic"
        self.model = model
        self.max_tokens = max_tokens
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic
            except ImportError as exc:  # pragma: no cover - depends on install
                raise ProviderError(
                    "cloud fallback requires `pip install anthropic`"
                ) from exc
            # Zero-arg construction resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN,
            # or an `ant auth login` profile, in that order.
            self._client = AsyncAnthropic()
        return self._client

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[dict] | None = None,
    ) -> AsyncIterator[Chunk]:
        client = self._ensure_client()

        # Anthropic takes the system prompt as its own parameter rather than a
        # message; everything else maps across directly.
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        turns = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]

        request: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": turns,
            "output_config": {"effort": _EFFORT},
        }
        if system:
            request["system"] = system
        if tools:
            request["tools"] = list(tools)

        try:
            async with client.messages.stream(**request) as stream:
                async for text in stream.text_stream:
                    yield Chunk(text=text)
                final = await stream.get_final_message()

            if final.stop_reason == "refusal":
                raise ProviderError("cloud model declined the request")

            yield Chunk(
                text="",
                done=True,
                prompt_tokens=final.usage.input_tokens,
                completion_tokens=final.usage.output_tokens,
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise _translate_error(exc) from exc

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise ProviderError("no embedding model on the cloud fallback path")

    async def health(self) -> bool:
        try:
            self._ensure_client()
        except ProviderError:
            return False
        return bool(
            os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")
            or (os.path.expanduser("~/.config/anthropic") and _profile_exists())
        )


def _profile_exists() -> bool:
    from pathlib import Path

    return (Path.home() / ".config" / "anthropic" / "credentials").exists()


def _translate_error(exc: Exception) -> ProviderError:
    name = type(exc).__name__
    text = str(exc)
    if "prompt is too long" in text or "context" in text.lower():
        return ContextOverflow(text)
    retryable = name in ("RateLimitError", "APIConnectionError", "InternalServerError")
    return ProviderError(f"{name}: {text[:500]}", retryable=retryable)
