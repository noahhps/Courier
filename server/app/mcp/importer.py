"""Reading the `mcpServers` config block that every MCP install page hands out.

The format is a de facto standard rather than a specified one -- it grew out of
Claude Desktop's config file and everything else copied it -- so this is
deliberately forgiving about shape and strict about content. It accepts the
whole file, the bare mapping, and the several spellings of "this one is over
HTTP" that different tools emit, then produces something Courier's own store
can take.

What it will not do is invent a transport. A server that names neither a
command nor a URL is rejected by name, because the alternative is a row in the
database that can never connect and a reader wondering why.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

# Same rule the API applies to a hand-typed name, so an import cannot create a
# server the edit form would refuse to save.
_NAME = re.compile(r"^[\w .-]{1,100}$")

# What various tools write to mean "not stdio". Courier stores "sse" only for
# the 2024-11-05 shape; everything else is Streamable HTTP.
_HTTP_KINDS = {"http", "streamable-http", "streamablehttp", "streamable_http"}
_SSE_KINDS = {"sse"}


class ImportError_(Exception):
    """The config could not be read at all. Per-server problems are reported, not raised."""


@dataclass
class ImportedServer:
    name: str
    transport: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    description: str | None = None
    homepage: str | None = None


def _as_str_list(value: Any) -> list[str]:
    """Args as a list, or as the single command line people sometimes paste."""
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value.strip():
        # shlex, not split(): an argument containing a quoted path with a space
        # is common enough that splitting on whitespace would corrupt it.
        try:
            return shlex.split(value)
        except ValueError:
            return value.split()
    return []


def _as_str_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items() if v is not None}


def _transport_for(entry: dict[str, Any]) -> str | None:
    """stdio, sse or http -- from what the entry declares, else from what it has."""
    declared = str(entry.get("type") or entry.get("transport") or "").strip().lower()
    if declared == "stdio":
        return "stdio"
    if declared in _SSE_KINDS:
        return "sse"
    if declared in _HTTP_KINDS:
        return "http"

    # Undeclared, which is the common case: infer it. A command means a
    # subprocess; a URL means HTTP, and only a /sse path means the old shape.
    if entry.get("command"):
        return "stdio"
    url = entry.get("url") or entry.get("endpoint")
    if url:
        path = urlparse(str(url)).path.rstrip("/")
        return "sse" if path.endswith("/sse") else "http"
    return None


def _homepage_for(entry: dict[str, Any], url: str | None) -> str | None:
    """Where this server's logo might live: what it says, else its own host."""
    stated = entry.get("homepage") or entry.get("website")
    if isinstance(stated, str) and stated.strip():
        return stated.strip()[:253]
    if url:
        host = urlparse(url).hostname
        if host and "." in host:
            return host
    return None


def parse_mcp_config(config: Any) -> list[ImportedServer]:
    """Turn a pasted config into server specs. Raises only if the whole thing is unreadable."""
    if not isinstance(config, dict):
        raise ImportError_("expected a JSON object")

    # Either the whole file (`{"mcpServers": {...}}`) or the mapping itself.
    # Presence of the key decides, not truthiness: an explicitly empty block is
    # an empty config and should say so, rather than falling through and being
    # read as a mapping with one server called "mcpServers".
    if "mcpServers" in config:
        servers = config["mcpServers"]
    elif "servers" in config:
        servers = config["servers"]
    else:
        servers = config
    if not isinstance(servers, dict) or not servers:
        raise ImportError_(
            'no "mcpServers" block found -- paste the whole config, or just the '
            "object mapping server names to their settings"
        )

    parsed: list[ImportedServer] = []
    for raw_name, entry in servers.items():
        name = str(raw_name).strip()
        if not _NAME.match(name) or not isinstance(entry, dict):
            continue
        if entry.get("disabled") is True:
            continue

        transport = _transport_for(entry)
        if transport is None:
            continue  # names neither a command nor a URL; nothing to connect to

        url = entry.get("url") or entry.get("endpoint")
        url = str(url).strip() if url else None
        command = entry.get("command")

        parsed.append(
            ImportedServer(
                name=name,
                transport=transport,
                command=str(command).strip() if command else None,
                args=_as_str_list(entry.get("args")),
                env=_as_str_map(entry.get("env")),
                cwd=str(entry["cwd"]).strip() if entry.get("cwd") else None,
                url=url,
                headers=_as_str_map(entry.get("headers")),
                description=(
                    str(entry["description"])[:1000] if entry.get("description") else None
                ),
                homepage=_homepage_for(entry, url),
            )
        )
    return parsed
