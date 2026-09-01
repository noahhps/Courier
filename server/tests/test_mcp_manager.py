"""Tests for MCPManager, transports, and G Suite / Figma presets."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
import pytest

from app.db import Database
from app.mcp import (
    BaseMCPTransport,
    MCPManager,
    MCPProtocolError,
    PRESETS,
    get_preset,
    list_presets,
)
from app.mcp.presets import missing_inputs, resolve_preset
from app.mcp.transports import HttpSseTransport
from app.skills.registry import Registry
from app.store import Store


class MockMCPTransport(BaseMCPTransport):
    """Mock MCP transport for testing handshake, tools/list, and tools/call."""

    def __init__(self, tools: list[dict[str, Any]] | None = None) -> None:
        self.tools = tools or [
            {
                "name": "calculate",
                "description": "Add two numbers",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "a": {"type": "number"},
                        "b": {"type": "number"},
                    },
                    "required": ["a", "b"],
                },
            }
        ]
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True

    async def send_request(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0
    ) -> Any:
        if method == "initialize":
            return {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "MockServer", "version": "1.0"},
            }
        elif method == "tools/list":
            return {"tools": self.tools}
        elif method == "tools/call":
            name = (params or {}).get("name")
            args = (params or {}).get("arguments", {})
            if name == "calculate":
                res = args.get("a", 0) + args.get("b", 0)
                return {"content": [{"type": "text", "text": str(res)}]}
            elif name == "error_tool":
                return {"content": [{"type": "text", "text": "Something broke"}], "isError": True}
            raise MCPProtocolError(-32601, f"Method {name} not found")
        raise MCPProtocolError(-32601, f"Unknown method {method}")

    async def send_notification(
        self, method: str, params: dict[str, Any] | None = None
    ) -> None:
        pass

    async def close(self) -> None:
        self._connected = False


@pytest.fixture
def manager(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    store = Store(db)
    registry = Registry()
    mgr = MCPManager(store, registry)
    return mgr, store, registry


# -- Presets ------------------------------------------------------------------


def test_presets_exist():
    preset_ids = {p["id"] for p in list_presets()}
    for expected in (
        "figma", "figma_api", "google_workspace", "google_calendar", "github",
        "deepwiki", "context7", "exa", "huggingface", "firecrawl", "playwright",
        "filesystem", "sequential_thinking", "remote_bridge",
    ):
        assert expected in preset_ids

    workspace = get_preset("google_workspace")
    assert workspace is not None
    assert "google-workspace" in workspace["args"][1]


def test_there_is_no_separate_gmail_preset():
    """It ran the same package as google_workspace, so both together doubled every tool."""
    assert get_preset("gmail") is None
    assert "gmail" not in {p["id"] for p in list_presets()}


def test_every_preset_declares_where_its_logo_lives():
    for preset in list_presets():
        assert preset.get("homepage"), f"{preset['id']} has no homepage"


def test_optional_credentials_drop_their_whole_header():
    """`Authorization: Bearer ` reads as a malformed key, not as no key."""
    context7 = get_preset("context7")
    assert resolve_preset(context7, {})["headers"] == {}
    assert resolve_preset(context7, {"CONTEXT7_API_KEY": "k"})["headers"] == {
        "Authorization": "Bearer k"
    }


def test_figma_preset_points_at_the_dev_mode_server():
    """The desktop app serves Streamable HTTP on 3845/mcp, not SSE on 3000."""
    figma = get_preset("figma")
    assert figma is not None
    assert figma["transport"] == "http"
    assert figma["url"] == "http://127.0.0.1:3845/mcp"
    # It reads the running app's selection, so there is no token to prompt for.
    assert figma.get("inputs") == []


def test_figma_api_preset_runs_in_stdio_mode():
    """Without --stdio the package starts an HTTP server and never answers on stdin."""
    figma = get_preset("figma_api")
    assert figma is not None
    assert figma["transport"] == "stdio"
    assert "--stdio" in figma["args"]
    assert figma["env"]["FIGMA_API_KEY"] == "${FIGMA_API_KEY}"
    assert [i["key"] for i in figma["inputs"]] == ["FIGMA_API_KEY"]


def test_no_preset_ships_an_unexpanded_placeholder_past_resolve():
    """Every ${NAME} in a preset must be one `resolve_preset` can fill in."""
    for preset in list_presets():
        resolved = resolve_preset(preset, {k: "x" for k in
                                           [i["key"] for i in preset.get("inputs") or []]})
        blob = json.dumps({k: resolved.get(k) for k in ("args", "env", "headers", "url")})
        assert "${" not in blob, f"{preset['id']} left a placeholder unresolved: {blob}"


def test_placeholder_substitution_reaches_args_env_and_headers():
    preset = {
        "args": ["--key", "${TOK}"],
        "env": {"TOK": "${TOK}"},
        "headers": {"Authorization": "Bearer ${TOK}"},
        "inputs": [{"key": "TOK", "required": True}],
    }
    assert missing_inputs(preset, {}) == ["TOK"]
    assert missing_inputs(preset, {"TOK": "abc"}) == []

    resolved = resolve_preset(preset, {"TOK": "abc"})
    assert resolved["args"] == ["--key", "abc"]
    assert resolved["env"] == {"TOK": "abc"}
    assert resolved["headers"] == {"Authorization": "Bearer abc"}


# -- MCPManager Sync & Execution ----------------------------------------------


@pytest.mark.anyio
async def test_mcp_manager_sync_and_call(manager):
    mgr, store, registry = manager

    server = store.add_mcp_server(
        name="math",
        transport="stdio",
        command="mock_cmd",
        enabled=True,
    )

    # Inject mock transport for the server
    mock_transport = MockMCPTransport()
    mgr._create_transport = lambda s: mock_transport  # type: ignore

    tools = await mgr.sync_server(server)
    assert tools == ["calculate"]
    assert registry.get("calculate") is not None

    # Call tool through manager
    result = await mgr.call_tool("math", "calculate", {"a": 5, "b": 10})
    assert result == "15"

    # Call tool via Registry Skill
    skill = registry.get("calculate")
    assert skill is not None
    res = await skill.use(a=20, b=22)
    assert res == "42"

    # Cleanup
    await mgr.disconnect_server("math")
    assert registry.get("calculate") is None
    assert not mock_transport.is_connected


@pytest.mark.anyio
async def test_mcp_manager_error_handling(manager):
    mgr, store, registry = manager

    server = store.add_mcp_server(
        name="err_server",
        transport="stdio",
        command="mock_cmd",
        enabled=True,
    )

    mock_transport = MockMCPTransport(
        tools=[{"name": "error_tool", "description": "Fails", "inputSchema": {}}]
    )
    mgr._create_transport = lambda s: mock_transport  # type: ignore

    await mgr.sync_server(server)

    result = await mgr.call_tool("err_server", "error_tool", {})
    assert "Error: Something broke" in result


@pytest.mark.anyio
async def test_colliding_tool_names_do_not_reap_each_other(manager):
    """Two servers exposing the same tool name must not delete each other's skill."""
    mgr, store, registry = manager

    tool = [{"name": "get_file", "description": "Read a file", "inputSchema": {}}]
    first = store.add_mcp_server(name="alpha", transport="stdio", command="a", enabled=True)
    second = store.add_mcp_server(name="beta", transport="stdio", command="b", enabled=True)

    mgr._create_transport = lambda s: MockMCPTransport(tools=tool)  # type: ignore
    assert await mgr.sync_server(first) == ["get_file"]
    # The second server's copy is prefixed rather than dropped on the floor.
    assert await mgr.sync_server(second) == ["beta_get_file"]

    await mgr.disconnect_server("beta")
    # This is the regression: alpha's tool used to vanish with beta's.
    assert registry.get("get_file") is not None
    assert registry.get("beta_get_file") is None


@pytest.mark.anyio
async def test_failed_sync_closes_the_transport_it_opened(manager):
    """A sync that dies mid-handshake must not leave the subprocess it spawned running."""
    mgr, store, _ = manager
    server = store.add_mcp_server(name="broken", transport="stdio", command="x", enabled=True)

    class ExplodingTransport(MockMCPTransport):
        async def send_request(self, method, params=None, *, timeout=30.0):
            if method == "tools/list":
                raise MCPProtocolError(-32000, "server fell over")
            return await super().send_request(method, params, timeout=timeout)

    doomed = ExplodingTransport()
    mgr._create_transport = lambda s: doomed  # type: ignore

    with pytest.raises(MCPProtocolError):
        await mgr.sync_server(server)

    assert not doomed.is_connected
    assert mgr.last_error("broken") is not None
    assert not mgr.is_server_connected("broken")


@pytest.mark.anyio
async def test_server_without_tools_capability_is_not_a_failure(manager):
    """Resources-only servers answer tools/list with -32601; that is not a sync error."""
    mgr, store, _ = manager
    server = store.add_mcp_server(name="res_only", transport="stdio", command="x", enabled=True)

    class ResourcesOnly(MockMCPTransport):
        async def send_request(self, method, params=None, *, timeout=30.0):
            if method == "initialize":
                return {"protocolVersion": "2025-06-18", "capabilities": {"resources": {}}}
            raise MCPProtocolError(-32601, "Method not found")

    mgr._create_transport = lambda s: ResourcesOnly()  # type: ignore
    assert await mgr.sync_server(server) == []
    assert mgr.is_server_connected("res_only")


def test_websocket_transport_raises_a_clear_error(manager):
    from app.mcp.protocol import MCPError

    mgr, store, _ = manager
    server = store.add_mcp_server(
        name="ws", transport="websocket", url="ws://localhost:9/x", enabled=True
    )
    with pytest.raises(MCPError, match="Unsupported transport"):
        mgr._create_transport(server)


def test_streamable_transport_chosen_for_http_even_on_an_sse_path(manager):
    mgr, store, _ = manager
    server = store.add_mcp_server(
        name="fig", transport="http", url="http://127.0.0.1:3845/mcp", enabled=True
    )
    transport = mgr._create_transport(server)
    assert isinstance(transport, HttpSseTransport)
    assert transport.mode == "streamable"
