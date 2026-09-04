"""OpenRouter: one key, several hundred models, one wire format.

The third provider, and the first that is neither "this machine" nor "one
vendor". It exists because the honest answer to "which cloud model should this
fall back to" is *the one you are paying for*, and that is not a decision this
project should make on someone's behalf.

Two ways in, both ending at the same place:

    an API key      pasted from openrouter.ai/keys
    a sign-in       the PKCE flow in openrouter_oauth.py, which mints a key

Either way what is held afterwards is a key on this machine, in `data/`,
beside the bearer token and the search key. There is no session to refresh and
nothing to revoke from here -- deleting the file is the whole of "log out".

The wire format is OpenAI's chat-completions, which OpenRouter speaks for every
model it fronts, including Anthropic's and Google's. That is the point of it:
one encoder here answers for all of them, where `anthropic.py` had to be
written against one vendor's block shapes. It is spoken with httpx rather than
the `openai` package because this file is 300 lines of it and a dependency that
exists to hide a JSON body is a dependency that hides a JSON body.

What it does *not* do is embeddings. Recall is deliberately local -- the whole
history of every conversation would otherwise leave the machine to be indexed
-- so `embed()` refuses here exactly as it does on the Anthropic path.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from .base import Chunk, ContextOverflow, Message, ProviderError, ToolCall

DEFAULT_URL = "https://openrouter.ai/api/v1"
# `openrouter/auto` picks a model per prompt. It is the default because it is
# the one choice that works before anybody has chosen: a fresh key answers with
# it, and the picker is then a refinement rather than a prerequisite.
DEFAULT_MODEL = "openrouter/auto"

# Same shape as the Ollama timeout and for the same reason: a long read, so a
# slow model is not cut off mid-answer, and a short connect so an unreachable
# service fails over quickly rather than hanging the turn.
_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0)

# The catalogue is ~300 entries and changes on the order of days, not seconds.
# Cached so opening the model picker twice in a row is one request, and short
# enough that a model added this morning is offered this afternoon.
_CATALOGUE_TTL_SECONDS = 600.0

_REASONING_EFFORTS = frozenset(("low", "medium", "high"))


class OpenRouterProvider:
    def __init__(
        self,
        api_key: str = "",
        model: str = DEFAULT_MODEL,
        *,
        base_url: str = DEFAULT_URL,
        max_tokens: int | None = None,
        referer: str = "https://github.com/noahhps/Courier",
        title: str = "Courier",
    ) -> None:
        self.name = "openrouter"
        self.model = model or DEFAULT_MODEL
        self.base_url = base_url.rstrip("/")
        self.api_key = (api_key or "").strip()
        self.max_tokens = max_tokens
        # Attribution headers. Optional, and worth sending: they are what puts
        # this app's name on the OpenRouter activity page, so a bill can be
        # read back as "Courier did this" rather than as an anonymous total.
        self._attribution = {"HTTP-Referer": referer, "X-Title": title}
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=_TIMEOUT)
        self._catalogue: list[dict] = []
        self._catalogue_at = 0.0

    # -- credentials ------------------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def set_api_key(self, key: str) -> None:
        """Swap the key in place, and drop anything it paid for.

        The catalogue is per-key in principle -- a key with BYOK providers
        enabled sees models an anonymous listing does not -- so a new key
        starts from an empty one rather than inheriting the last one's.
        """
        self.api_key = (key or "").strip()
        self._catalogue = []
        self._catalogue_at = 0.0

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ProviderError(
                "OpenRouter is not connected: add a key or sign in from Settings"
            )
        return {"Authorization": f"Bearer {self.api_key}", **self._attribution}

    # -- generation -------------------------------------------------------

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        think: Any = None,
        tools: Sequence[dict] | None = None,
    ) -> AsyncIterator[Chunk]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [_encode(m) for m in messages],
            "stream": True,
            # Without this the usage block never arrives and every turn is
            # stored with null token counts. It costs one extra field and is
            # the only way to know what a reply actually spent.
            "usage": {"include": True},
        }
        if self.max_tokens:
            payload["max_tokens"] = self.max_tokens
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]
        if reasoning := _reasoning(think):
            payload["reasoning"] = reasoning

        # Assembled across events, released with the done chunk -- the same
        # contract every provider here keeps. OpenAI's format fragments a call
        # across deltas (the name in one, the arguments a few characters at a
        # time in the rest) and indexes them, so the index is the identity
        # until the stream ends.
        pending: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] = {}
        finish_reason = ""
        # What actually answered. `openrouter/auto` and any model configured
        # with fallbacks can land somewhere other than what was asked for, and
        # a turn recorded as "auto" has lost which model wrote it.
        served = ""

        try:
            async with self._client.stream(
                "POST", "/chat/completions", json=payload, headers=self._headers()
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise _translate_error(response.status_code, body)

                async for line in response.aiter_lines():
                    # Keep-alives arrive as SSE comments (": OPENROUTER
                    # PROCESSING") on a long queue. Dropping them here is what
                    # keeps a slow start from looking like a protocol error.
                    if not line.strip() or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    # An error can arrive mid-stream, after a 200, when the
                    # upstream model rejects something OpenRouter accepted.
                    if error := event.get("error"):
                        raise _translate_error(
                            int(error.get("code") or 500), json.dumps(error)
                        )

                    if reported := event.get("usage"):
                        usage = reported
                    served = event.get("model") or served

                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    finish_reason = choice.get("finish_reason") or finish_reason
                    delta = choice.get("delta") or {}

                    for entry in delta.get("tool_calls") or ():
                        _merge_call(pending, entry)

                    text = delta.get("content") or ""
                    # OpenRouter normalises every family's reasoning onto this
                    # one field, which is the reason a single encoder can serve
                    # gpt-oss, Claude and Gemini without knowing which is which.
                    thinking = delta.get("reasoning") or ""
                    if text or thinking:
                        yield Chunk(text=text, thinking=thinking)

            if finish_reason == "content_filter":
                raise ProviderError("the model's provider filtered this reply")

            yield Chunk(
                done=True,
                tool_calls=_finish_calls(pending),
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                meta={
                    key: value
                    for key, value in (
                        ("served_model", served),
                        ("cost", usage.get("cost")),
                    )
                    if value
                },
            )
        except ProviderError:
            raise
        except httpx.HTTPError as exc:
            raise ProviderError(f"openrouter unreachable: {exc}", retryable=True) from exc

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise ProviderError("recall is indexed locally; OpenRouter does not embed")

    # -- what the picker needs -------------------------------------------

    async def health(self) -> bool:
        """Connected and answering, not merely holding a string.

        A key that has been revoked is indistinguishable from no key at all as
        far as the composer is concerned, so this asks rather than assumes --
        cheaply, against the endpoint that describes the key itself.
        """
        if not self.api_key:
            return False
        try:
            response = await self._client.get(
                "/key", headers=self._headers(), timeout=httpx.Timeout(6.0)
            )
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def account(self) -> dict:
        """Label, spend and limit for the key in hand.

        Shown on the settings page next to the connection: a cloud provider
        billed by the token should say what it has cost, and this is the only
        number OpenRouter will give without a second request per model.
        """
        response = await self._client.get("/key", headers=self._headers())
        if response.status_code >= 400:
            raise _translate_error(response.status_code, response.text)
        data = (response.json() or {}).get("data") or {}
        return {
            "label": data.get("label") or "",
            "usage": data.get("usage"),
            "limit": data.get("limit"),
            "free_tier": bool(data.get("is_free_tier")),
        }

    async def list_models(self, *, refresh: bool = False) -> list[dict]:
        """The catalogue, trimmed to what a picker can draw.

        Every field here is one the menu shows or filters on. The full listing
        is ten times this size -- per-provider routing, moderation flags,
        tokenizer names -- and none of it survives the trip to a phone in a
        form anybody reads.

        Unauthenticated when there is no key: the listing is public, and a
        picker that can show what connecting would buy is better than one that
        shows an empty list until it is connected.
        """
        now = time.monotonic()
        if self._catalogue and not refresh and now - self._catalogue_at < _CATALOGUE_TTL_SECONDS:
            return self._catalogue

        headers = self._headers() if self.api_key else dict(self._attribution)
        try:
            response = await self._client.get("/models", headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"openrouter unreachable: {exc}", retryable=True) from exc
        if response.status_code >= 400:
            raise _translate_error(response.status_code, response.text)

        models = [_describe(entry) for entry in (response.json() or {}).get("data") or ()]
        # `openrouter/auto` is a real model id on this API but is not returned
        # by the listing, so it is prepended rather than looked for -- without
        # it the default the provider ships with is missing from its own menu.
        models = [
            {
                "id": DEFAULT_MODEL,
                "name": "Auto",
                "description": "OpenRouter picks a model to suit the prompt",
                "context": None,
                "prompt_price": None,
                "completion_price": None,
                "vision": True,
                "tools": True,
                "reasoning": True,
                "free": False,
            },
            *sorted(models, key=lambda m: m["id"]),
        ]
        self._catalogue = models
        self._catalogue_at = now
        return models

    async def aclose(self) -> None:
        await self._client.aclose()


# -- encoding -------------------------------------------------------------


def _encode(message: Message) -> dict[str, Any]:
    """One message in OpenAI's shape, which OpenRouter speaks for every model.

    The three roles that carry more than text each need their own handling:
    a tool result names the call it answers by id, an assistant turn that asked
    for skills has to replay the asking, and a user turn with pictures becomes
    a list of content parts rather than a string.
    """
    if message.role == "tool":
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id or "call_0",
            "content": message.content,
        }

    if message.role == "assistant" and message.tool_calls:
        return {
            "role": "assistant",
            # Null rather than "" when the model said nothing on its way to
            # asking: some upstreams reject an empty string in this position.
            "content": message.content or None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        # A string here, not an object. This is the one place
                        # the format is deliberately lossy, and sending the
                        # dict instead is a 400 from half the upstreams.
                        "arguments": json.dumps(call.arguments or {}),
                    },
                }
                for call in message.tool_calls
            ],
        }

    if message.images:
        parts: list[dict[str, Any]] = [
            {
                "type": "image_url",
                # A data URL rather than a hosted one: these bytes live in the
                # user's own SQLite file and there is nowhere to upload them to
                # that would not be a second place they exist.
                "image_url": {"url": f"data:{image.mime};base64,{image.b64()}"},
            }
            for image in message.images
        ]
        if message.content:
            parts.append({"type": "text", "text": message.content})
        return {"role": message.role, "content": parts}

    return {"role": message.role, "content": message.content}


def _reasoning(think: Any) -> dict[str, Any] | None:
    """The three shapes a thinking control can send, as one request field.

    `thinking.py` describes a model's control as an effort word, a switch or a
    token budget, depending on the family. OpenRouter accepts all three under
    one key and converts between them upstream -- an effort word becomes a
    budget for Claude, a budget becomes an effort for gpt-oss -- so this is the
    one provider where the client's control never has to be translated.

    None leaves the field off entirely, which is not the same as switching
    reasoning off: a model that reasons by default should keep doing so when
    nobody has touched the control.
    """
    if think is None:
        return None
    if isinstance(think, bool):
        return {"enabled": True} if think else {"exclude": True, "effort": "low"}
    if isinstance(think, int):
        return {"max_tokens": max(1024, think)}
    level = str(think).strip().lower()
    if level in _REASONING_EFFORTS:
        return {"effort": level}
    return None


def _merge_call(pending: dict[int, dict[str, Any]], entry: dict) -> None:
    """Fold one tool-call delta into the call it belongs to.

    OpenAI's streaming format sends a call in pieces: the id and name once, the
    arguments as a run of fragments that only parse once concatenated. The
    `index` is what ties them together, and it is the only thing that does --
    two calls in one turn interleave their fragments.
    """
    index = int(entry.get("index") or 0)
    slot = pending.setdefault(index, {"id": "", "name": "", "arguments": ""})
    if entry.get("id"):
        slot["id"] = str(entry["id"])
    function = entry.get("function") or {}
    if function.get("name"):
        slot["name"] = str(function["name"])
    if function.get("arguments"):
        slot["arguments"] += str(function["arguments"])


def _finish_calls(pending: dict[int, dict[str, Any]]) -> tuple[ToolCall, ...]:
    """The accumulated calls, as the seam's shape.

    Arguments arrive as a JSON string and are decoded here so nothing above
    this module has to know that. An unparseable one becomes an empty dict for
    the reason `ollama.py` gives: the skill then raises a clean error about a
    missing argument, which the model is told and can fix, where an exception
    here would end the turn with nothing to say.
    """
    calls: list[ToolCall] = []
    for index in sorted(pending):
        slot = pending[index]
        if not slot["name"]:
            continue
        try:
            arguments = json.loads(slot["arguments"] or "{}")
        except json.JSONDecodeError:
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        calls.append(
            ToolCall(
                id=slot["id"] or f"call_{index}",
                name=slot["name"],
                arguments=arguments,
            )
        )
    return tuple(calls)


def _describe(entry: dict) -> dict:
    """One catalogue row, as the picker draws it."""
    architecture = entry.get("architecture") or {}
    pricing = entry.get("pricing") or {}
    parameters = set(entry.get("supported_parameters") or ())
    modalities = architecture.get("input_modalities") or []
    model_id = str(entry.get("id") or "")
    return {
        "id": model_id,
        "name": str(entry.get("name") or model_id),
        "description": (entry.get("description") or "")[:280],
        "context": entry.get("context_length"),
        # Dollars per token as OpenRouter states them -- strings, because they
        # are decimals with more precision than a float keeps. Formatting into
        # "per million" is the client's job; the wire keeps the source number.
        "prompt_price": pricing.get("prompt"),
        "completion_price": pricing.get("completion"),
        "vision": "image" in modalities,
        "tools": "tools" in parameters,
        "reasoning": "reasoning" in parameters or "include_reasoning" in parameters,
        # A model whose prompt and completion both cost nothing. Worth its own
        # flag: it is the filter someone reaches for first.
        "free": _is_free(pricing),
    }


def _is_free(pricing: dict) -> bool:
    try:
        return float(pricing.get("prompt") or 0) == 0 and float(
            pricing.get("completion") or 0
        ) == 0
    except (TypeError, ValueError):
        return False


def _translate_error(status: int, body: str) -> ProviderError:
    """An OpenRouter failure as something the caller can route around.

    The distinction that matters upstream is `retryable`: the router drops a
    provider that failed and the orchestrator retries a context overflow with a
    smaller window. Everything else is a sentence for the reader, so the
    message is written to be shown rather than parsed.
    """
    lowered = body.lower()
    if status == 401:
        return ProviderError(
            "OpenRouter rejected the key. Reconnect it from Settings."
        )
    if status == 402:
        return ProviderError(
            "This OpenRouter key has no credit left for that model."
        )
    if status == 403 and "moderation" in lowered:
        return ProviderError("the model's provider refused this prompt")
    if status == 429:
        return ProviderError("OpenRouter is rate limiting this key", retryable=True)
    if "context" in lowered and ("length" in lowered or "maximum" in lowered):
        return ContextOverflow(body)
    if status == 404 and "model" in lowered:
        return ProviderError(
            "That model is not available on OpenRouter any more. Pick another."
        )
    return ProviderError(f"openrouter returned {status}: {body[:500]}", retryable=status >= 500)
