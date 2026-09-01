"""MCP Manager: server lifecycle, tool discovery, and JSON-RPC dispatch."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .protocol import (
    MCPError,
    MCPProtocolError,
    MCPTimeoutError,
    MCPToolInfo,
)
from .transports import (
    PREFERRED_PROTOCOL_VERSION,
    BaseMCPTransport,
    HttpSseTransport,
    StdioTransport,
)
from ..skills.mcp_skill import MCPSkill, sanitize_tool_name

if TYPE_CHECKING:
    from ..skills.registry import Registry
    from ..store import Store, StoredMCPServer

# Transports a stored server may name. "websocket" used to be accepted by the
# API and then quietly handed to the HTTP transport, which cannot speak ws://
# and failed with a confusing network error instead of an honest one.
SUPPORTED_TRANSPORTS = ("stdio", "sse", "http", "streamable-http")


class MCPManager:
    """Manages active MCP connections and registers their discovered tools."""

    def __init__(self, store: Store, registry: Registry) -> None:
        self.store = store
        self.registry = registry
        self._transports: dict[str, BaseMCPTransport] = {}
        self._server_skills: dict[str, list[str]] = {}
        self._errors: dict[str, str] = {}

    def is_server_connected(self, server_name: str) -> bool:
        transport = self._transports.get(server_name)
        return transport is not None and transport.is_connected

    def tools_for(self, server_name: str) -> list[str]:
        """Skill names this server currently contributes to the registry."""
        return list(self._server_skills.get(server_name, []))

    def last_error(self, server_name: str) -> str | None:
        """Why this server last failed to sync, if it did. Cleared on success."""
        return self._errors.get(server_name)

    async def sync_all(self) -> dict[str, Any]:
        """Synchronize all enabled MCP servers stored in SQLite."""
        results: dict[str, Any] = {"synced": 0, "failed": 0, "errors": {}}
        enabled_servers = self.store.list_mcp_servers(enabled_only=True)
        enabled_names = {s.name for s in enabled_servers}

        # Disconnect any servers that were disabled or deleted
        for active_name in list(self._transports.keys()):
            if active_name not in enabled_names:
                await self.disconnect_server(active_name)

        # Connect and register tools for each enabled server
        for server in enabled_servers:
            try:
                await self.sync_server(server)
                results["synced"] += 1
            except Exception as exc:
                results["failed"] += 1
                results["errors"][server.name] = str(exc)

        return results

    async def sync_server(self, server: StoredMCPServer) -> list[str]:
        """Connect to an MCP server, query its tools, and register them as Skills."""
        # Clean up existing registration if re-syncing
        await self.disconnect_server(server.name)

        if not server.enabled:
            return []

        transport = self._create_transport(server)
        try:
            registered = await self._handshake_and_register(server, transport)
        except Exception as exc:
            # Nothing is stored on the failure path, so nothing would ever close
            # this transport again: without it, every failed sync of an stdio
            # server leaks the npx process it just spawned.
            try:
                await transport.close()
            except Exception:
                pass
            self._errors[server.name] = str(exc)
            raise

        self._transports[server.name] = transport
        self._server_skills[server.name] = registered
        self._errors.pop(server.name, None)
        return registered

    async def _handshake_and_register(
        self, server: StoredMCPServer, transport: BaseMCPTransport
    ) -> list[str]:
        await transport.connect()

        # Step 1: initialize handshake. The server answers with the version it
        # chose; the HTTP transport also latches the session id off this
        # exchange, so it has to happen before anything else is sent.
        init_params = {
            "protocolVersion": PREFERRED_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "Courier", "version": "0.1.0"},
        }
        init_res = await transport.send_request("initialize", init_params, timeout=20.0)
        await transport.send_notification("notifications/initialized")

        # Step 2: discover tools. A server that advertises no tools capability
        # is a resources- or prompts-only server; it answers tools/list with
        # "method not found", which is not a reason to call the whole sync a
        # failure and tear the connection down.
        capabilities = init_res.get("capabilities", {}) if isinstance(init_res, dict) else {}
        if isinstance(capabilities, dict) and capabilities and "tools" not in capabilities:
            return []

        try:
            tools_res = await transport.send_request("tools/list", {}, timeout=20.0)
        except MCPProtocolError as exc:
            if exc.code == -32601:  # method not found
                return []
            raise

        raw_tools = tools_res.get("tools", []) if isinstance(tools_res, dict) else []

        registered_skill_names: list[str] = []
        for raw in raw_tools:
            if not isinstance(raw, dict) or not raw.get("name"):
                continue

            tool_info = MCPToolInfo(
                name=raw["name"],
                description=raw.get("description", ""),
                input_schema=raw.get("inputSchema") or {"type": "object", "properties": {}},
            )

            skill = MCPSkill(
                server_name=server.name,
                tool_info=tool_info,
                manager=self,
                name_override=self._available_name(server.name, tool_info.name),
            )
            # register() reports a collision rather than raising, and the old
            # code ignored the answer: the name went into _server_skills anyway,
            # so disconnecting this server later deleted whichever *other*
            # server's skill actually owned it.
            if self.registry.get(skill.name) is not None:
                continue
            self.registry.register(skill)
            registered_skill_names.append(skill.name)

        return registered_skill_names

    def _available_name(self, server_name: str, tool_name: str) -> str:
        """Pick a registry name for a tool, prefixing or numbering past collisions.

        Collision detection runs on the *sanitized* name, which is what actually
        lands in the registry -- checking the raw MCP name missed every clash
        between tools whose names differ only in characters the sanitizer strips.
        """
        clean_tool = sanitize_tool_name(tool_name)
        if self.registry.get(clean_tool) is None:
            return clean_tool

        prefixed = sanitize_tool_name(f"{sanitize_tool_name(server_name)}_{clean_tool}")
        if self.registry.get(prefixed) is None:
            return prefixed

        for n in range(2, 100):
            candidate = f"{prefixed[: 64 - len(str(n)) - 1]}_{n}"
            if self.registry.get(candidate) is None:
                return candidate
        return prefixed

    async def disconnect_server(self, server_name: str) -> None:
        """Disconnect transport and unregister its skills."""
        for skill_name in self._server_skills.pop(server_name, []):
            skill = self.registry.get(skill_name)
            # Only delete skills this server actually owns. Belt and braces
            # against ever handing another server's tool to the reaper again.
            if isinstance(skill, MCPSkill) and skill.server_name == server_name:
                self.registry.delete(skill_name)

        transport = self._transports.pop(server_name, None)
        if transport:
            try:
                await transport.close()
            except Exception:
                pass

    async def call_tool(
        self, server_name: str, tool_name: str, arguments: dict[str, Any]
    ) -> str:
        """Dispatch a tools/call request to the corresponding MCP server."""
        transport = self._transports.get(server_name)
        if not transport or not transport.is_connected:
            server = self.store.get_mcp_server_by_name(server_name)
            if not server:
                return f"MCP server '{server_name}' is not registered."
            try:
                await self.sync_server(server)
                transport = self._transports.get(server_name)
            except Exception as exc:
                return f"Could not connect to MCP server '{server_name}': {exc}"

        if not transport:
            return f"MCP server '{server_name}' is not available."

        params = {"name": tool_name, "arguments": arguments}
        try:
            res = await transport.send_request("tools/call", params, timeout=45.0)
        except MCPTimeoutError:
            return f"Tool '{tool_name}' on server '{server_name}' timed out after 45s."
        except MCPProtocolError as exc:
            return f"Tool '{tool_name}' error: {exc.message}"
        except Exception as exc:
            return f"Tool '{tool_name}' failed: {exc}"

        return self._format_result(res)

    @staticmethod
    def _format_result(res: Any) -> str:
        """Flatten an MCP content array into the text a model can read."""
        if not isinstance(res, dict):
            return str(res) if res is not None else ""

        contents = res.get("content", [])
        is_error = res.get("isError", False)

        parts: list[str] = []
        if isinstance(contents, list):
            for c in contents:
                if isinstance(c, dict):
                    kind = c.get("type")
                    if kind == "text":
                        parts.append(str(c.get("text", "")))
                    elif kind == "image":
                        parts.append(f"[MCP Image: {c.get('mimeType', 'image')}]")
                    elif kind == "resource":
                        parts.append(f"[MCP Resource: {json.dumps(c.get('resource', {}))}]")
                    else:
                        parts.append(str(c))
                else:
                    parts.append(str(c))
        elif isinstance(contents, str):
            parts.append(contents)

        # structuredContent is the 2025-06-18 way to return data; a server that
        # sends only that would otherwise come back as the whole raw envelope.
        if not parts and isinstance(res.get("structuredContent"), (dict, list)):
            parts.append(json.dumps(res["structuredContent"], ensure_ascii=False))

        output = "\n\n".join(p for p in parts if p) or json.dumps(res, ensure_ascii=False)
        if is_error and not output.lower().startswith("error"):
            output = f"Error: {output}"
        return output

    def _create_transport(self, server: StoredMCPServer) -> BaseMCPTransport:
        transport_type = (server.transport or "").lower()
        if transport_type == "stdio":
            if not server.command:
                raise MCPError(
                    f"Server '{server.name}' requires a command for stdio transport"
                )
            return StdioTransport(
                command=server.command,
                args=server.parsed_args(),
                env=server.parsed_env(),
                cwd=server.cwd,
            )
        if transport_type in ("sse", "http", "streamable-http"):
            if not server.url:
                raise MCPError(
                    f"Server '{server.name}' requires a URL for {transport_type} transport"
                )
            return HttpSseTransport(
                url=server.url,
                headers=server.parsed_headers(),
                # "http"/"streamable-http" mean Streamable HTTP even when the
                # path happens to end in /sse; only "sse" forces the legacy shape.
                mode="sse" if transport_type == "sse" else "streamable",
            )
        raise MCPError(
            f"Unsupported transport '{server.transport}' for server '{server.name}'. "
            f"Supported: {', '.join(SUPPORTED_TRANSPORTS)}."
        )

    async def aclose(self) -> None:
        """Close all active MCP transports on server shutdown."""
        for server_name in list(self._transports.keys()):
            await self.disconnect_server(server_name)
