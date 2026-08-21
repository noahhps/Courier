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
        tools: Sequence[dict] | None = None,
    ) -> AsyncIterator[Chunk]:
        payload: dict = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "options": {"num_ctx": self.context_tokens},
        }
        if tools:
            payload["tools"] = list(tools)

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


def _translate_error(status: int, body: str) -> ProviderError:
    lowered = body.lower()
    if "context" in lowered and ("length" in lowered or "exceed" in lowered):
        return ContextOverflow(body)
    if "out of memory" in lowered or "cudamalloc" in lowered or "vram" in lowered:
        # Treated as overflow: the recovery is identical -- shrink and retry.
        return ContextOverflow(body)
    return ProviderError(f"ollama returned {status}: {body[:500]}", retryable=status >= 500)
