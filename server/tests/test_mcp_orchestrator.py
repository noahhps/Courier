"""End-to-end test verifying MCP skills execution in the Orchestrator turn loop."""

from __future__ import annotations

from pathlib import Path
import pytest

from app.db import Database
from app.mcp import BaseMCPTransport, MCPManager
from app.orchestrator import Orchestrator
from app.providers.base import Chunk, Message, ModelProvider, ToolCall
from app.providers.router import ProviderRouter
from app.skills.registry import Registry
from app.store import Store


class MockProvider:
    def __init__(self):
        self.name = "mock_provider"
        self.model = "mock_model"
        self.call_count = 0

    async def stream(self, messages, *, think=None, tools=None):
        self.call_count += 1
        if self.call_count == 1:
            # First round: ask to call the MCP tool
            yield Chunk(
                text="Calling Gmail...",
                done=True,
                tool_calls=(
                    ToolCall(
                        id="call_gmail_1",
                        name="search_emails",
                        arguments={"query": "is:unread"},
                    ),
                ),
            )
        else:
            # Second round: answer after receiving tool result
            yield Chunk(
                text="You have 1 unread email from Alice.",
                done=True,
            )

    async def embed(self, texts):
        return [[0.0] * 768 for _ in texts]

    async def health(self):
        return True


class MockGmailTransport(BaseMCPTransport):
    def __init__(self):
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True

    async def send_request(self, method: str, params=None, *, timeout=30.0):
        if method == "initialize":
            return {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}}
        elif method == "tools/list":
            return {
                "tools": [
                    {
                        "name": "search_emails",
                        "description": "Search inbox messages",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    }
                ]
            }
        elif method == "tools/call":
            return {
                "content": [
                    {"type": "text", "text": "Subject: Lunch meeting\nFrom: Alice"}
                ]
            }
        return {}

    async def send_notification(self, method: str, params=None) -> None:
        pass

    async def close(self) -> None:
        self._connected = False


@pytest.mark.anyio
async def test_mcp_skill_in_orchestrator_turn(tmp_path: Path):
    db = Database(tmp_path / "test_turn.db")
    store = Store(db)
    registry = Registry()
    mcp_mgr = MCPManager(store, registry)

    # Add Gmail MCP server in SQLite
    server = store.add_mcp_server(
        name="gmail",
        transport="stdio",
        command="mock_gmail",
        enabled=True,
    )

    # Inject mock transport
    mcp_mgr._create_transport = lambda s: MockGmailTransport()  # type: ignore
    await mcp_mgr.sync_server(server)

    assert registry.get("search_emails") is not None

    # Setup orchestrator with mock provider
    provider = MockProvider()
    router = ProviderRouter.__new__(ProviderRouter)
    router.local = provider
    router.cloud = provider

    async def mock_resolve(prefer=None):
        return type("Route", (), {"provider": provider, "reason": "local"})()

    router.resolve = mock_resolve
    router.invalidate_health = lambda: None

    settings = type(
        "MockSettings",
        (),
        {
            "system_preamble": "You are a helpful assistant.",
            "context_tokens": 8192,
            "reply_tokens": 1024,
            "ollama_think": "medium",
            "memory_max_facts": 20,
            "memory_fact_chars": 200,
        },
    )()

    orchestrator = Orchestrator(settings, store, router, registry)

    session = store.create_session()
    frames = []
    async for frame in orchestrator.run_turn(session["id"], "Check my unread emails"):
        frames.append(frame)

    frames_str = "".join(frames)
    assert "event: tool_call" in frames_str
    assert "search_emails" in frames_str
    assert "event: tool_result" in frames_str
    assert "Lunch meeting" in frames_str
    assert "You have 1 unread email from Alice." in frames_str

    # Verify message persistence in store
    messages = store.list_messages(session["id"])
    assistant_msg = next(m for m in messages if m.role == "assistant")
    assert assistant_msg.content == "Calling Gmail...You have 1 unread email from Alice."
    assert assistant_msg.to_dict()["skills"][0]["name"] == "search_emails"
    assert "Lunch meeting" in assistant_msg.to_dict()["skills"][0]["result"]

    await mcp_mgr.aclose()
