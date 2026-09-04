"""Signing in to OpenRouter, for people who would rather not handle a key.

Pasting an API key works and is one fewer moving part, so it stays. This is
here because the first thing a new key asks you to do is create it, name it,
copy it and keep it somewhere -- four steps in a web console, on a phone,
before the app does anything at all. The sign-in does the same thing in one
tap and hands back a key scoped to this app.

The flow is OAuth PKCE, which is the shape used by clients that cannot keep a
secret. There is no client secret here and there could not be: this server is
installed on someone's own machine, so anything baked into it is baked into
every copy of it.

    1. begin()      mints a verifier, keeps it, and returns the URL to open
    2. the browser  signs in at openrouter.ai and is redirected back with a code
    3. complete()   trades code + verifier for a key, which is then stored

The verifier never leaves this process until step 3, and the challenge that
does travel is a hash of it -- so intercepting the redirect is not enough to
mint a key, which is the whole point of the exchange.

State lives in memory and dies with the process. That is not a compromise: a
half-finished sign-in is worth nothing after a restart, and the one thing worth
keeping -- the key -- is written to disk by the caller the moment it arrives.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from .base import ProviderError
from .openrouter import DEFAULT_URL

AUTH_URL = "https://openrouter.ai/auth"

# Long enough to find the password manager, short enough that an abandoned
# attempt is not still exchangeable an hour later.
FLOW_TTL_SECONDS = 900.0

# One person, one browser, one sign-in at a time. The cap exists so a bug in a
# client that starts a flow per render cannot grow this dict without bound.
MAX_FLOWS = 8

_EXCHANGE_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)


@dataclass
class Flow:
    """One sign-in attempt, from the tap to the key.

    `state` is the handle: it names the flow in the callback URL and is what
    the client polls on. It is a 256-bit random string because it is the only
    thing standing between the callback route -- which cannot be authenticated,
    since OpenRouter redirects a browser to it -- and someone else's code being
    exchanged against this server.
    """

    state: str
    verifier: str
    callback_url: str
    created_at: float
    status: str = "pending"  # pending | connected | failed
    error: str = ""
    # Set once, on success. Held only until the caller has written it down.
    key: str = ""

    def expired(self, *, now: float | None = None) -> bool:
        return (now or time.monotonic()) - self.created_at > FLOW_TTL_SECONDS

    def to_dict(self) -> dict[str, Any]:
        # Deliberately without the key or the verifier. This is what the
        # polling client sees, and neither belongs in a browser.
        return {"state": self.state, "status": self.status, "error": self.error}


@dataclass
class OAuthFlows:
    """The sign-ins in flight. One instance per server."""

    flows: dict[str, Flow] = field(default_factory=dict)
    auth_url: str = AUTH_URL
    api_url: str = DEFAULT_URL

    def begin(self, callback_base: str) -> Flow:
        """Start a flow and return it; the caller sends the reader to `url`.

        `callback_base` is the origin the browser reached this server on, not
        the address the server is bound to. They differ in every case that
        matters -- a phone on the LAN, a reverse proxy, a laptop that changed
        network -- and the browser is the one being redirected, so its own view
        of where this server lives is the only one that can be right.
        """
        base = _normalise_base(callback_base)
        state = secrets.token_urlsafe(32)
        # 96 bytes of randomness, base64url'd: comfortably inside the 43-128
        # character range the spec allows for a verifier.
        verifier = secrets.token_urlsafe(72)
        flow = Flow(
            state=state,
            verifier=verifier,
            callback_url=f"{base}/openrouter/callback/{state}",
            created_at=time.monotonic(),
        )
        self.flows[state] = flow
        # Swept after the insert rather than before it, so the cap is a cap:
        # sweeping first leaves room for one more and the table settles at
        # MAX_FLOWS + 1. The flow just added is the newest, so it is never the
        # one the cap drops.
        self._sweep()
        return flow

    def url_for(self, flow: Flow) -> str:
        """Where to send the browser.

        The state rides in the callback *path* rather than in a query
        parameter. OpenRouter appends its own `code` to whatever URL it is
        given, and a path segment cannot be dropped, reordered or collide with
        that -- which is worth more here than the convention of using ?state=.
        """
        return (
            f"{self.auth_url}?callback_url={quote(flow.callback_url, safe='')}"
            f"&code_challenge={_challenge(flow.verifier)}"
            f"&code_challenge_method=S256"
        )

    def get(self, state: str) -> Flow | None:
        self._sweep()
        return self.flows.get(state)

    async def complete(self, state: str, code: str) -> str:
        """Trade the code for a key, or raise saying why not.

        Single use, but not by deletion: the client is polling this state to
        learn how the sign-in went, and a flow that vanished the moment it
        succeeded would report "expired" for the one outcome worth reporting.
        So the verifier is spent instead -- taken out of the flow before the
        request goes out, which is what makes a reloaded callback tab fail
        cleanly rather than mint a second key against the same code.
        """
        self._sweep()
        flow = self.flows.get(state)
        if flow is None:
            raise ProviderError("that sign-in has expired -- start it again")
        if not code.strip():
            raise ProviderError("OpenRouter sent no code back")
        if not flow.verifier:
            raise ProviderError("that sign-in has already been used")

        verifier, flow.verifier = flow.verifier, ""

        try:
            async with httpx.AsyncClient(timeout=_EXCHANGE_TIMEOUT) as client:
                response = await client.post(
                    f"{self.api_url}/auth/keys",
                    json={
                        "code": code.strip(),
                        "code_verifier": verifier,
                        "code_challenge_method": "S256",
                    },
                )
        except httpx.HTTPError as exc:
            flow.status, flow.error = "failed", f"could not reach OpenRouter: {exc}"
            raise ProviderError(flow.error, retryable=True) from exc

        if response.status_code >= 400:
            flow.status = "failed"
            flow.error = _readable(response)
            raise ProviderError(flow.error)

        key = str((response.json() or {}).get("key") or "").strip()
        if not key:
            flow.status, flow.error = "failed", "OpenRouter returned no key"
            raise ProviderError(flow.error)

        flow.status, flow.key = "connected", key
        return key

    def _sweep(self) -> None:
        """Drop what has expired, and the oldest if there are somehow too many.

        Called on every entry point rather than on a timer: this is a dict with
        single digits in it, and a background task to prune it would be more
        machinery than the thing it prunes.
        """
        now = time.monotonic()
        for state, flow in list(self.flows.items()):
            if flow.expired(now=now):
                del self.flows[state]
        while len(self.flows) > MAX_FLOWS:
            oldest = min(self.flows.values(), key=lambda f: f.created_at)
            del self.flows[oldest.state]


def _challenge(verifier: str) -> str:
    """S256: base64url of the SHA-256 of the verifier, unpadded.

    The padding has to go. `=` is legal in a query parameter but not in the
    challenge itself, and servers that compare the string rather than the bytes
    reject it -- which surfaces as an invalid_grant at the exchange, two steps
    later, with nothing pointing back here.
    """
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _normalise_base(base: str) -> str:
    """The origin to send the browser back to, or a refusal.

    Only http and https: OpenRouter redirects a browser, and the desktop
    shell's own `tauri://localhost` is not an address that browser can be sent
    to. The caller is expected to hand this the address the server is bound to
    in that case, which is why this refuses rather than guessing.
    """
    cleaned = (base or "").strip().rstrip("/")
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ProviderError(f"{base!r} is not an address a browser can return to")
    return f"{parsed.scheme}://{parsed.netloc}"


def _readable(response: httpx.Response) -> str:
    """OpenRouter's refusal as a sentence, whatever shape it arrived in."""
    try:
        body = response.json()
    except ValueError:
        return f"OpenRouter refused the sign-in ({response.status_code})"
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    if isinstance(error, str) and error:
        return error
    return f"OpenRouter refused the sign-in ({response.status_code})"
