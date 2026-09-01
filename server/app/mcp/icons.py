"""Finding the logo for an MCP server, from the service's own website.

An MCP server is a brand more than it is a subprocess -- "the GitHub one", "the
Figma one" -- and a row of identical generic glyphs makes a list of them hard to
read. So the icon comes from the service's own site: its `<link rel="icon">`, or
failing that /favicon.ico.

Three things this is careful about, because it is the only part of Courier that
fetches a URL a reader typed:

* **It only ever talks to public hosts.** A server on 127.0.0.1 or 192.168.x.x
  has no brand site to fetch, so refusing those costs nothing and closes the
  door on using this as a probe for what else is on the network.
* **It is bounded.** One redirect chain, a few seconds, a few hundred KB. A
  page that streams forever cannot hold a connection open forever.
* **It verifies what came back is an image**, by its bytes rather than by the
  Content-Type it claims.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

# Generous for an icon and small enough that a hostile response cannot fill the
# database: the largest favicon anyone ships is well under this.
MAX_ICON_BYTES = 256 * 1024
FETCH_TIMEOUT = 6.0

# `rel` can carry several tokens ("shortcut icon", "apple-touch-icon"), so this
# looks for the word rather than matching the attribute exactly. Deliberately a
# regex and not an HTML parser: the target is one tag in the first few KB of a
# document, and a parser is a dependency plus a new class of parse failures.
_LINK_TAG = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_REL = re.compile(r"""\brel\s*=\s*["']?([^"'>]+)""", re.IGNORECASE)
_HREF = re.compile(r"""\bhref\s*=\s*["']([^"']+)""", re.IGNORECASE)
_SIZES = re.compile(r"(\d+)x(\d+)")

# Magic bytes, because a server that mislabels its favicon as text/html should
# not be able to put arbitrary bytes in an <img> the reader will render.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"\x00\x00\x01\x00", "image/x-icon"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"RIFF", "image/webp"),  # confirmed against the WEBP tag below
)


@dataclass(frozen=True)
class FetchedIcon:
    mime: str
    data: bytes
    source: str


def sniff(data: bytes) -> str | None:
    """The image type these bytes actually are, or None if they are not an image."""
    if not data:
        return None
    for signature, mime in _SIGNATURES:
        if data.startswith(signature):
            if mime == "image/webp" and data[8:12] != b"WEBP":
                continue
            return mime
    # SVG is text, so it has no signature worth trusting; look for the tag in
    # the opening bytes instead, past any XML declaration or comment.
    head = data[:512].lstrip()
    if head.startswith(b"<?xml") or head.startswith(b"<svg") or b"<svg" in data[:512]:
        return "image/svg+xml"
    return None


def is_public_host(host: str) -> bool:
    """Whether a hostname resolves somewhere on the public internet.

    Every address the name resolves to has to be public, not merely the first:
    a name that answers with one public address and one loopback address is the
    shape of a deliberate bypass, not an accident.
    """
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    if not infos:
        return False
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            return False
    return True


def domain_for(*, homepage: str | None = None, url: str | None = None) -> str | None:
    """Which host to ask for a logo: the declared homepage, else the endpoint's.

    Returns None for a stdio server that named no homepage -- an npx package has
    no website this can guess at -- and for anything not on the public internet,
    which includes every ``127.0.0.1`` MCP endpoint.
    """
    candidate = (homepage or "").strip()
    if not candidate and url:
        candidate = url.strip()
    if not candidate:
        return None

    if "//" not in candidate:
        candidate = f"https://{candidate}"
    host = (urlparse(candidate).hostname or "").strip().lower().rstrip(".")
    if not host or "." not in host:
        return None
    return host if is_public_host(host) else None


def _pick_link(html: str, base: str) -> list[str]:
    """Icon URLs named by the page, best first.

    Bigger is better, because these get displayed at 2x on a phone and an
    upscaled 16px favicon looks like a mistake. A declared icon with no size
    sorts above the implicit /favicon.ico and below anything that said it is
    large.
    """
    found: list[tuple[int, str]] = []
    for tag in _LINK_TAG.findall(html):
        rel = _REL.search(tag)
        href = _HREF.search(tag)
        if not rel or not href:
            continue
        tokens = rel.group(1).lower().split()
        if not any(token in ("icon", "shortcut", "apple-touch-icon") for token in tokens):
            continue
        size = 0
        sizes = _SIZES.search(tag)
        if sizes:
            size = int(sizes.group(1))
        elif "apple-touch-icon" in tokens:
            size = 180  # the size Apple mandates, whether or not it is declared
        found.append((size, urljoin(base, href.group(1))))

    found.sort(key=lambda pair: pair[0], reverse=True)
    seen: set[str] = set()
    ordered = []
    for _, href in found:
        if href not in seen:
            seen.add(href)
            ordered.append(href)
    return ordered


async def fetch_icon(domain: str) -> FetchedIcon | None:
    """The best icon `domain` offers, or None. Never raises."""
    if not is_public_host(domain):
        return None

    base = f"https://{domain}/"
    candidates: list[str] = []
    try:
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
            max_redirects=3,
            headers={"User-Agent": "Courier/0.1 (MCP icon fetcher)"},
        ) as client:
            try:
                page = await client.get(base)
                if page.status_code < 400:
                    # Only the head of the document: the <link> tags live there,
                    # and a page that never ends should not be read to the end.
                    candidates = _pick_link(page.text[:100_000], str(page.url))
            except httpx.HTTPError:
                pass  # a site with no reachable homepage may still have a favicon

            candidates.append(urljoin(base, "/favicon.ico"))

            for href in candidates[:5]:
                if not is_public_host(urlparse(href).hostname or ""):
                    continue  # a redirect or a <link> pointing back inside the network
                try:
                    response = await client.get(href)
                except httpx.HTTPError:
                    continue
                if response.status_code >= 400:
                    continue
                data = response.content[: MAX_ICON_BYTES + 1]
                if not data or len(data) > MAX_ICON_BYTES:
                    continue
                mime = sniff(data)
                if mime:
                    return FetchedIcon(mime=mime, data=data, source=href)
    except Exception:
        # An icon is decoration. Nothing here is worth failing a sync over.
        return None
    return None
