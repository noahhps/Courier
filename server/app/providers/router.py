"""Local first, cloud only when local is unreachable.

Health is cached briefly so a burst of turns doesn't hammer Ollama's /api/tags,
but not so long that a restarted Ollama stays marked dead for minutes.

Three backends now rather than two, which makes the naming worth stating: the
wire vocabulary is `local`, `cloud` and `openrouter` -- what a caller asks for
-- while `provider.name` is `ollama`, `anthropic` and `openrouter`, which is
what answered. The first is the user's word and appears in the client; the
second is recorded on the message. `cloud` predates the third backend and stays
spelled that way because it is what the stored rows and the running client
already say.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ..config import Settings
from .anthropic import AnthropicProvider
from .base import ModelProvider
from .ollama import OllamaProvider
from .openrouter import OpenRouterProvider

_HEALTH_TTL_SECONDS = 15.0

LOCAL = "local"
CLOUD = "cloud"
OPENROUTER = "openrouter"

# The order auto walks when local is not answering. OpenRouter before Anthropic
# on purpose: it is only ever configured by someone doing so deliberately in
# this app, where ANTHROPIC_API_KEY is an environment variable that may be
# there for something else entirely.
FALLBACK_ORDER = (OPENROUTER, CLOUD)

# Where a picked model is kept, per backend, in `app_settings`. Namespaced like
# the theme and the memory switches beside it, because that table is shared.
MODEL_SETTING_PREFIX = "model."


def model_setting_key(provider_id: str) -> str:
    return f"{MODEL_SETTING_PREFIX}{provider_id}"


@dataclass
class Route:
    provider: ModelProvider
    # "local" | "fallback" -- surfaced in the UI banner. It says whether this
    # was a fallback, not which one: with two cloud backends, *what* answered
    # is `provider.name`, which is unique per backend and is what the message
    # is stored with.
    reason: str


class ProviderRouter:
    def __init__(self, settings: Settings) -> None:
        self.local = OllamaProvider(
            settings.ollama_url,
            settings.ollama_model,
            embed_model=settings.embed_model,
            context_tokens=settings.context_tokens,
        )
        self.cloud = AnthropicProvider()
        self.openrouter = OpenRouterProvider(
            settings.openrouter_api_key,
            settings.openrouter_model,
            base_url=settings.openrouter_url,
        )
        self.by_id: dict[str, ModelProvider] = {
            LOCAL: self.local,
            CLOUD: self.cloud,
            OPENROUTER: self.openrouter,
        }
        self._last_check = 0.0
        self._last_healthy = True

    def set_model(self, provider_id: str, model: str) -> str:
        """Point one backend at a different model.

        Mutating the live provider rather than building a new one: an httpx
        client, a cached catalogue and a cached health probe all hang off these
        objects, and a model change is a change of one string, not of the
        connection underneath it. A turn already streaming keeps the model it
        started with, because the request went out with it.
        """
        provider = self.by_id.get(provider_id)
        if provider is None:
            raise KeyError(provider_id)
        model = (model or "").strip()
        if not model:
            raise ValueError("a model name is required")
        provider.model = model
        return model

    async def resolve(self, prefer: str | None = None) -> Route:
        if prefer in self.by_id:
            return Route(self.by_id[prefer], LOCAL if prefer == LOCAL else "fallback")

        if await self._local_healthy():
            return Route(self.local, LOCAL)
        for provider_id in FALLBACK_ORDER:
            provider = self.by_id[provider_id]
            if await provider.health():
                return Route(provider, "fallback")
        # Nothing is reachable. Hand back local anyway so the caller surfaces
        # the real connection error rather than an invented one.
        return Route(self.local, LOCAL)

    async def _local_healthy(self) -> bool:
        now = time.monotonic()
        if now - self._last_check < _HEALTH_TTL_SECONDS:
            return self._last_healthy
        self._last_healthy = await self.local.health()
        self._last_check = now
        return self._last_healthy

    def invalidate_health(self) -> None:
        """Force the next resolve() to re-probe -- call this after a local failure."""
        self._last_check = 0.0

    async def aclose(self) -> None:
        await self.local.aclose()
        await self.openrouter.aclose()
