"""The seam between orchestration and whatever is generating tokens.

Everything upstream of this module talks in `Message` and `Chunk` and never
learns which backend answered. Retrofitting that later means touching every
call site, so it exists before the first line of business logic.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class Image:
    """An image riding along with a message.

    Raw bytes, not base64: every backend wants a different encoding, and this
    way the choice is made once per provider instead of guessed here.
    """

    name: str
    mime: str
    data: bytes

    def b64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")


@dataclass(frozen=True)
class ToolCall:
    """A model asking for a skill, normalised across backends.

    `id` is the only thing that pairs a call with its result. Anthropic issues
    one; Ollama does not, so the provider mints it on the way up. Nothing above
    this module may read meaning into it beyond "these two go together".

    `arguments` is already decoded to a dict. Ollama sends a JSON object and
    Anthropic sends a dict, but a model under load will occasionally emit a
    JSON *string* instead -- normalising that is the provider's job, so callers
    never have to guess which they got.

    Named ToolCall rather than SkillCall deliberately: this is the vocabulary
    of the model APIs underneath, and the boundary where the app's word for it
    becomes theirs should be visible.
    """

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: str  # system | user | assistant | tool
    content: str
    # Only images. Text files are folded into `content` upstream, because every
    # model can read text and only some can see -- so inlining is the one form
    # that always works.
    images: tuple[Image, ...] = ()
    # Set on an assistant turn that asked for skills. Empty on every other turn.
    tool_calls: tuple[ToolCall, ...] = ()
    # Set on a role="tool" turn, naming the call it answers. Both are carried
    # because the backends pair results differently: Anthropic on the id,
    # Ollama on the name. Filling in one and not the other means the turn
    # replays correctly on one provider and silently mismatches on the other.
    tool_call_id: str | None = None
    tool_name: str | None = None


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
    # Populated on the final chunk when the model stopped to ask for skills.
    # It rides on `done` rather than arriving mid-stream because neither
    # backend can promise a complete, parseable call before the stop event --
    # a half-streamed argument object is not something a caller can act on.
    tool_calls: tuple[ToolCall, ...] = ()
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
