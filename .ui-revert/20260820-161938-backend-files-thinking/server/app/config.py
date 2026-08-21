"""Configuration. Environment variables only -- there is one deployment.

Everything has a working default so `python -m app` runs with no setup at all.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    # --- transport -------------------------------------------------------
    # Used verbatim. Loopback by default, so nothing off this machine can
    # reach the API until this says otherwise; a LAN address (or 0.0.0.0)
    # opens it to that network, with the bearer token as the only thing in
    # front of it. Read the note on the token before widening this.
    bind_host: str = field(default_factory=lambda: _env("BIND_HOST", "127.0.0.1"))
    bind_port: int = field(default_factory=lambda: _env_int("BIND_PORT", 8080))

    # --- auth ------------------------------------------------------------
    # The perimeter. Generated and persisted on first run.
    auth_token: str = field(default_factory=lambda: _env("AUTH_TOKEN", ""))

    # --- storage ---------------------------------------------------------
    db_path: Path = field(
        default_factory=lambda: Path(_env("DB_PATH", str(REPO_ROOT / "data" / "chat.db")))
    )
    # The built client, not the source: `npm run build` in client/ produces it.
    client_dir: Path = field(
        default_factory=lambda: Path(_env("CLIENT_DIR", str(REPO_ROOT / "client" / "dist")))
    )

    # --- inference -------------------------------------------------------
    ollama_url: str = field(default_factory=lambda: _env("OLLAMA_URL", "http://127.0.0.1:11434"))
    ollama_model: str = field(default_factory=lambda: _env("OLLAMA_MODEL", "gemma4"))
    # "" leaves the model's own default. Set OLLAMA_THINK=false on a reasoning
    # model to trade depth for a reply that starts arriving immediately.
    ollama_think: bool | None = field(
        default_factory=lambda: {"true": True, "false": False}.get(
            _env("OLLAMA_THINK", "").lower()
        )
    )

    # Rough working-context budget in tokens. The window builder trims to fit;
    # real compaction (summarise the middle, keep head and tail) is phase 5.
    context_tokens: int = field(default_factory=lambda: _env_int("CONTEXT_TOKENS", 32768))
    # Headroom reserved for the reply so a full window can't crowd it out.
    reply_tokens: int = field(default_factory=lambda: _env_int("REPLY_TOKENS", 2048))

    system_preamble: str = field(
        default_factory=lambda: _env(
            "SYSTEM_PREAMBLE",
            "You are a personal assistant running on the user's own hardware. "
            "Be direct and concrete. Skip preamble and flattery. "
            "Use markdown when it aids scanning; otherwise plain prose. "
            "If you don't know something, say so rather than guessing."
            ,
        )
    )


def load_settings() -> Settings:
    settings = Settings()
    if not settings.auth_token:
        token = _load_or_create_token(settings.db_path.parent / "token")
        settings = Settings(**{**settings.__dict__, "auth_token": token})
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    return settings


# Deliberately excludes 0/O, 1/l/I -- this gets typed on a phone keyboard once
# per device, and a misread character is the whole experience.
_TOKEN_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def _load_or_create_token(path: Path) -> str:
    """Keep the bearer token on disk so it survives restarts.

    Regenerating it every boot would log the phone out roughly weekly, which is
    exactly the friction that gets a personal tool abandoned.

    14 characters from a 31-symbol alphabet is ~69 bits: short enough to type
    from a sticky note, far beyond guessing at any rate a network allows.

    It is the only thing standing in front of the API, so it carries more
    weight the further BIND_HOST reaches. On loopback it is a formality; on an
    address other machines can dial it is the whole perimeter, which is what
    TOKEN_LENGTH is there for.
    """
    if path.exists():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    length = _env_int("TOKEN_LENGTH", 14)
    token = "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(length))
    path.write_text(token, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # Windows; ACLs are the user's problem there
    return token
