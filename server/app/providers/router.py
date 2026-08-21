"""Local first, cloud only when local is unreachable.

Health is cached briefly so a burst of turns doesn't hammer Ollama's /api/tags,
but not so long that a restarted Ollama stays marked dead for minutes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ..config import Settings
from .anthropic import AnthropicProvider
from .base import ModelProvider
from .ollama import OllamaProvider

_HEALTH_TTL_SECONDS = 15.0


@dataclass
class Route:
    provider: ModelProvider
    reason: str  # "local" | "fallback" — surfaced in the UI banner


class ProviderRouter:
    def __init__(self, settings: Settings) -> None:
        self.local = OllamaProvider(
            settings.ollama_url,
            settings.ollama_model,
            context_tokens=settings.context_tokens,
        )
        self.cloud = AnthropicProvider()
        self._last_check = 0.0
        self._last_healthy = True

    async def resolve(self, prefer: str | None = None) -> Route:
        if prefer == "cloud":
            return Route(self.cloud, "fallback")
        if prefer == "local":
            return Route(self.local, "local")

        if await self._local_healthy():
            return Route(self.local, "local")
        if await self.cloud.health():
            return Route(self.cloud, "fallback")
        # Nothing is reachable. Hand back local anyway so the caller surfaces
        # the real connection error rather than an invented one.
        return Route(self.local, "local")

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
