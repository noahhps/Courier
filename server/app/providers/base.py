"""The seam between orchestration and whatever is generating tokens.

Everything upstream of this module talks in `Message` and `Chunk` and never
learns which backend answered. Retrofitting that later means touching every
call site, so it exists before the first line of business logic.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class Message:
    role: str  # system | user | assistant | tool
    content: str


@dataclass(frozen=True)
class Chunk:
    """One streamed piece of a reply.

    `text` carries the delta. The final chunk of a stream sets `done` and, when
    the backend reports them, `prompt_tokens` / `completion_tokens`.
    """

    text: str = ""
    thinking: str = ""
    done: bool = False
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class ProviderError(RuntimeError):
    """Backend failed in a way the caller may want to route around."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class ContextOverflow(ProviderError):
    """Prompt did not fit. Retry once with a smaller window (section 7)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=True)


@runtime_checkable
class ModelProvider(Protocol):
    name: str
    model: str

    def stream(
        self,
        messages: Sequence[Message],
        *,
        think: str | None = None,
        tools: Sequence[dict] | None = None,
    ) -> AsyncIterator[Chunk]: ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def health(self) -> bool: ...
