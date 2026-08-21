"""Ollama on 127.0.0.1. The default and, on a good day, the only provider."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence

import httpx

from .base import Chunk, ContextOverflow, Message, ProviderError

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
        think: bool | None = None,
    ) -> None:
        self.name = "ollama"
        self.model = model
        self.embed_model = embed_model
        self.context_tokens = context_tokens
        # None leaves it to the model. Reasoning models (qwen3, deepseek-r1)
        # otherwise spend thousands of tokens before emitting a single visible
        # character, which reads as a hang.
        self.think = think
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=_TIMEOUT)

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[dict] | None = None,
        think: bool | None = None,
    ) -> AsyncIterator[Chunk]:
        payload: dict = {
            "model": self.model,
            "messages": [_encode(m) for m in messages],
            "stream": True,
            "options": {"num_ctx": self.context_tokens},
        }
        if tools:
            payload["tools"] = list(tools)

        # The request decides; the environment is only the fallback for a
        # caller with no opinion.
        wanted = self.think if think is None else think

        # Thinking and vision do not mix. With the reasoning pass on, gemma4
        # answers image prompts with "no image was provided" -- the picture is
        # accepted, then lost before the model looks at it. Turning thinking
        # off for that one request is the difference between an answer and a
        # flat denial, so images overrule both of the above.
        if any(m.images for m in messages):
            wanted = False

        if wanted is not None:
            payload["think"] = wanted

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

                    # Reasoning models stream `thinking` before any content.
                    # Forward it tagged so the UI can show progress instead of
                    # a dead screen -- it is never part of the saved answer.
                    reasoning = message.get("thinking")
                    if reasoning:
                        yield Chunk(text=reasoning, meta={"kind": "thinking"})

                    if event.get("done"):
                        yield Chunk(
                            text=text,
                            done=True,
                            prompt_tokens=event.get("prompt_eval_count"),
                            completion_tokens=event.get("eval_count"),
                        )
                        return
                    if text:
                        yield Chunk(text=text)
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
    return encoded


def _translate_error(status: int, body: str) -> ProviderError:
    lowered = body.lower()
    if "failed to load image" in lowered:
        # The runner could not decode an attachment. Almost always the format:
        # it reads PNG, JPEG, and GIF, and refuses WebP. The client re-encodes
        # anything else before sending, so reaching here means the file came
        # from somewhere else -- or was stored before that conversion existed.
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
