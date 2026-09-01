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
from typing import Any

from .base import Chunk, ContextOverflow, Message, ProviderError, ToolCall

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
            self._client = AsyncAnthropic()
        return self._client

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[dict] | None = None,
        think: str | None = None,
    ) -> AsyncIterator[Chunk]:
        client = self._ensure_client()

        # Anthropic takes the system prompt as its own parameter
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        turns = _build_turns(messages)

        request: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": turns,
            "output_config": {"effort": think or _EFFORT},
        }
        if system:
            request["system"] = system
        if tools:
            request["tools"] = [
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "input_schema": t.get("parameters")
                    or {"type": "object", "properties": {}},
                }
                for t in tools
            ]

        try:
            async with client.messages.stream(**request) as stream:
                async for text in stream.text_stream:
                    yield Chunk(text=text)
                final = await stream.get_final_message()

            if final.stop_reason == "refusal":
                raise ProviderError("cloud model declined the request")

            tool_calls: list[ToolCall] = []
            for block in getattr(final, "content", []):
                if getattr(block, "type", "") == "tool_use":
                    tool_calls.append(
                        ToolCall(
                            id=str(block.id),
                            name=str(block.name),
                            arguments=dict(getattr(block, "input", {}) or {}),
                        )
                    )

            yield Chunk(
                text="",
                done=True,
                tool_calls=tuple(tool_calls),
                prompt_tokens=final.usage.input_tokens if final.usage else None,
                completion_tokens=final.usage.output_tokens if final.usage else None,
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


def _build_turns(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Build conversation turns formatted for the Anthropic Messages API."""
    turns: list[dict[str, Any]] = []

    for m in messages:
        if m.role == "system":
            continue

        if m.role == "tool":
            # Anthropic expects tool results as role="user" content blocks
            tool_result_block = {
                "type": "tool_result",
                "tool_use_id": m.tool_call_id or "call_0",
                "content": m.content,
            }
            if turns and turns[-1]["role"] == "user" and isinstance(turns[-1]["content"], list):
                turns[-1]["content"].append(tool_result_block)
            else:
                turns.append({"role": "user", "content": [tool_result_block]})

        elif m.role == "assistant":
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            for call in m.tool_calls:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                )
            turns.append({"role": "assistant", "content": blocks or m.content})

        elif m.role == "user":
            turns.append({"role": "user", "content": _content(m)})

    return turns


def _content(message: Message):
    if not message.images:
        return message.content

    blocks: list[dict] = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": image.mime, "data": image.b64()},
        }
        for image in message.images
    ]
    if message.content:
        blocks.append({"type": "text", "text": message.content})
    return blocks
