"""Tests for the HTTP transports -- Streamable HTTP in particular.

The Figma Dev Mode server is a Streamable HTTP server, so these cover the three
things that stopped Courier talking to it: the Accept header, the session id,
and reading a JSON-RPC response out of an SSE-formatted POST body.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.mcp.protocol import MCPProtocolError, MCPTransportError
from app.mcp.transports import HttpSseTransport


def sse(payload: dict) -> str:
    return f"event: message\ndata: {json.dumps(payload)}\n\n"


class FakeServer:
    """A minimal Streamable HTTP MCP server, recording what the client sent."""

    def __init__(self, *, respond_with_sse: bool = True, require_session: bool = True) -> None:
        self.respond_with_sse = respond_with_sse
        self.require_session = require_session
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content or b"{}")
        method = body.get("method")

        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "FakeFigma", "version": "1.0"},
            }
            return self._reply(body["id"], result, headers={"Mcp-Session-Id": "sess-123"})

        # Everything after initialize must carry the session the server issued.
        if self.require_session and request.headers.get("mcp-session-id") != "sess-123":
            return httpx.Response(400, json={"error": "Missing session ID"})

        if "id" not in body:  # a notification
            return httpx.Response(202)

        if method == "tools/list":
            return self._reply(
                body["id"],
                {"tools": [{"name": "get_code", "description": "Generate code", "inputSchema": {}}]},
            )
        if method == "tools/call":
            return self._reply(body["id"], {"content": [{"type": "text", "text": "<Button />"}]})
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": body["id"],
                  "error": {"code": -32601, "message": f"Unknown {method}"}},
        )

    def _reply(self, req_id, result, headers=None) -> httpx.Response:
        msg = {"jsonrpc": "2.0", "id": req_id, "result": result}
        if self.respond_with_sse:
            return httpx.Response(
                200,
                text=sse(msg),
                headers={"Content-Type": "text/event-stream", **(headers or {})},
            )
        return httpx.Response(200, json=msg, headers=headers or {})


async def wire(transport: HttpSseTransport, server: FakeServer) -> None:
    await transport.connect()
    transport._client = httpx.AsyncClient(transport=httpx.MockTransport(server.handler))


# -- mode selection -----------------------------------------------------------


def test_mode_is_read_from_the_path_not_the_whole_url():
    # The old check was `"sse" in url`, which read a hostname as a protocol.
    assert HttpSseTransport("http://127.0.0.1:3845/mcp").mode == "streamable"
    assert HttpSseTransport("https://sse-gateway.example.com/mcp").mode == "streamable"
    assert HttpSseTransport("https://example.com/mcp?token=assets").mode == "streamable"
    assert HttpSseTransport("http://127.0.0.1:3845/sse").mode == "sse"


def test_blank_header_values_are_dropped():
    """An unfilled token field must mean no header, not `Authorization: Bearer `."""
    t = HttpSseTransport("http://x/mcp", headers={"Authorization": "", "X-Keep": "y"})
    assert t.headers == {"X-Keep": "y"}


def test_sse_bodies_are_parsed_into_jsonrpc_messages():
    body = ": keep-alive\nevent: message\ndata: {\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{}}\n\n"
    assert HttpSseTransport._messages_from_sse(body) == [
        {"jsonrpc": "2.0", "id": 1, "result": {}}
    ]


# -- streamable HTTP round trip ----------------------------------------------


@pytest.mark.anyio
async def test_streamable_http_handshake_and_call():
    server = FakeServer()
    t = HttpSseTransport("http://127.0.0.1:3845/mcp")
    await wire(t, server)

    init = await t.send_request("initialize", {"protocolVersion": "2025-06-18"})
    assert init["serverInfo"]["name"] == "FakeFigma"

    await t.send_notification("notifications/initialized")
    tools = await t.send_request("tools/list", {})
    assert tools["tools"][0]["name"] == "get_code"

    call = await t.send_request("tools/call", {"name": "get_code", "arguments": {}})
    assert call["content"][0]["text"] == "<Button />"
    await t.close()


@pytest.mark.anyio
async def test_session_id_is_captured_and_echoed():
    server = FakeServer(require_session=True)
    t = HttpSseTransport("http://127.0.0.1:3845/mcp")
    await wire(t, server)

    await t.send_request("initialize", {})
    assert t._session_id == "sess-123"
    # Would 400 without the header; the point of the test is that it does not.
    await t.send_request("tools/list", {})
    assert server.requests[-1].headers["mcp-session-id"] == "sess-123"
    await t.close()


@pytest.mark.anyio
async def test_accept_header_offers_both_content_types():
    server = FakeServer()
    t = HttpSseTransport("http://127.0.0.1:3845/mcp")
    await wire(t, server)
    await t.send_request("initialize", {})

    accept = server.requests[0].headers["accept"]
    assert "application/json" in accept and "text/event-stream" in accept
    await t.close()


@pytest.mark.anyio
async def test_protocol_version_header_follows_the_negotiated_version():
    server = FakeServer()
    t = HttpSseTransport("http://127.0.0.1:3845/mcp")
    await wire(t, server)

    await t.send_request("initialize", {})
    await t.send_request("tools/list", {})
    assert server.requests[-1].headers["mcp-protocol-version"] == "2025-06-18"
    await t.close()


@pytest.mark.anyio
async def test_plain_json_responses_still_work():
    server = FakeServer(respond_with_sse=False)
    t = HttpSseTransport("http://127.0.0.1:3845/mcp")
    await wire(t, server)
    init = await t.send_request("initialize", {})
    assert init["protocolVersion"] == "2025-06-18"
    await t.close()


@pytest.mark.anyio
async def test_jsonrpc_errors_surface_as_protocol_errors():
    server = FakeServer()
    t = HttpSseTransport("http://127.0.0.1:3845/mcp")
    await wire(t, server)
    await t.send_request("initialize", {})

    with pytest.raises(MCPProtocolError) as exc:
        await t.send_request("resources/list", {})
    assert exc.value.code == -32601
    await t.close()


@pytest.mark.anyio
async def test_http_errors_name_the_status_and_the_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Invalid token")

    t = HttpSseTransport("https://example.com/mcp")
    await t.connect()
    t._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    with pytest.raises(MCPTransportError, match="401"):
        await t.send_request("initialize", {})
    await t.close()


@pytest.mark.anyio
async def test_failed_notification_is_reported_not_swallowed():
    """A dropped notifications/initialized turns into unexplainable later failures."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    t = HttpSseTransport("https://example.com/mcp")
    await t.connect()
    t._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    with pytest.raises(MCPTransportError):
        await t.send_notification("notifications/initialized")
    await t.close()


@pytest.mark.anyio
async def test_expired_session_is_not_mistaken_for_a_legacy_server():
    """Once a session exists, a 404 means expiry -- not 'try the old protocol'."""
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        body = json.loads(request.content or b"{}")
        if body.get("method") == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": body["id"],
                      "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}},
                headers={"Mcp-Session-Id": "sess-abc"},
            )
        return httpx.Response(404, text="Session not found")

    t = HttpSseTransport("https://example.com/mcp")
    await t.connect()
    t._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await t.send_request("initialize", {})

    with pytest.raises(MCPTransportError, match="session expired"):
        await t.send_request("tools/list", {})
    assert t.mode == "streamable"  # did not flip to SSE
    assert t._session_id is None  # cleared, so the next sync re-initializes
    await t.close()


@pytest.mark.anyio
async def test_405_before_a_session_falls_back_to_legacy_sse():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                text="event: endpoint\ndata: /messages?id=1\n\n",
                headers={"Content-Type": "text/event-stream"},
            )
        if str(request.url).endswith("/mcp"):
            return httpx.Response(405, text="Method Not Allowed")
        body = json.loads(request.content or b"{}")
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": body["id"], "result": {"ok": True}},
            headers={"Content-Type": "application/json"},
        )

    t = HttpSseTransport("https://example.com/mcp")
    await t.connect()
    t._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    assert await t.send_request("initialize", {}) == {"ok": True}
    assert t.mode == "sse"
    await t.close()
