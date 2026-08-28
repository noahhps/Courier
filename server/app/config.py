"""Configuration. Environment variables only -- there is one deployment.

Everything has a working default so `python -m app` runs with no setup at all.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

REPO_ROOT = Path(__file__).resolve().parents[2]

ThinkingLevel = Literal["low", "medium", "high"]
_THINKING_LEVELS = frozenset(("low", "medium", "high"))


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)

def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


def _env_thinking_level(name: str, default: ThinkingLevel) -> ThinkingLevel:
    """Read the reasoning effort understood by gpt-oss via Ollama."""
    value = _env(name, default).lower()
    if value not in _THINKING_LEVELS:
        choices = ", ".join(sorted(_THINKING_LEVELS))
        raise ValueError(f"{name} must be one of: {choices}")
    return cast(ThinkingLevel, value)


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
    ollama_model: str = field(default_factory=lambda: _env("OLLAMA_MODEL", "gpt-oss"))
    # gpt-oss uses a three-level reasoning effort, not a boolean. This is the
    # fallback when an API caller does not choose a level of its own.
    ollama_think: ThinkingLevel = field(
        default_factory=lambda: _env_thinking_level("OLLAMA_THINK", "medium")
    )

    # Documents the assistant writes. Beside the database rather than inside
    # it: these are artifacts a conversation produced, not data the user gave
    # it, and they are regenerable.
    documents_dir: Path = field(
        default_factory=lambda: Path(
            _env("DOCUMENTS_DIR", str(REPO_ROOT / "data" / "documents"))
        )
    )

    # --- memory ----------------------------------------------------------
    # The encoder behind recall. Pulled separately from the chat model
    # (`ollama pull nomic-embed-text`); if it is missing, chunks stay
    # unembedded and search falls back to keywords alone.
    #
    # Changing this does not migrate anything. Vectors from different encoders
    # are not comparable, so `chunks.model` records what made each row and
    # search filters on it -- an old row simply stops being found until it is
    # re-embedded.
    embed_model: str = field(default_factory=lambda: _env("EMBED_MODEL", "nomic-embed-text"))

    # How close a chunk must be to count as a match at all, as cosine
    # similarity between normalised vectors.
    #
    # Encoder-dependent, and worth checking once against your own: ask
    # something the history definitely does not cover and print the scores. If
    # anything comes back, this is too low. Too high is the safe direction --
    # recall falls back to keywords rather than inventing relevance.
    memory_min_similarity: float = field(
        default_factory=lambda: _env_float("MEMORY_MIN_SIMILARITY", 0.35)
    )

    # How many curated facts may ride in the system prompt. They are prepended
    # to every request, on the same budget the conversation is competing for,
    # so this is a cap on how much memory is allowed to cost per turn.
    memory_max_facts: int = field(default_factory=lambda: _env_int("MEMORY_MAX_FACTS", 40))
    # And how long any one of them may be. A fact is a sentence; anything
    # longer is a note that belongs in the history, where recall can find it.
    memory_fact_chars: int = field(default_factory=lambda: _env_int("MEMORY_FACT_CHARS", 200))

    # Turns between curation passes. The pass is a whole extra generation, so
    # running it every turn doubles the work the GPU does for something nobody
    # is waiting on. 0 switches it off entirely.
    memory_extract_every: int = field(
        default_factory=lambda: _env_int("MEMORY_EXTRACT_EVERY", 5)
    )

    # --- web search ------------------------------------------------------
    # The one thing here that leaves the machine. Empty by default, and the
    # skill is not registered at all without it -- a capability the model is
    # told about but cannot use is worse than one it never hears of.
    #
    # A Brave Search key (free tier, no card) unless SEARCH_ENDPOINT points
    # somewhere else that answers in the same shape.
    search_api_key: str = field(default_factory=lambda: _env("SEARCH_API_KEY", ""))
    # Where a key pasted into the Skills page is kept. Beside the bearer token,
    # for the same reason: it is a secret that has to survive a restart, and
    # `data/` is already the directory nothing commits.
    search_key_path: Path = field(
        default_factory=lambda: Path(
            _env("SEARCH_KEY_PATH", str(REPO_ROOT / "data" / "search_key"))
        )
    )
    search_endpoint: str = field(default_factory=lambda: _env("SEARCH_ENDPOINT", ""))

    # Rough working-context budget in tokens. The window builder trims to fit;
    # real compaction (summarise the middle, keep head and tail) is phase 5.
    context_tokens: int = field(default_factory=lambda: _env_int("CONTEXT_TOKENS", 32768))
    # Headroom reserved for the reply so a full window can't crowd it out.
    reply_tokens: int = field(default_factory=lambda: _env_int("REPLY_TOKENS", 2048))

    system_preamble: str = field(
        default_factory=lambda: _env(
            "SYSTEM_PREAMBLE",
            # Kept deliberately short: this is the cacheable prefix and it is
            # prepended to every request, so each sentence is paid for on every
            # turn of every conversation forever.
            #
            # It says nothing about which skills exist. That list reaches the
            # model as the `tools` array, which Ollama renders into the prompt
            # using the format the model was trained on -- naming them here as
            # well would duplicate it, cost tokens twice, and go stale the
            # moment a skill is added or switched off. It once said "you have
            # access to a clock tool" while no tools were being sent, and the
            # model spent every turn hunting for a tool it could not see.
            "You are Marco, a personal assistant running on the user's own "
            "hardware. "
            # Scoped to conversation on purpose. This preamble is prepended to
            # every request, so an unqualified "be brief" was also in force
            # while the model wrote documents -- which is how a report came
            # back as four one-line bullets.
            "In conversation, be direct and concrete: skip preamble and "
            "flattery. Do not apologise excessively -- acknowledge a mistake "
            "briefly and correct it. "
            "A document, deck or spreadsheet is the opposite case. There, "
            "brevity is not a virtue: write the content out in full, because a "
            "heading with one line under it reads as unfinished. "
            "Match the user's tone, language, depth and formality. Use markdown "
            "when it aids scanning; otherwise plain prose. "
            "Say when you are unsure, and say what would settle it. Never "
            "invent a fact to fill a gap. If the user is wrong, say so plainly "
            "and explain why. "
            "Use a skill when it would answer better than your own recollection "
            "-- anything about the present moment, or about the user's own "
            "files and history, is worth looking up rather than guessing. "
            "Report what it told you; do not narrate the machinery unless "
            "asked. If a skill fails, say what failed rather than answering as "
            "though it had worked. "
            "Write dates as DD-MM-YYYY, and say plainly when you do not know "
            "the current date rather than guessing at it.",
        )
    )


def read_search_key(path: Path) -> str:
    """The saved key, or empty. Never raises -- a missing file is the normal
    state before anyone has pasted one in."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_search_key(path: Path, key: str) -> None:
    """Persist or clear the key. Empty clears the file rather than writing a
    blank one, so the state on disk matches the state in memory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    key = (key or "").strip()
    if not key:
        path.unlink(missing_ok=True)
        return
    path.write_text(key, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # Windows; ACLs are the user's problem there


def load_settings() -> Settings:
    settings = Settings()
    if not settings.auth_token:
        token = _load_or_create_token(settings.db_path.parent / "token")
        settings = Settings(**{**settings.__dict__, "auth_token": token})
        
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    # The environment wins: someone who exported SEARCH_API_KEY meant it, and
    # a stale file should not quietly override this run.
    if not settings.search_api_key:
        saved = read_search_key(settings.search_key_path)
        if saved:
            settings = Settings(**{**settings.__dict__, "search_api_key": saved})
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
