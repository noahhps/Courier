"""Ollama on 127.0.0.1. The default and, on a good day, the only provider."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence

import httpx

from .base import Chunk, ContextOverflow, Message, ProviderError, ToolCall

# Generation can idle for a long time behind a cold model load; the read
# timeout has to tolerate that, while connect stays short so a dead Ollama
# fails over quickly instead of hanging the request.
_TIMEOUT = httpx.Timeout(connect=3.0, read=300.0, write=30.0, pool=5.0)


class OllamaProvider:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        embed_model: str = "nomic-embed-text",
        context_tokens: int = 32768,
    ) -> None:
        self.name = "ollama"
        self.model = model
        self.embed_model = embed_model
        self.context_tokens = context_tokens
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=_TIMEOUT)

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        think: str | None = None,
        # Neutral schemas from Skill.schema(), handed down by the orchestrator.
        # The provider never looks a skill up -- it only serialises what it is
        # given, which is what keeps this module importable on its own.
        tools: Sequence[dict] | None = None,
    ) -> AsyncIterator[Chunk]:
        payload: dict = {
            "model": self.model,
            "messages": [_encode(m) for m in messages],
            "stream": True,
            "options": {"num_ctx": self.context_tokens},
        }
        # Thinking and vision do not mix on the local runner. With a reasoning
        # pass on, gemma4 answered image prompts with "no image was provided" --
        # the picture is accepted, then lost before the model looks at it. A
        # turn carrying an image drops the key entirely and takes the model's
        # own default, which is the conservative reading of a bug whose exact
        # shape varies by model.
        carries_images = any(m.images for m in messages)
        if think is not None and not carries_images:
            payload["think"] = think;
        
        # Ollama wants each schema wrapped as a function declaration. That
        # wrapper is this backend's spelling, so it is applied here rather than
        # baked into Skill.schema() -- Anthropic spells the same thing
        # differently, and neither shape belongs upstream.
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]

        try:
            async with self._client.stream("POST", "/api/chat", json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise _translate_error(response.status_code, body)

                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # keep-alive noise

                    if error := event.get("error"):
                        raise _translate_error(200, str(error))

                    message = event.get("message") or {}
                    text = message.get("content", "")
                    thinking = message.get("thinking", "")
                    raw = message.get("tool_calls") or []
                    calls = tuple(_parse_call(c, i) for i, c in enumerate(raw))

                    # `done` is not an alternative to the rest -- the final
                    # event carries the stop flag, the last of the content and
                    # any tool calls together. So the calls are parsed above,
                    # before any branching, and ride out on the done chunk.
                    if event.get("done"):
                        yield Chunk(
                            text=text,
                            thinking=thinking,
                            done=True,
                            tool_calls=calls,
                            prompt_tokens=event.get("prompt_eval_count"),
                            completion_tokens=event.get("eval_count"),
                        )
                        return
                    if text or thinking:
                        yield Chunk(text=text, thinking=thinking)

        except httpx.HTTPError as exc:
            raise ProviderError(f"ollama unreachable: {exc}", retryable=True) from exc

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        response = await self._client.post(
            "/api/embed", json={"model": self.embed_model, "input": list(texts)}
        )
        response.raise_for_status()
        return response.json()["embeddings"]

    async def health(self) -> bool:
        try:
            response = await self._client.get("/api/tags", timeout=httpx.Timeout(2.0))
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        await self._client.aclose()


def _encode(message: Message) -> dict:
    """One message in Ollama's shape.

    Images go in a sibling `images` array of base64 strings rather than inside
    the content -- that is the format /api/chat has always taken, and a model
    without the `vision` capability simply ignores the field.
    """
    encoded: dict = {"role": message.role, "content": message.content}
    if message.images:
        encoded["images"] = [image.b64() for image in message.images]
    # Both of these have to survive the round trip. Without them the second
    # pass replays a conversation in which the model asked for a skill and
    # nothing answered -- so it asks again, and again, until the round cap in
    # the orchestrator is the only thing that stops it.
    if message.tool_calls:
        encoded["tool_calls"] = [
            {"function": {"name": call.name, "arguments": call.arguments}}
            for call in message.tool_calls
        ]
    # Ollama pairs a result with its call by name; it never issued an id.
    if message.tool_name:
        encoded["tool_name"] = message.tool_name
    return encoded


def _parse_call(raw: dict, index: int) -> ToolCall:
    """One entry of Ollama's `tool_calls` array as the seam's shape.

    Ollama sends complete objects rather than deltas, and issues no id, so one
    is minted from the position in the array. That is enough: an id only has to
    pair a call with its result within a single turn.

    `arguments` is normally a decoded object, but a model under load will
    sometimes emit it as a JSON string. Normalising here means nothing above
    this module ever has to check which it got -- and an unparseable one
    becomes an empty dict rather than an exception, so the skill raises a clean
    TypeError about a missing argument and the model gets told what to fix.
    """
    function = raw.get("function") or {}
    arguments = function.get("arguments") or {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    return ToolCall(
        id=f"call_{index}",
        name=function.get("name", ""),
        arguments=arguments,
    )

    if message.tool_calls:
        encoded["tool_calls"] = [
            {"function": {"name": c.name, "arguments": c.arguments}}
            for c in message.tool_calls
        ]
    if message.tool_name:
        encoded["tool_name"] = message.tool_name


def _translate_error(status: int, body: str) -> ProviderError:
    lowered = body.lower()
    if "failed to load image" in lowered:
        # The runner could not decode an attachment. Almost always the format:
        # it reads PNG, JPEG, and GIF, and refuses WebP. Uploads are normalised
        # before storage, so reaching here means the file predates that.
        return ProviderError(
            "The local model couldn't read one of the attached images. "
            "It reads PNG, JPEG, and GIF; convert the file and attach it again."
        )
    if "context" in lowered and ("length" in lowered or "exceed" in lowered):
        return ContextOverflow(body)
    if "out of memory" in lowered or "cudamalloc" in lowered or "vram" in lowered:
        # Treated as overflow: the recovery is identical -- shrink and retry.
        return ContextOverflow(body)
    return ProviderError(f"ollama returned {status}: {body[:500]}", retryable=status >= 500)
